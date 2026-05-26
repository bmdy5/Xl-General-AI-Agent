# -*- coding: utf-8 -*-
"""抖音私信网关 - 独立微服务进程主控

独立进程，与 QQ 大脑通过 HTTP 通信。
上行: POST 粉丝消息到 :8000/event
下行: 接收大脑 :9000/send_private_msg 回复指令
"""

import os
import time
import random
import logging
import asyncio
import aiohttp
from aiohttp import web

from .douyin_browser import DouyinBrowserManager
from .douyin_dom_poller import DouyinDomPoller
from .douyin_dom_sender import DouyinDomSender

logger = logging.getLogger("net_gateway.douyin.main")

class FlushingFileHandler(logging.FileHandler):
    """自定义实时强行刷盘 FileHandler"""
    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass

# 抖音网关日志统一合流物理刷盘绑定
try:
    import sys
    from pathlib import Path
    root_dir = Path(__file__).resolve().parents[2]
    douyin_log_path = root_dir / "logs" / "douyin_gateway.log"
    os.makedirs(douyin_log_path.parent, exist_ok=True)
    
    parent_logger = logging.getLogger("net_gateway.douyin")
    # 清理所有历史残留 Handlers 避免重复打印
    for h in list(parent_logger.handlers):
        parent_logger.removeHandler(h)
        
    # 物理强行实时刷盘 FileHandler
    douyin_handler = FlushingFileHandler(str(douyin_log_path), encoding="utf-8")
    douyin_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'))
    parent_logger.addHandler(douyin_handler)
    
    # 控制台实时刷盘 StreamHandler
    class FlushingStreamHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            try:
                self.flush()
            except Exception:
                pass
                
    console_handler = FlushingStreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'))
    parent_logger.addHandler(console_handler)
    
    parent_logger.setLevel(logging.INFO)
    parent_logger.propagate = False
except Exception:
    pass

BRAIN_EVENT_URL = os.getenv("BRAIN_EVENT_URL", "http://127.0.0.1:8000/event")
BRAIN_QRCODE_URL = os.getenv("BRAIN_QRCODE_URL", "http://127.0.0.1:8000/report_qrcode")
DOUYIN_PORT = int(os.getenv("DOUYIN_PORT", "9000"))
POLLING_BASE_INTERVAL = 45.0

