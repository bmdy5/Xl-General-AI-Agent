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
            
            # 默认窗口位置修改为屏幕中央的 100,100，确保亮哥清晰可见并能完成一次性扫码
            pos = os.getenv('DOUYIN_BROWSER_POSITION', '100,100')
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

    # ── ⚡ 通用视觉自愈接管引擎增量核心 ──

    async def screenshot_base64(self) -> str:
        """捕获当前页面的 1280x800 Base64 编码 PNG 截图 (内存直传，不落盘)"""
        if not self.page:
            raise RuntimeError("Active page context is not initialized.")
        await self.page.set_viewport_size({"width": 1280, "height": 800})
        img_bytes = await self.page.screenshot(type="png")
        import base64
        b64_str = base64.b64encode(img_bytes).decode("utf-8")
        return f"data:image/png;base64,{b64_str}"

    async def setup_preemption_listener(self) -> None:
        """向浏览器端注入位移监测器与“小萤自愈横幅”"""
        if not self.page:
            return
        try:
            await self.page.evaluate("""() => {
                window.__myagent_mouse_movement = 0;
                if (!window.__myagent_listening) {
                    window.__myagent_listening = true;
                    window.addEventListener('mousemove', (e) => {
                        if (e.movementX && e.movementY) {
                            const dist = Math.sqrt(e.movementX * e.movementX + e.movementY * e.movementY);
                            if (dist > 15) { // 物理剧烈位移阈值
                                window.__myagent_mouse_movement += dist;
                            }
                        }
                    });
                }
                
                // 注入专属悬浮警示条
                let banner = document.getElementById('myagent-visual-banner');
                if (!banner) {
                    banner = document.createElement('div');
                    banner.id = 'myagent-visual-banner';
                    banner.style.position = 'fixed';
                    banner.style.top = '0px';
                    banner.style.left = '50%';
                    banner.style.transform = 'translateX(-50%)';
                    banner.style.backgroundColor = '#FFF0F5';
                    banner.style.border = '1px solid #FF80B5';
                    banner.style.color = '#FF80B5';
                    banner.style.padding = '8px 16px';
                    banner.style.borderRadius = '0 0 8px 8px';
                    banner.style.fontFamily = 'sans-serif';
                    banner.style.fontSize = '14px';
                    banner.style.fontWeight = 'bold';
                    banner.style.zIndex = '999999';
                    banner.style.pointerEvents = 'none';
                    banner.innerText = '🤖 小萤正在进行视觉自愈操作，请稍候...';
                    document.body.appendChild(banner);
                }
            }""")
        except Exception as e:
            logger.error(f"Failed to setup preemption listener on page: {e}")

    async def check_preemption(self) -> None:
        """检测亮哥的物理抢占行为。位移得分超标立即触发自毁与让权退避"""
        if not self.page:
            return
        try:
            score = await self.page.evaluate("window.__myagent_mouse_movement || 0")
            if score > 100:
                await self.cleanup_visual_overlay()
                logger.warning("🚨 [物理抢占] 监测到主人物理控制权抢占，小萤立即自毁视觉覆盖并断开 CDP 让权。")
                raise RuntimeError("UserInterrupted: 主人已物理抢占浏览器控制权，接管被迫中止")
        except Exception as e:
            if "UserInterrupted" in str(e):
                raise e

    async def cleanup_visual_overlay(self) -> None:
        """优雅移除注入的虚拟光标与悬浮提示横幅"""
        if not self.page:
            return
        try:
            await self.page.evaluate("""() => {
                const banner = document.getElementById('myagent-visual-banner');
                if (banner) banner.remove();
                const cursor = document.getElementById('myagent-pink-cursor');
                if (cursor) cursor.remove();
                window.__myagent_mouse_movement = 0;
            }""")
        except Exception:
            pass

    async def visual_click(self, x: int, y: int) -> str:
        """模拟平滑滑动小萤专属粉色心形光标并执行点击，伴随淡粉色水波纹动画，最后同步返回最新 Base64 截图"""
        if not self.page:
            raise RuntimeError("Active page context is not initialized.")

        # 1. 注入抢占监听与警示
        await self.setup_preemption_listener()
        await self.check_preemption()

        # 2. 用 JS 渲染粉色虚拟鼠标平滑滑移与点击波纹
        try:
            await self.page.evaluate("""([tx, ty]) => {
                let cursor = document.getElementById('myagent-pink-cursor');
                if (!cursor) {
                    cursor = document.createElement('div');
                    cursor.id = 'myagent-pink-cursor';
                    cursor.style.position = 'fixed';
                    cursor.style.width = '24px';
                    cursor.style.height = '24px';
                    cursor.style.zIndex = '999999';
                    cursor.style.pointerEvents = 'none';
                    cursor.style.transition = 'all 0.5s cubic-bezier(0.25, 0.8, 0.25, 1)';
                    cursor.innerHTML = `
                        <svg viewBox="0 0 24 24" width="32" height="32" fill="#FF1493" style="filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.3));">
                            <polygon points="3,3 3,21 9,15 15,24 18,22 12,14 21,14" stroke="white" stroke-width="1.5" stroke-linejoin="round"/>
                        </svg>
                    `;
                    document.body.appendChild(cursor);
                    cursor.style.left = '0px';
                    cursor.style.top = '0px';
                }
                
                // 平滑移向目标点
                cursor.style.left = tx + 'px';
                cursor.style.top = ty + 'px';
                
                // 延时 500ms 后触发水波纹微动画
                setTimeout(() => {
                    const ripple = document.createElement('div');
                    ripple.style.position = 'fixed';
                    ripple.style.left = tx + 'px';
                    ripple.style.top = ty + 'px';
                    ripple.style.width = '10px';
                    ripple.style.height = '10px';
                    ripple.style.borderRadius = '50%';
                    ripple.style.border = '2px solid #FF80B5';
                    ripple.style.backgroundColor = 'rgba(255, 128, 181, 0.2)';
                    ripple.style.transform = 'translate(-50%, -50%) scale(1)';
                    ripple.style.transition = 'all 0.4s ease-out';
                    ripple.style.pointerEvents = 'none';
                    ripple.style.zIndex = '999998';
                    document.body.appendChild(ripple);
                    
                    requestAnimationFrame(() => {
                        ripple.style.transform = 'translate(-50%, -50%) scale(4)';
                        ripple.style.opacity = '0';
                    });
                    
                    setTimeout(() => ripple.remove(), 400);
                }, 500);
            }""", [x, y])
        except Exception as e:
            logger.error(f"Failed to animate visual click overlay: {e}")

        # 3. 等待滑移与波纹动画执行完毕
        await asyncio.sleep(0.55)
        await self.check_preemption()

        # 4. 执行 Playwright CDP 真实物理点击
        await self.page.mouse.click(x, y)
        await asyncio.sleep(0.30)
        
        # 5. 清理粉色虚拟光标并捕获最新最新画面
        await self.cleanup_visual_overlay()
        return await self.screenshot_base64()

    async def visual_type(self, text: str) -> str:
        """在当前输入焦点执行打字键盘输入 (带有真人 50ms 字符延迟)"""
        if not self.page:
            raise RuntimeError("Active page context is not initialized.")
        await self.setup_preemption_listener()
        await self.check_preemption()

        await self.page.keyboard.type(text, delay=50)
        await asyncio.sleep(0.30)
        await self.cleanup_visual_overlay()
        return await self.screenshot_base64()

    async def visual_scroll(self, direction: str, amount: int) -> str:
        """物理滚动当前页面视口 (direction: 'up'/'down')"""
        if not self.page:
            raise RuntimeError("Active page context is not initialized.")
        await self.setup_preemption_listener()
        await self.check_preemption()

        delta_y = amount if direction.lower() == "down" else -amount
        await self.page.mouse.wheel(0, delta_y)
        await asyncio.sleep(0.50)
        await self.cleanup_visual_overlay()
        return await self.screenshot_base64()

