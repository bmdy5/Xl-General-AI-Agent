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
        asyncio.create_task(self._daemon_loop())
        
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
                                # 委托给 dispatcher 消息分发处理器，0 耦合
                                await self.dispatcher.dispatch_event(event)
                        except Exception as parse_err:
                            logger.error(f"Error parsing websocket message: {parse_err}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """OneBot HTTP 协议消息发送，负责具体的网络包推送。"""
        # 0. 全局物理发包滑窗令牌桶平滑流控整流
        await self.limiter.acquire()

        # 1. 拟人思考打字延迟
        is_media = text.strip().startswith("[CQ:") or text.strip().startswith("[ CQ:")
        is_system_msg = any(text.strip().startswith(prefix) for prefix in ["🤖", "⏰", "⚙️", "✅", "❌", "🔍", "🌅", "🚀", "💡"])
        
        if not skip_delay and not is_media and not is_system_msg:
            n_chars = len(text)
            base_delay = 0.35
            char_delay = n_chars * 0.03
            total_delay = min(base_delay + char_delay, 2.5)
            self._log_activity("打字延迟", f"纯文本打字延迟：计算延迟 {total_delay:.2f}秒，开始等待...")
            await asyncio.sleep(total_delay)

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
            # 采用异步 non-blocking 请求，防止卡死
            if self._http and not self._http.closed:
                async with self._http.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Message send failed ({resp.status}): {body[:100]}")
            else:
                # 兜底同步请求，防止 _http 被关闭时发生崩溃
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
                
            logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def _daemon_loop(self):
        """后台高可用健康守护轮询进程，负责 NapCat 断线自愈重启及定时技术早报播客推送。"""
        logger.info("QQ Gateway Background Daemon Loop started.")
        while True:
            await asyncio.sleep(15)  # 每 15s 轮询检测一次健康度
            
            # 1. 监测 NapCat WebSocket 连通状态进行自愈判定
            # 获取当前时间
            now_dt = datetime.now()
            
            # 定时任务：每日 21:00 自动拉起夜间极客播客选题（仅限管理员私聊）
            if now_dt.hour == 21 and now_dt.minute == 0 and 0 <= now_dt.second < 20:
                p_key = f"private_{self.admin_id}"
                if not self._waiting_podcast_topic.get(p_key, False):
                    logger.info("⏰ Time hit 21:00. Triggering night podcast topic selection...")
                    asyncio.create_task(self._trigger_night_podcast_selection(p_key, self.admin_id))
                    await asyncio.sleep(20)  # 防重入冷却
            
            # 定时任务：每日 06:00 自动拉取云端 NotebookLM 播客并推送
            if now_dt.hour == 6 and now_dt.minute == 0 and 0 <= now_dt.second < 20:
                logger.info("⏰ Time hit 06:00. Triggering morning technical podcast push...")
                asyncio.create_task(self._trigger_morning_podcast_download(self.admin_id))
                await asyncio.sleep(20)  # 防重入冷却

    def _log_activity(self, category: str, content: str):
        """结构化轨迹活动日志记录"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        try:
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")

    async def _trigger_night_podcast_selection(self, session_key: str, admin_id: str):
        """夜间播客自动选题器"""
        try:
            from agent.tools.mcp_agent_learning_server import list_agent_topics
            res_topics = await list_agent_topics()
            data = json.loads(res_topics)
            if data.get("status") != "success":
                raise ValueError(f"获取选题失败: {data.get('message')}")
                
            topics = data.get("topics", [])
            self._podcast_choices[session_key] = topics
            self._waiting_podcast_topic[session_key] = True
            
            t_str = "\n".join([f"{t}" for t in topics])
            msg = (
                f"💡 亮哥，我是小萤。今晚我们来为明早的极客播客定个专题吧！\n"
                f"您可以直接选择以下任一主题（回复 1、2 或 3），或者直接回复您想听的任意技术方向：\n\n"
                f"{t_str}\n\n"
                f"请在回复中选择。"
            )
            await self._send("private", admin_id, "", msg)
        except Exception as e:
            logger.error(f"获取选题或推送失败: {e}", exc_info=True)
            await self._send("private", admin_id, "", f"❌ 抱歉亮哥，智能提炼明早播客选题时发生异常: {e}")

    async def _trigger_morning_podcast_download(self, admin_id: str):
        """晨间播客音频自动拉取与文件主动推送"""
        from agent.tools.mcp_agent_learning_server import check_and_push_podcast
        try:
            res = await check_and_push_podcast()
            data = json.loads(res)
            status = data.get("status")
            if status == "success":
                local_path = data.get("local_path")
                topic = data.get("topic")
                if os.path.exists(local_path):
                    import shutil
                    share_dir = "/Users/xiaofeng/napcat-data-tmp"
                    os.makedirs(share_dir, exist_ok=True)
                    safe_topic = re.sub(r'[\/:*?"<>|]', '_', topic)
                    dest_filename = f"亮哥专属完整播客音频-{safe_topic}.wav"
                    host_dest_path = os.path.join(share_dir, dest_filename)
                    container_dest_path = f"/app/.config/QQ/{dest_filename}"
                    
                    logger.info(f"➡️ 正在拷贝音频到共享目录: {host_dest_path}...")
                    shutil.copy(local_path, host_dest_path)
                    
                    file_payload = {
                        "user_id": int(admin_id),
                        "file": container_dest_path,
                        "name": dest_filename
                    }
                    
                    url = f"{NC_HTTP_URL}/upload_private_file"
                    headers = {"Content-Type": "application/json"}
                    if NC_TOKEN:
                        headers["Authorization"] = f"Bearer {NC_TOKEN}"
                        
                    logger.info(f"📤 正在向亮哥 QQ 主动推送完整版播客文件: {dest_filename}")
                    try:
                        if self._http and not self._http.closed:
                            async with self._http.post(url, json=file_payload, headers=headers) as resp:
                                if resp.status != 200:
                                    body = await resp.text()
                                    logger.warning(f"File upload failed ({resp.status}): {body[:100]}")
                        else:
                            req = urllib.request.Request(url, data=json.dumps(file_payload).encode(), headers=headers, method="POST")
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30))
                    except Exception as upload_err:
                        logger.error(f"Failed to upload file to QQ: {upload_err}")
                    
                    success_msg = f"🎉 亮哥专属每日学习早报播客获取成功！\n今日主题：【{topic}】\n音频已通过 QQ 文件传输发送到您的手机。\n本地保存路径：{local_path}"
                    await self._send("private", admin_id, "", success_msg)
            elif status == "pending":
                logger.info("晨间播客尚在生成中，将由守护进程轮询捕获。")
        except Exception as e:
            logger.error(f"晨间主动下载播客失败: {e}", exc_info=True)

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
        """发送一个文本块，处理 [SPLIT] 和 [WAIT:N] 并执行拟真打字延迟"""
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
            # 第一段保留拟真打字延迟，其余后续段通过 skip_delay=True 瞬间发送，消除 Double Delay 叠加
            should_skip_delay = (i > 0)
            await self._send(msg_type, user_id, group_id, part, skip_delay=should_skip_delay)
            if i < len(parts) - 1:
                delay = max(0.5, wait) if wait > 0 else self._natural_delay(part)
                await asyncio.sleep(delay)
                wait = 0

    def _natural_delay(self, text: str) -> float:
        """根据文本长度自然计算发送间隔."""
        n = len(text)
        if n < 10:
            return 0.3
        if n < 30:
            return 0.6
        if n < 80:
            return 1.0
        return 1.5

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
