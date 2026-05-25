# -*- coding: utf-8 -*-
"""抖音网关 - 浏览器与 CDP 调试管理模块"""
import os
import asyncio
import logging
import subprocess
import aiohttp
from playwright.async_api import async_playwright

logger = logging.getLogger("net_gateway.douyin.browser")

class DouyinBrowserManager:
    """专职负责常驻 CloakBrowser 浏览器实例的管理、9222 调试端口的 Popen 托管拉起、以及基于 CDP 的无感热接管"""
    
    def __init__(self):
        self.playwright_context = None
        self.browser_context = None
        self.page = None

    async def is_cdp_active(self) -> bool:
        """探测本地 9222 调试端口是否通畅"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:9222/json/version", timeout=1) as resp:
                    if resp.status == 200:
                        return True
        except Exception:
            pass
        return False

    async def init_browser(self) -> None:
        """初始化或热接管常驻 CloakBrowser"""
        cdp_active = await self.is_cdp_active()
        profile_dir = os.path.expanduser("~/.my-agent/memory/1705919142/cloak_douyin")
        os.makedirs(profile_dir, exist_ok=True)
        
        # 释放独占锁，防止锁文件挂住
        lock_file = os.path.join(profile_dir, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass

        if not cdp_active:
            logger.info("CDP target not active on port 9222. Launching resident CloakBrowser via standalone subprocess...")
            from cloakbrowser import ensure_binary, get_default_stealth_args
            chrome_path = ensure_binary()
            
            pos = os.getenv('DOUYIN_BROWSER_POSITION', '1400,900')
            cmd_args = [
                chrome_path,
                f"--user-data-dir={profile_dir}",
                "--remote-debugging-port=9222",
                f"--window-position={pos}",
                "--window-size=1280,800",
                "--hide-crash-restore-bubble",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check"
            ]
            # 加上默认 stealth 选项
            for arg in get_default_stealth_args():
                if not any(arg.split('=', 1)[0] in a for a in cmd_args):
                    cmd_args.append(arg)
            
            # 使用 Popen 抛到后台，使其脱离 Python 生命周期成为独立常驻系统进程
            subprocess.Popen(cmd_args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Resident CloakBrowser subprocess launched. Waiting for CDP port to open...")
            
            # 等待调试端口就绪
            for _ in range(20):
                await asyncio.sleep(0.5)
                if await self.is_cdp_active():
                    cdp_active = True
                    logger.info("CDP port 9222 is active!")
                    break
            
            if not cdp_active:
                logger.error("Failed to start resident CloakBrowser subprocess.")
                raise RuntimeError("Failed to start resident CloakBrowser.")
        else:
            logger.info("🎉 Found resident CloakBrowser active on port 9222. Reusing active instance via CDP...")

        # 2. 接管调试端口
        self.playwright_context = await async_playwright().start()
        browser = await self.playwright_context.chromium.connect_over_cdp("http://127.0.0.1:9222")
        
        if browser.contexts:
            self.browser_context = browser.contexts[0]
        else:
            self.browser_context = await browser.new_context()

        # 3. 检索已有的抖音 page
        self.page = None
        for pg in self.browser_context.pages:
            if "douyin.com" in pg.url:
                self.page = pg
                logger.info("🎉 Found active Douyin page in existing CloakBrowser context. Reusing it...")
                break

        if not self.page:
            logger.info("No active Douyin page found. Creating new page...")
            self.page = await self.browser_context.new_page()
            await self.page.set_viewport_size({"width": 1280, "height": 800})
            self.page.set_default_timeout(30000)
            
            # 首次冷启动，导航至主页 (不直达 message，从根源规避已失效 message 页 of 404 封锁)
            try:
                await self.page.goto("https://www.douyin.com", wait_until="domcontentloaded")
                await asyncio.sleep(6)
            except Exception as e:
                logger.error(f"Failed to navigate to Douyin Homepage initially: {e}")

    async def close_context(self) -> None:
        """温和断开调试连接，保持浏览器常驻"""
        if self.playwright_context:
            try:
                logger.info("Gently disconnecting CDP debugger without closing the resident browser...")
                await self.playwright_context.stop()
            except Exception as e:
                logger.error(f"Error stopping Playwright context: {e}")
            self.playwright_context = None
            self.browser_context = None
            self.page = None
