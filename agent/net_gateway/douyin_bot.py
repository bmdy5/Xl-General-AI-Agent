# -*- coding: utf-8 -*-
"""抖音私信防封隐身网关 - 独立微服务进程主控 (DouyinGateway)

基于 CloakBrowser 隐形指纹独立常驻进程驱动。
剥离了浏览器驱动、DOM轮询及物理发送，单个子模块全部小于 300 行。
通过 9001 端口 HTTP 接口接收大脑的下行指令，并将捕获消息通过 8000 端口 POST 上报给大脑。
"""

import os
import time
import random
import logging
import asyncio
import aiohttp
from aiohttp import web
from typing import Optional

from .douyin_browser import DouyinBrowserManager
from .douyin_dom_poller import DouyinDomPoller
from .douyin_dom_sender import DouyinDomSender

logger = logging.getLogger("net_gateway.douyin.main")

# 抖音网关日志重定向绑定
try:
    from pathlib import Path
    root_dir = Path(__file__).resolve().parents[2]
    douyin_log_path = root_dir / "logs" / "douyin_gateway.log"
    os.makedirs(douyin_log_path.parent, exist_ok=True)
    douyin_handler = logging.FileHandler(str(douyin_log_path), encoding="utf-8")
    douyin_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'))
    logger.addHandler(douyin_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
except Exception:
    pass

BRAIN_EVENT_URL = os.getenv("BRAIN_EVENT_URL", "http://127.0.0.1:8000/event")
BRAIN_QRCODE_URL = os.getenv("BRAIN_QRCODE_URL", "http://127.0.0.1:8000/report_qrcode")
DOUYIN_PORT = int(os.getenv("DOUYIN_PORT", "9001"))
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
        
        self.current_interval = POLLING_BASE_INTERVAL
        self.consecutive_idle_turns = 0
        self.last_active_time = time.monotonic()
        
        self._running_task = None
        self._web_runner = None

    def start(self, dispatcher=None) -> None:
        """多协程拉起抖音独立网关进程（兼容原 OneBot 启动器）。"""
        logger.info("Initializing Standalone Douyin Gateway Process...")
        self._running_task = asyncio.create_task(self._run_loop())
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
                
                await asyncio.sleep(self.current_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Douyin Gateway daemon: {e}", exc_info=True)
                await asyncio.sleep(15)

    async def _start_web_server(self) -> None:
        """开启 9001 端口极轻量 API 监听，接收大脑下达的下行发消息请求"""
        app = web.Application()
        app.router.add_post('/send_private_msg', self._handle_send_msg)
        
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, '127.0.0.1', DOUYIN_PORT)
        await site.start()
        logger.info(f"Douyin Standalone API Server listening on http://127.0.0.1:{DOUYIN_PORT}")

    async def _handle_send_msg(self, request) -> web.Response:
        """处理来自大脑下达的发送私信请求"""
        try:
            data = await request.json()
            user_id = data.get("user_id")
            message = data.get("message")
            if not user_id or not message:
                return web.json_response({"status": "failed", "reason": "Missing user_id or message"}, status=400)
            
            # 委派给 DOM 发送器执行物理输入
            asyncio.create_task(self.sender.send_message(
                page=self.browser_mgr.page,
                target_user_id=user_id,
                text=message,
                nickname_map=self.nickname_map,
                ensure_logged_in_cb=lambda: self.poller.ensure_logged_in(self.browser_mgr.page, self._report_qrcode)
            ))
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"status": "failed", "reason": str(e)}, status=500)

douyin_gateway = DouyinGateway()