class DouyinGateway:
    """独立的抖音网关进程，负责自愈轮询主循环与 API 指令分发。"""

    def __init__(self):
        self.browser_mgr = DouyinBrowserManager()
        self.poller = DouyinDomPoller()
        self.sender = DouyinDomSender()

        self.nickname_map = {}
        self.last_processed_msg_map = {}
        self.active_session_key = None
        self.is_first_poll = True
        self.poll_count = 0
        
        self.current_interval = POLLING_BASE_INTERVAL
        self.consecutive_idle_turns = 0
        self.last_active_time = time.monotonic()
        
        self._running_task = None
        self._web_runner = None

    def start(self) -> None:
        """拉起抖音网关协程。"""
        logger.info("Douyin Gateway starting...")
        if os.getenv("DOUYIN_POLL_ENABLED", "").lower() in ("true", "1", "yes"):
            self._running_task = asyncio.create_task(self._run_loop())
        else:
            self._running_task = None
            async def _init_only():
                try:
                    await asyncio.sleep(2)
                    await self.browser_mgr.init_browser()
                    logger.info("✅ [浏览器接管] 独立 CloakBrowser 浏览器接管初始化完成 (API 服务随时可用)")
                except Exception as e:
                    logger.error(f"Failed to initialize browser only: {e}")
            asyncio.create_task(_init_only())
        # 独立拉起 aiohttp web server，接收大脑下发指令
        asyncio.create_task(self._start_web_server())

    async def stop(self) -> None:
        """优雅关闭网关及断开 CDP 调试。"""
        if self._running_task:
            self._running_task.cancel()
            self._running_task = None
            
        if self._web_runner:
            await self._web_runner.cleanup()
            self._web_runner = None

        await self.browser_mgr.close_context()

    async def _report_qrcode(self, local_path: str, message: str) -> None:
        """自愈上报：通过 HTTP 向主大脑投递二维码截图路径"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"local_path": local_path, "message": message}
                async with session.post(BRAIN_QRCODE_URL, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        logger.info("QR Code successfully reported to Brain event gateway.")
        except Exception as report_err:
            logger.error(f"Failed to report QR Code to Brain: {report_err}")

    async def _post_event_to_brain(self, event: dict) -> None:
        """消息上报：通过 HTTP 向主大脑投递捕获的消息 OneBot 事件"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BRAIN_EVENT_URL, json=event, timeout=5) as resp:
                    if resp.status == 200:
                        logger.info(f"Reported event from {event.get('sender', {}).get('nickname')} to Brain successfully.")
        except Exception as post_err:
            logger.error(f"Failed to report event to Brain: {post_err}")

    async def _run_loop(self) -> None:
        """自愈轮询主循环"""
        logger.info("Douyin Gateway backend daemon launched successfully!")
        await asyncio.sleep(5) # 避开启动爆发点
        
        await self.browser_mgr.init_browser()
        
        while True:
            try:
                self.poll_count += 1
                # ── ⚡ 物理发信智能退避与防干扰保护 ──
                if self.sender.is_sending:
                    logger.info("⏳ [轮询退避] 检测到正在执行物理发信动作，主轮询自动避让中...")
                    await asyncio.sleep(2.0)
                    continue

                # 0. 检验 Page 存活性，若失效则触发重连
                page_is_ok = False
                if self.browser_mgr.page:
                    try:
                        await self.browser_mgr.page.evaluate("1")
                        page_is_ok = True
                    except Exception:
                        logger.warning("Active page has been closed or lost. Need to re-init...")
                        self.browser_mgr.page = None
                        self.browser_mgr.browser_context = None
                        if self.browser_mgr.playwright_context:
                            try:
                                await self.browser_mgr.playwright_context.stop()
                            except Exception:
                                pass
                            self.browser_mgr.playwright_context = None

                if not self.browser_mgr.page:
                    await self.browser_mgr.init_browser()

                # 1. 登录校验与扫码自愈
                is_logged_in = await self.poller.ensure_logged_in(self.browser_mgr.page, self._report_qrcode)
                if not is_logged_in:
                    await asyncio.sleep(5)
                    continue

                # 2. 扫秒 DOM 私信
                events = await self.poller.poll_messages(
                    page=self.browser_mgr.page,
                    is_sending=self.sender.is_sending,
                    is_first_poll=self.is_first_poll,
                    nickname_map=self.nickname_map,
                    last_processed_msg_map=self.last_processed_msg_map,
                    active_session_key=self.active_session_key
                )
                self.is_first_poll = False

                # 3. 将扫描到的消息上报给大脑
                has_new = False
                if events:
                    has_new = True
                    for ev in events:
                        # 触发异步上报
                        asyncio.create_task(self._post_event_to_brain(ev))
                        self.active_session_key = ev.get("user_id")

                # 3.5 智能退避时间调节（高斯抖动）
                if has_new:
                    self.consecutive_idle_turns = 0
                    self.current_interval = random.uniform(5.0, 10.0)
                    self.last_active_time = time.monotonic()
                else:
                    self.consecutive_idle_turns += 1
                    if time.monotonic() - self.last_active_time > 300.0:
                        self.current_interval = random.gauss(80.0, 15.0)
                        self.current_interval = max(60.0, min(120.0, self.current_interval))
                    else:
                        self.current_interval = random.gauss(POLLING_BASE_INTERVAL, 8.0)
                        self.current_interval = max(30.0, min(65.0, self.current_interval))
                
                # 同步心跳假死除颤轮数
                self.poller.idle_reload_turns = self.poller.idle_reload_turns if not has_new else 0
                
                # ── ⚡ A方案极简心跳前缀输出 ──
                if not has_new:
                    panel_visible = await self.poller._container_visible(self.browser_mgr.page)
                    panel_str = "Visible" if panel_visible else "Hidden"
                    
                    active_session = self.active_session_key or "None"
                    if active_session != "None" and self.nickname_map:
                        nickname = self.nickname_map.get(active_session) or self.nickname_map.get(active_session.split("douyin_", 1)[-1]) or "Unknown"
                    else:
                        nickname = "None"
                    
                    last_msg = self.last_processed_msg_map.get(nickname) if nickname != "None" else "None"
                    if last_msg and len(last_msg) > 15:
                        last_msg = last_msg[:15] + "..."
                        
                    logger.info(f"[Poll #{self.poll_count}] Panel={panel_str} | ActiveSession={nickname} | LastMsg={repr(last_msg)} | IdleTurns={self.poller.idle_reload_turns} | NextInterval={self.current_interval:.1f}s")
                
                await asyncio.sleep(self.current_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Douyin Gateway daemon: {e}", exc_info=True)
                await asyncio.sleep(15)

    async def _start_web_server(self) -> None:
        """启动 HTTP API 服务，接收大脑下发指令及视觉操作请求"""
        app = web.Application()
        app.router.add_post('/send_private_msg', self._handle_send_msg)
        app.router.add_get('/get_nickname', self._handle_get_nickname)
        
        # 挂载 4 个通用视觉接管 API 路由契约
        app.router.add_post('/vision/screenshot', self._handle_vision_screenshot)
        app.router.add_post('/vision/click', self._handle_vision_click)
        app.router.add_post('/vision/type', self._handle_vision_type)
        app.router.add_post('/vision/scroll', self._handle_vision_scroll)
        
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, '127.0.0.1', DOUYIN_PORT)
        await site.start()
        logger.info(f"Douyin Standalone API Server listening on http://127.0.0.1:{DOUYIN_PORT}")

    async def _handle_get_nickname(self, request) -> web.Response:
        """根据 user_id 获取对应的粉丝昵称"""
        try:
            user_id = request.query.get("user_id")
            if not user_id:
                return web.json_response({"status": "failed", "reason": "Missing user_id"}, status=400)
            chat_id = user_id.split("douyin_", 1)[-1]
            nickname = self.nickname_map.get(user_id) or self.nickname_map.get(chat_id) or ""
            return web.json_response({"status": "success", "nickname": nickname})
        except Exception as e:
            return web.json_response({"status": "failed", "reason": str(e)}, status=500)

    async def _handle_send_msg(self, request) -> web.Response:
        """处理来自大脑下达的发送私信请求"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            message = data.get("message")
            if not user_id or not message:
                return web.json_response({"status": "failed", "reason": "Missing user_id or message"}, status=400)

            # A方案：同步阻塞发信，硬超时限制 10s
            try:
                success = await asyncio.wait_for(
                    self.sender.send_message(
                        page=self.browser_mgr.page,
                        target_user_id=user_id,
                        text=message,
                        nickname_map=self.nickname_map,
                        ensure_logged_in_cb=lambda: self.poller.ensure_logged_in(self.browser_mgr.page, self._report_qrcode)
                    ),
                    timeout=10.0
                )
                if success:
                    return web.json_response({"status": "success"})
                else:
                    return web.json_response({"status": "failed", "reason": "DOM operation failed in browser"})
            except asyncio.TimeoutError:
                logger.error("Timeout (10s) reached while sending DOM message.")
                return web.json_response({"status": "failed", "reason": "DOM operation timed out (10s)"})
        except Exception as e:
            logger.error(f"Error handling send private message request: {e}", exc_info=True)
            return web.json_response({"status": "failed", "reason": str(e)}, status=500)

    # ── ⚡ 视觉接管 API 处理器 ──

    async def _handle_vision_screenshot(self, request) -> web.Response:
        """视觉接管：捕获当前视口 1280x800 Base64 截图"""
        try:
            b64 = await self.browser_mgr.screenshot_base64()
            return web.json_response({"status": "success", "screenshot_b64": b64})
        except Exception as e:
            status_code = 400 if "UserInterrupted" in str(e) else 500
            return web.json_response({"status": "failed", "reason": str(e)}, status=status_code)

    async def _handle_vision_click(self, request) -> web.Response:
        """视觉接管：在绝对坐标 (x, y) 处执行物理鼠标左键点击，同步返回最新截图"""
        try:
            data = await request.json()
            x = data.get("x")
            y = data.get("y")
            if x is None or y is None:
                return web.json_response({"status": "failed", "reason": "Missing coordinate x or y"}, status=400)
            
            b64 = await self.browser_mgr.visual_click(int(x), int(y))
            return web.json_response({"status": "success", "screenshot_b64": b64})
        except Exception as e:
            status_code = 400 if "UserInterrupted" in str(e) else 500
            return web.json_response({"status": "failed", "reason": str(e)}, status=status_code)

    async def _handle_vision_type(self, request) -> web.Response:
        """视觉接管：在当前焦点元素模拟键盘打字，同步返回最新截图"""
        try:
            data = await request.json()
            text = data.get("text")
            if text is None:
                return web.json_response({"status": "failed", "reason": "Missing type text"}, status=400)
            
            b64 = await self.browser_mgr.visual_type(str(text))
            return web.json_response({"status": "success", "screenshot_b64": b64})
        except Exception as e:
            status_code = 400 if "UserInterrupted" in str(e) else 500
            return web.json_response({"status": "failed", "reason": str(e)}, status=status_code)

    async def _handle_vision_scroll(self, request) -> web.Response:
        """视觉接管：在视口上执行滚轮滚动，同步返回最新截图"""
        try:
            data = await request.json()
            direction = data.get("direction", "down")
            amount = data.get("amount", 400)
            
            b64 = await self.browser_mgr.visual_scroll(str(direction), int(amount))
            return web.json_response({"status": "success", "screenshot_b64": b64})
        except Exception as e:
            status_code = 400 if "UserInterrupted" in str(e) else 500
            return web.json_response({"status": "failed", "reason": str(e)}, status=status_code)

douyin_gateway = DouyinGateway()

