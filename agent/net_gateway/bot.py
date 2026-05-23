import asyncio
import json
import logging
import os
import re
import urllib.request
import time
from datetime import datetime, timezone
from typing import Optional
import aiohttp

from .context import GatewayContext
from .dispatcher import MessageDispatcher

logger = logging.getLogger("net_gateway.bot")

NC_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000

class QQGateway:
    """精简后的 QQ 通信网关，只负责长连接维持、自愈监控、早晚间电台轮询。"""
    
    def __init__(self, agent_factory):
        # 1. 初始化统一状态上下文总线，并将 admin_id 共享过去
        admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
        self.context = GatewayContext(admin_id=admin_id, factory=agent_factory)
        
        # 2. 为 context 动态绑定发包回调，使用 lambda 动态路由以完美支持单元测试对底层方法的 Mock 劫持
        self.context.send_handler = lambda *args, **kwargs: self._send(*args, **kwargs)
        self.context.send_chunk_handler = lambda *args, **kwargs: self._send_chunk(*args, **kwargs)
        
        # 引入并实例化高内聚解耦组件，建立对本网关的反向指针
        self.dispatcher = MessageDispatcher(self.context, bot=self)
        self._handle = self.dispatcher.dispatch_event
        
        # 维持底层属性，保证测试用例 mock 对象的绝对向下兼容
        self._factory = agent_factory
        self._agents = self.context._agents
        self._last_voice_time = self.context._last_voice_time
        self._last_receive_time = self.context._last_receive_time
        
        # 通信底层状态与缓存
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, object] = {}
        self._reconnect_failures: int = 0
        self._last_offline_alert: float = 0.0
        self._activity_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent_activity.log"
        self._bypass_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/coworker_activity.log"
        self._current_tasks: dict[str, asyncio.Task] = {}
        self._message_queues: dict[str, list[tuple[dict, str]]] = {}
        
        # 配置同步
        self.admin_id = admin_id
        self.csma_backoff_seconds = float(os.getenv("QQ_CSMA_BACKOFF_SECONDS", "2.0"))
        
        # 初始化全局发包平滑流控令牌桶限流器（默认最大并发爆发5包，每1.5秒填充1包）
        from .bus import TokenBucketLimiter
        capacity = float(os.getenv("QQ_LIMITER_CAPACITY", "5.0"))
        refill_rate = float(os.getenv("QQ_LIMITER_REFILL_RATE", "0.67"))
        self.limiter = TokenBucketLimiter(capacity=capacity, refill_rate=refill_rate)

    async def run(self):
        """网关启动主协程，启动 WebSocket 长连接并挂载守护协程。"""
        # 为 context 动态绑定语音合成发送回调，使用 lambda 动态路由以完美支持单元测试对 _send_voice 的 Mock
        from .tts import send_voice
        self.context.send_voice_handler = lambda *args, **kwargs: (
            self._send_voice(*args, **kwargs) if hasattr(self, "_send_voice") else send_voice(self.context, *args, **kwargs)
        )
        
        logger.info(f"MyAgent — QQ Gateway 模式 (100% 模块化)")
        logger.info(f"WebSocket: {NC_WS_URL}")
        logger.info(f"HTTP API:  {NC_HTTP_URL}")
        
        self._http = aiohttp.ClientSession()
        from .scheduler import GatewayScheduler
        self.scheduler = GatewayScheduler(self)
        await self.scheduler.start()
        
        # 主长连接维持循环
        while True:
            try:
                await self._ws_loop()
            except Exception as e:
                logger.error(f"WebSocket loop finished with error: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def _ws_loop(self):
        """WebSocket 收包内循环。"""
        headers = {}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"
            
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(NC_WS_URL, headers=headers) as ws:
                logger.info(f"QQ Gateway connected to NapCat: {NC_WS_URL}")
                self._reconnect_failures = 0  # 成功连接，重置断连计数
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(msg.data)
                            # 过滤并仅处理聊天消息，忽略其他噪音事件
                            if event.get("post_type") == "message" and event.get("message_type") in ("private", "group"):
                                # 委托给 dispatcher 消息分发处理器，并发非阻塞协程派发，实现 100% 极速并发消费
                                asyncio.create_task(self.dispatcher.dispatch_event(event))
                        except Exception as parse_err:
                            logger.error(f"Error parsing websocket message: {parse_err}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """OneBot HTTP 协议消息发送，负责具体的网络包推送。"""
        # 0. 全局物理发包滑窗令牌桶平滑流控整流
        await self.limiter.acquire()

        # 2. 文本净化（QQ 不支持 Markdown 粗斜体渲染，在此进行自动降解）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)

        # 3. 构造发送 payload
        payload = {}
        endpoint = ""
        if msg_type == "group" and group_id:
            endpoint = "/send_group_msg"
            payload = {"group_id": int(group_id), "message": text}
        else:
            endpoint = "/send_private_msg"
            payload = {"user_id": int(user_id), "message": text}

        url = f"{NC_HTTP_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        try:
            # 采用异步 non-blocking 请求，并设置 5.0 秒超时限制防卡死
            if self._http and not self._http.closed:
                timeout = aiohttp.ClientTimeout(total=5.0)
                async with self._http.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Message send failed ({resp.status}): {body[:100]}")
            else:
                # 兜底同步请求，防止 _http 被关闭时发生崩溃
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
                
            logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
            # 物理写入活动轨迹日志以实现输入与输出时间线完全融合
            session_key = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
            self._log_activity("Agent回复", f"小萤 ({session_key}): {text}", user_id=user_id)
        except Exception as e:
            logger.error(f"Send error: {e}")

    def _log_activity(self, category: str, content: str, user_id: str = None):
        """结构化轨迹活动日志记录，支持根据发言人身份将主流量与沙箱旁路流量物理隔离分流"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        
        # 物理路由判定：亮哥本人的轨迹打入主要日志，其他人的动作全数隔离归入旁路日志
        target_path = self._activity_log_path
        if user_id is not None:
            if str(user_id) != str(self.admin_id):
                target_path = self._bypass_log_path
                
        try:
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")

    # ── 兼容测试套件属性/方法代理代理 ──

    @property
    def _private_chat_paused(self) -> bool:
        return self.dispatcher._private_chat_paused

    @_private_chat_paused.setter
    def _private_chat_paused(self, value: bool):
        self.dispatcher._private_chat_paused = value

    @property
    def _fatigue_levels(self) -> dict:
        return self.dispatcher._fatigue_levels

    @_fatigue_levels.setter
    def _fatigue_levels(self, value: dict):
        self.dispatcher._fatigue_levels = value

    @property
    def _sleep_modes(self) -> dict:
        return self.dispatcher._sleep_modes

    @_sleep_modes.setter
    def _sleep_modes(self, value: dict):
        self.dispatcher._sleep_modes = value

    @property
    def _waiting_podcast_topic(self) -> dict:
        return self.dispatcher._waiting_podcast_topic

    @_waiting_podcast_topic.setter
    def _waiting_podcast_topic(self, value: dict):
        self.dispatcher._waiting_podcast_topic = value

    @property
    def _podcast_choices(self) -> dict:
        return self.dispatcher._podcast_choices

    @_podcast_choices.setter
    def _podcast_choices(self, value: dict):
        self.dispatcher._podcast_choices = value

    async def _send_chunk(self, msg_type: str, user_id: str, group_id: str, text: str):
        """发送一个文本块，处理 [SPLIT] 和 [WAIT:N]"""
        wait = 0
        def _extract_wait(t):
            nonlocal wait
            m = re.search(r'\[WAIT:([\d.]+)\]', t)
            if m:
                wait = max(wait, float(m.group(1)))
                t = re.sub(r'\[WAIT:[\d.]+\]', '', t)
            return t

        parts = text.split("[SPLIT]")
        for i, part in enumerate(parts):
            part = _extract_wait(part.strip())
            if not part:
                continue
            if len(part) > MAX_REPLY_CHARS:
                part = part[:MAX_REPLY_CHARS - 20] + "\n...(truncated)"
            
            await self._send(msg_type, user_id, group_id, part, skip_delay=True)
            if i < len(parts) - 1:
                if wait > 0:
                    await asyncio.sleep(wait)
                wait = 0

    def _natural_delay(self, text: str) -> float:
        """根据文本长度自然计算发送间隔（打字延迟已物理清退，默认为 0.0）"""
        return 0.0

    def _load_persona(self) -> tuple:
        """从资源文件加载画像属性，支持被 dispatcher 调用或在测试中被 mock."""
        import json
        from pathlib import Path
        _persona_name = "小萤"
        _user_address = "亮哥"
        try:
            pf = Path("/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/persona_profile.json")
            if pf.exists():
                prof = json.loads(pf.read_text(encoding="utf-8"))
                _persona_name = prof.get("name", "小萤")
                _user_address = prof.get("user_address", "亮哥")
        except Exception:
            pass
        return _persona_name, _user_address

    async def _generate_private_fatigue_announcement(self, user_id: str) -> str:
        """物理打盹宣告，优先委派给 dispatcher。测试用例对此方法进行了直接 mock."""
        if hasattr(self, "dispatcher") and hasattr(self.dispatcher, "_generate_private_fatigue_announcement"):
            return await self.dispatcher._generate_private_fatigue_announcement(user_id)
        return "小萤累了，去打盹半小时。"

    async def _generate_fatigue_announcement(self, group_id: str) -> str:
        """物理打盹宣告，优先委派给 dispatcher。"""
        if hasattr(self, "dispatcher") and hasattr(self.dispatcher, "_generate_fatigue_announcement"):
            return await self.dispatcher._generate_fatigue_announcement(group_id)
        return "唔……小萤用脑过度，去打盹半小时。"


def main():
    """主启动程序"""
    async def _main():
        from agent.core import Agent
        def factory(session_key):
            return Agent()
        bot = QQGateway(factory)
        await bot.run()
    asyncio.run(_main())

if __name__ == "__main__":
    main()
