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
        from agent.core.config import settings
        sec_cfg = settings.get("security") or {}
        admin_id = os.getenv("QQ_ADMIN_ID", sec_cfg.get("admin_id", "1705919142"))
        self.context = GatewayContext(admin_id=admin_id, factory=agent_factory)
        
        # 引入并实例化高内聚网络消息发送器
        from .sender import MessageSender
        self.sender = MessageSender(self)
        
        # 引入并实例化轨迹日志记录器
        from .logger import ActivityLogger
        self.activity_logger = ActivityLogger(self)
        
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
        self._http = None
        self._reconnect_failures = 0
        self._last_offline_alert = 0.0
        self._current_tasks = {}
        self._message_queues = {}
        
        # 配置同步
        self.admin_id = admin_id
        from .scheduler import GatewayScheduler
        self.scheduler = GatewayScheduler(self)
        self.csma_backoff_seconds = float(os.getenv("QQ_CSMA_BACKOFF_SECONDS", "2.0"))

    async def run(self, only_douyin: bool = False):
        """网关启动主协程，启动 WebSocket 长连接并挂载守护协程。"""
        # 为 context 动态绑定语音合成发送回调，使用 try...except 彻底消除 hasattr 反射探测
        from .tts import send_voice
        async def custom_send_voice(msg_type, user_id, group_id, text, style="知性", is_test=False):
            try:
                fn = getattr(self, "_send_voice")
                return await fn(msg_type, user_id, group_id, text, style, is_test)
            except AttributeError:
                return await send_voice(self.context, msg_type, user_id, group_id, text, style, is_test)
        self.context.send_voice_handler = custom_send_voice
        
        # ── 独立抖音进程分支 ──
        if only_douyin:
            logger.info("Initializing Standalone Douyin Gateway Process...")
            try:
                from .douyin_bot import douyin_gateway
                douyin_gateway.start(self.dispatcher)
                logger.info("Douyin Gateway standalone process daemon started successfully.")
                
                # 保持主协程常驻挂起
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                logger.info("Standalone Douyin Gateway cancelled.")
            finally:
                try:
                    from .douyin_bot import douyin_gateway
                    await douyin_gateway.stop()
                    logger.info("Douyin Gateway standalone stopped and resources cleared.")
                except Exception as douyin_stop_err:
                    logger.error(f"Error when stopping Standalone Douyin Gateway: {douyin_stop_err}")
            return

        logger.info(f"MyAgent — QQ Gateway 模式 (100% 模块化)")
        logger.info(f"WebSocket: {NC_WS_URL}")
        logger.info(f"HTTP API:  {NC_HTTP_URL}")
        
        # 启动大脑端事件 API 服务端，接收外部独立进程的消息上行投递
        asyncio.create_task(self._start_brain_server())
        
        self._http = aiohttp.ClientSession()
        try:
            # 1. 仅在明确启用时，在 QQ 进程中并行挂载并启动抖音私信网关
            if os.getenv("ENABLE_DOUYIN_IN_QQ", "false").lower() == "true":
                try:
                    from .douyin_bot import douyin_gateway
                    douyin_gateway.start(self.dispatcher)
                    logger.info("Douyin Gateway started concurrently in QQ background task.")
                except Exception as douyin_err:
                    logger.error(f"Failed to launch Douyin Gateway inside QQ Gateway: {douyin_err}")
            else:
                logger.info("Douyin Gateway is disabled inside QQ Gateway process (isolated).")

            await self.scheduler.start()
            
            # 主长连接维持循环
            while True:
                try:
                    await self._ws_loop()
                except Exception as e:
                    logger.error(f"WebSocket loop finished with error: {e}. Reconnecting in 3s...")
                    await asyncio.sleep(3)
        finally:
            # 2. 优雅停机并释放 CloakBrowser 浏览器沙箱资源
            if os.getenv("ENABLE_DOUYIN_IN_QQ", "false").lower() == "true":
                try:
                    from .douyin_bot import douyin_gateway
                    await douyin_gateway.stop()
                    logger.info("Douyin Gateway stopped and resources cleared.")
                except Exception as douyin_stop_err:
                    logger.error(f"Error when stopping Douyin Gateway: {douyin_stop_err}")

            if hasattr(self, "_brain_runner") and self._brain_runner:
                await self._brain_runner.cleanup()

            await self.scheduler.stop()
            if self._http and not self._http.closed:
                await self._http.close()

    async def _start_brain_server(self) -> None:
        """启动 8000 端口的大脑 HTTP 服务，接收外部微服务网关上行的 Event 和自愈二维码"""
        from aiohttp import web
        app = web.Application()
        app.router.add_post('/event', self._handle_brain_event)
        app.router.add_post('/report_qrcode', self._handle_brain_qrcode)
        
        self._brain_runner = web.AppRunner(app)
        await self._brain_runner.setup()
        port = int(os.getenv("BRAIN_PORT", "8000"))
        site = web.TCPSite(self._brain_runner, '127.0.0.1', port)
        await site.start()
        logger.info(f"Main Brain Event Server listening on http://127.0.0.1:{port}")

    async def _handle_brain_event(self, request) -> web.Response:
        """处理外部平台独立微网关上报的 OneBot 事件"""
        from aiohttp import web
        try:
            event = await request.json()
            asyncio.create_task(self.dispatcher.dispatch_event(event))
            return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json")
        except Exception as e:
            return web.Response(text=json.dumps({"status": "failed", "reason": str(e)}), content_type="application/json", status=500)

    async def _handle_brain_qrcode(self, request) -> web.Response:
        """接收外部独立网关进程发送的扫码自愈二维码，并委派大脑公共工具推送至亮哥 QQ"""
        from aiohttp import web
        try:
            data = await request.json()
            local_path = data.get("local_path")
            message = data.get("message")
            if local_path:
                from agent.tools.registry import registry
                image_tool = registry.get("send_image_to_qq")
                if image_tool:
                    async def _push():
                        async for res in image_tool.call({
                            "local_path": local_path,
                            "cos_key_suffix": "douyin_qrcode/login.png",
                            "message_prefix": message
                        }):
                            pass
                    asyncio.create_task(_push())
                    logger.info("Successfully scheduled QR code push task via send_image_to_qq.")
            return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json")
        except Exception as e:
            return web.Response(text=json.dumps({"status": "failed", "reason": str(e)}), content_type="application/json", status=500)

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
        """兼容代理：委托给物理 logger 记录轨迹日志。"""
        return self.activity_logger.log_activity(category, content, user_id)

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
            root_dir = Path(__file__).resolve().parents[2]
            pf = root_dir / "agent" / "resources" / "persona_profile.json"
            if pf.exists():
                prof = json.loads(pf.read_text(encoding="utf-8"))
                _persona_name = prof.get("name", "小萤")
                _user_address = prof.get("user_address", "亮哥")
        except Exception:
            pass
        return _persona_name, _user_address

    async def _generate_private_fatigue_announcement(self, user_id: str) -> str:
        """物理打盹宣告，优先委派给 dispatcher。测试用例对此方法进行了直接 mock."""
        try:
            return await self.dispatcher._generate_private_fatigue_announcement(user_id)
        except AttributeError:
            return "小萤累了，去打盹半小时。"

    async def _generate_fatigue_announcement(self, group_id: str) -> str:
        """物理打盹宣告，优先委派给 dispatcher。"""
        try:
            return await self.dispatcher._generate_fatigue_announcement(group_id)
        except AttributeError:
            return "唔……小萤用脑过度，去打盹半小时。"

    @property
    def _pending_perms(self) -> dict:
        try:
            return self.dispatcher._pending_perms
        except AttributeError:
            return {}

    @_pending_perms.setter
    def _pending_perms(self, value: dict):
        try:
            self.dispatcher._pending_perms = value
        except AttributeError:
            pass

    def get_active_task(self, session_key: str):
        return self._current_tasks.get(session_key)

    def set_active_task(self, session_key: str, task):
        self._current_tasks[session_key] = task

    def remove_active_task(self, session_key: str, task = None):
        if task is None:
            self._current_tasks.pop(session_key, None)
        else:
            current = self._current_tasks.get(session_key)
            if current is task:
                self._current_tasks.pop(session_key, None)

    def enqueue_message(self, session_key: str, event: dict, raw: str):
        if session_key not in self._message_queues:
            self._message_queues[session_key] = []
        self._message_queues[session_key].append((event, raw))

    def pop_queued_message(self, session_key: str):
        queue = self._message_queues.get(session_key, [])
        if queue:
            return queue.pop(0)
        return None

    def has_queued_messages(self, session_key: str) -> bool:
        return len(self._message_queues.get(session_key, [])) > 0

    def get_waiting_podcast_topic(self) -> dict:
        try:
            return self.dispatcher._waiting_podcast_topic
        except AttributeError:
            return {}

    def get_podcast_choices(self) -> dict:
        try:
            return self.dispatcher._podcast_choices
        except AttributeError:
            return {}

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
