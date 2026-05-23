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
        
        # 引入并实例化高内聚网络消息发送器
        from .sender import MessageSender
        self.sender = MessageSender(self)
        
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
        from .scheduler import GatewayScheduler
        self.scheduler = GatewayScheduler(self)
        self.csma_backoff_seconds = float(os.getenv("QQ_CSMA_BACKOFF_SECONDS", "2.0"))

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
        try:
            await self.scheduler.start()
            
            # 主长连接维持循环
            while True:
                try:
                    await self._ws_loop()
                except Exception as e:
                    logger.error(f"WebSocket loop finished with error: {e}. Reconnecting in 3s...")
                    await asyncio.sleep(3)
        finally:
            await self.scheduler.stop()
            if self._http and not self._http.closed:
                await self._http.close()

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

    @property
    def limiter(self):
        """提供 limiter 属性代理以兼容既有 Mock 逻辑"""
        return self.sender.limiter

    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """兼容代理：委托给物理 sender 发送网络包。"""
        return await self.sender.send(msg_type, user_id, group_id, text, skip_delay)

    async def _send_chunk(self, msg_type: str, user_id: str, group_id: str, text: str):
        """兼容代理：委托给物理 sender 拆分发送文本块。"""
        return await self.sender.send_chunk(msg_type, user_id, group_id, text)

    def _natural_delay(self, text: str) -> float:
        """根据文本长度自然计算发送间隔（打字延迟已物理清退，默认为 0.0）"""
        return 0.0

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

    async def _process_podcast_generation_async(self, session_key: str, topic: str, admin_id: str):
        """夜间播客异步生成转发代理"""
        return await self.scheduler._process_podcast_generation_async(session_key, topic, admin_id)



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
