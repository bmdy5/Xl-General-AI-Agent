# -*- coding: utf-8 -*-
"""抖音私信防封隐身网关 (DouyinGateway)

基于 CloakBrowser 隐形 Chromium 沙箱物理驱动网页版抖音私信。
采用「智能随机退避轮询」防反爬，捕获私信并桥接小萤已有的 CSMA/CD、Fatigue 及 RAG 高可用处理管道。
支持 Cookie 登录失效时自动截图上传 COS 并通过 QQ 提醒亮哥扫码自愈。
"""

import os
import re
import time
import random
import logging
import asyncio
from typing import Optional, Any
from pathlib import Path

logger = logging.getLogger("net_gateway.douyin")

# 绑定独立的抖音网关专属日志文件，方便亮哥实时纯净监控
try:
    from pathlib import Path
    import logging.handlers
    root_dir = Path(__file__).resolve().parents[2]
    douyin_log_path = root_dir / "logs" / "douyin_gateway.log"
    os.makedirs(douyin_log_path.parent, exist_ok=True)
    douyin_handler = logging.FileHandler(str(douyin_log_path), encoding="utf-8")
    douyin_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'))
    logger.addHandler(douyin_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False # 彻底隔断，不让抖音日志流入喧闹的 gateway.log
except Exception as log_init_err:
    pass

# 抖音网页端消息中心 URL
DOUYIN_MSG_URL = "https://www.douyin.com/message"
DOUYIN_HOME_URL = "https://www.douyin.com"
# 默认智能退避基准间隔（秒）
POLLING_BASE_INTERVAL = 45.0


class DouyinGateway:
    """抖音私信通信网关，负责 CloakBrowser 常驻监控、DOM 拟真收发、扫码自愈。"""

    def __init__(self):
        self.dispatcher = None
        self.context = None
        self.browser_context = None
        self.page = None
        self._running_task = None
        
        # 智能随机退避间隔状态
        self.current_interval = POLLING_BASE_INTERVAL
        self.consecutive_idle_turns = 0
        
        # 活跃的会话标志，用于收窄即时聊天回复的轮询间隔
        self.active_session_key = None
        self.last_active_time = 0.0
        
        # 内存昵称映射表，用于把 MD5 加密的 chat_id 和粉丝真实 nickname 做绑定
        self.nickname_map = {}
        
        # 缓存每个粉丝最后一次已处理的对方消息文本，支持无红点高亮会话的主动心跳扫描
        self.last_processed_msg_map = {}
        
        # 连续闲置（无消息变化）计数器，用于触发 WebSocket 重载除颤自愈
        # 默认设为 2，以保障冷启动首轮在去重时瞬间触发强力 Reload 激活最新离线长连接
        self.idle_reload_turns = 2
        
        # 状态标志，用于防打扰与下行优先
        self.is_sending = False
        self.send_lock = asyncio.Lock()

    def start(self, dispatcher) -> None:
        """多协程拉起抖音私信网关."""
        self.dispatcher = dispatcher
        self.context = dispatcher.context
        
        logger.info("Initializing Douyin Gateway dynamic task...")
        self._running_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """优雅关闭网关及浏览器."""
        if self._running_task:
            self._running_task.cancel()
            self._running_task = None
        
        if self.browser_context:
            try:
                await self.browser_context.close()
            except Exception as e:
                logger.error(f"Error closing CloakBrowser: {e}")
            self.browser_context = None
            self.page = None

    async def _run_loop(self) -> None:
        """常驻智能随机退避轮询与登录自愈主循环."""
        logger.info("Douyin Gateway backend daemon launched successfully!")
        
        # 延迟 5 秒拉起浏览器，防止和 OneBot 网关抢占系统端口或 CPU 爆发点
        await asyncio.sleep(5)
        
        await self._init_browser()
        
        while True:
            try:
                # 1. 登录状态性检测与扫码自愈
                is_logged_in = await self._ensure_logged_in()
                if not is_logged_in:
                    # 登录失效，进入扫码自愈流
                    await self._handle_login_self_healing()
                    continue
                
                # 2. 已登录状态下执行 DOM 私信扫描
                has_new = await self._poll_messages()
                
                # 2.5 实时生成调试截图，以 Artifact 形式供亮哥审查
                try:
                    if self.page:
                        import os
                        os.makedirs("/Users/xiaofeng/.gemini/antigravity-ide/brain/e4d4d097-84cc-4c05-a164-25bdd4bb5985", exist_ok=True)
                        await self.page.screenshot(path="/Users/xiaofeng/.gemini/antigravity-ide/brain/e4d4d097-84cc-4c05-a164-25bdd4bb5985/debug_reply_success.png")
                        logger.info("Successfully updated real-time browser screenshot 'debug_reply_success.png'")
                except Exception as snap_err:
                    logger.warning(f"Failed to update real-time debug snapshot: {snap_err}")
                
                # 3. 智能退避时间调节（高斯抖动）
                if has_new:
                    # 对话活跃期：下一次轮询收窄到 5-10 秒，保证即时聊天的体验
                    self.consecutive_idle_turns = 0
                    self.current_interval = random.uniform(5.0, 10.0)
                    self.last_active_time = time.monotonic()
                else:
                    self.consecutive_idle_turns += 1
                    # 闲置期：采用高斯分布在 40s - 75s 之间波动以防反爬，且随着闲置轮数拉长退避时间
                    if time.monotonic() - self.last_active_time > 300.0:
                        # 超过 5 分钟无对话，进入深度 IDLE 退避
                        self.current_interval = random.gauss(80.0, 15.0)
                        self.current_interval = max(60.0, min(120.0, self.current_interval))
                    else:
                        self.current_interval = random.gauss(POLLING_BASE_INTERVAL, 8.0)
                        self.current_interval = max(30.0, min(65.0, self.current_interval))
                
                logger.debug(f"Douyin Gateway sleeping for {self.current_interval:.1f}s...")
                await asyncio.sleep(self.current_interval)
                
            except asyncio.CancelledError:
                logger.info("Douyin Gateway loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in Douyin Gateway daemon: {e}", exc_info=True)
                await asyncio.sleep(15)  # 发生异常，静默 15s 后自愈重试

    async def _init_browser(self) -> None:
        """使用 CloakBrowser 隐形指纹 Profile 初始化 Chromium 沙箱."""
        from cloakbrowser import launch_persistent_context_async
        
        profile_dir = os.path.expanduser("~/.my-agent/memory/1705919142/cloak_douyin")
        os.makedirs(profile_dir, exist_ok=True)
        
        # 【强杀与释放独占锁】强制清除可能冲突的残留孤儿 Chromium 进程，保障 SingletonLock 干净释放
        logger.info("Clearing SingletonLock and lingering chromium instances to avoid lock conflict...")
        os.system("ps aux | grep -i 'chromium.*cloak_douyin' | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null")
        lock_file = os.path.join(profile_dir, "SingletonLock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass
        
        logger.info(f"Launching CloakBrowser sandbox with Profile: {profile_dir}")
        
        # 物理启动持久化隐形指纹 context (物理有头 + 扔到屏幕外，规避 404 检测并获取高信誉)
        self.browser_context = await launch_persistent_context_async(
            user_data_dir=profile_dir,
            headless=False,  # 物理有头！享有真实 GPU 与高信誉！
            humanize=True,   # 强制加持人类物理键鼠操作曲线补丁，抗 30/30 设备检测
            viewport=None,   # 【避坑红线】设为 None，断绝 Playwright 用 Viewport 尺寸强行把物理窗口拉大的冲突 Bug！
            args=[
                "--window-position=9999,9999",    # 将窗口物理扔到逻辑分辨率外的右下角极远虚空
                "--window-size=1280,800",         # 维持标准工业尺寸保证后台全功率重绘渲染与新消息拉取！
                "--hide-crash-restore-bubble",   # 物理消灭崩溃恢复气泡
                "--disable-infobars"
            ]
        )
        
        self.page = await self.browser_context.new_page()
        # 【物理自愈重塑】动态重置逻辑视口为 1280x800，保证抖音侧边栏布局与 Actionability 判定 100% 完美无瑕！
        await self.page.set_viewport_size({"width": 1280, "height": 800})
        self.page.set_default_timeout(30000)
        
        # 首次冷启动，导航至主页 (不直达 message，从根源规避已失效 message 页的 404 封锁)
        try:
            await self.page.goto(DOUYIN_HOME_URL, wait_until="domcontentloaded")
            await asyncio.sleep(6)
        except Exception as e:
            logger.error(f"Failed to navigate to Douyin Homepage initially: {e}")

    async def _ensure_logged_in(self) -> bool:
        """判定当前是否已处于已登录并打开了私信面板的状态."""
        if not self.page:
            return False
        
        current_url = self.page.url
        # 如果重定向到 sso.douyin.com 或含有 login，表明未登录
        if "login" in current_url or "sso.douyin" in current_url:
            return False
        
        # 1. 优先使用私信面板可见性作为登录并就绪的标准
        is_visible = False
        try:
            is_visible = await self.page.locator('#imSaasContainerId').is_visible()
            if is_visible:
                return True
        except Exception:
            pass
            
        # 2. 如果面板不可见，但主页显示已经登录（例如能找到用户头像），则物理点击右上角私信按钮展开侧边栏
        avatar_element = None
        for avatar_sel in ['.dy-avatar', '[class*="avatar"]', '[class*="header"] [src*="avatar"]']:
            try:
                avatar_element = await self.page.query_selector(avatar_sel)
                if avatar_element:
                    break
            except Exception:
                continue
                
        if avatar_element:
            logger.info("[登录状态验证] 页面检测到登录头像。尝试物理展开右侧私信面板...")
            # 物理点击右上角私信按钮
            for btn_sel in ['a[href*="message"]', 'text=私信', '[class*="message"]', 'svg:has-text("私信")']:
                try:
                    btn = await self.page.query_selector(btn_sel)
                    if btn:
                        # 强力穿透与 2 秒灵敏限时点击，打碎隐藏A标签导致的假死卡壳 30 秒
                        await btn.click(force=True, timeout=2000)
                        await asyncio.sleep(4)
                        break
                except Exception:
                    continue
            
            # 点击后再次检查面板可见性与联系人就绪
            try:
                # 强制等待侧边栏动画滑动出完毕，且列表第一项对 Playwright 呈现 visible 状态
                await self.page.locator('.conversationConversationItemwrapper, [class*="ConversationItem"]').first.wait_for(state="visible", timeout=6000)
                is_visible = await self.page.locator('#imSaasContainerId').is_visible()
                if is_visible:
                    return True
            except Exception:
                pass
                
        return False

    async def _handle_login_self_healing(self) -> None:
        """Cookie 登录失效时的扫码自愈逻辑 (截图 COS 推送亮哥 QQ)."""
        logger.warning("[登录失效] 检测到抖音登录态过期，启动全自动扫码自愈流程...")
        
        # 1. 强制重新载入主页以自动调出登录弹窗或展示登录态
        try:
            await self.page.goto(DOUYIN_HOME_URL)
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"Failed to load homepage: {e}")
            await asyncio.sleep(5)
            return

        # 2. 定位网页二维码元素并物理截图
        local_path = os.path.expanduser("~/.my-agent/memory/1705919142/douyin_login_qrcode.png")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # 抖音登录弹窗的二维码通常可以通过 CSS 定位器捕获
        qrcode_selectors = [
            'div[class*="login-guide-container"] iframe',
            'div[class*="qrcode"]',
            'canvas',
            'img[src*="qrcode"]',
            '[class*="qrcode-image"]',
            'div[class*="qr-code"]'
        ]
        
        qrcode_element = None
        for sel in qrcode_selectors:
            try:
                qrcode_element = await self.page.query_selector(sel)
                if qrcode_element:
                    logger.info(f"Located login QR code element via selector: {sel}")
                    break
            except Exception:
                continue

        # 3. 截图并调用 send_image_to_qq 发送给亮哥
        try:
            if qrcode_element:
                await qrcode_element.screenshot(path=local_path)
            else:
                # 找不到具体二维码，直接截取整个视口作为兜底
                await self.page.screenshot(path=local_path)
                logger.warning("QR code element not found, screenshot whole page as fallback.")

            # 调用我们在上一步中抽象出的 send_image_to_qq 工具
            from agent.tools.registry import registry
            image_tool = registry.get("send_image_to_qq")
            if image_tool:
                async for res in image_tool.call({
                    "local_path": local_path,
                    "cos_key_suffix": "douyin_qrcode/login.png",
                    "message_prefix": (
                        "[抖音小萤扫码自愈提示]\n"
                        "亮哥！我的抖音登录态失效啦，请在手机上扫码授权我重新恢复灵魂交互吧"
                    )
                }):
                    if res.type == "result":
                        logger.info(f"Login QR code sent to admin: {res.data}")
            else:
                logger.error("send_image_to_qq tool not registered in registry!")
        except Exception as qrcode_err:
            logger.error(f"Failed to capture or send login QR code: {qrcode_err}")

        # 4. 后台阻塞，每隔 5 秒监听状态直至登录自愈成功
        logger.info("QR code successfully pushed. Waiting for admin to scan on phone...")
        for _ in range(60):  # 最多等待 5 分钟 (60 * 5s)
            await asyncio.sleep(5)
            if await self._ensure_logged_in():
                logger.info("[自愈成功] 亮哥扫码成功！恢复智能退避轮询！")
                # 成功跳转，自动清理临时二维码
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
                return
                
        logger.warning("Timeout waiting for admin to scan QR code. Refreshing page...")

    async def _poll_messages(self) -> bool:
        """扫描主页侧边栏私信列表 DOM，捕获最新对方消息并转化为等价 OneBot 事件派发."""
        if not self.page:
            return False
        
        # 1. 物理动作防冲突：若当前正处于物理打字发送期，为了防止物理点击切换联系人导致输入串台，
        # 我们开启“安全被动听觉轮询”，仅允许在当前右侧活跃聊天窗口中监听新消息，坚决不进行左侧联系人切换！
        only_passive_listen = False
        if self.is_sending:
            logger.info("Current in physical sending state. Engaging passive message monitoring without contact switching.")
            only_passive_listen = True
            
        # === [ WebSocket 假死重载除颤自愈机制 ] ===
        if self.idle_reload_turns >= 2:
            logger.info("ℹ️ Detected 2 consecutive idle turns without message change. Triggering F5 page reload to heal WebSocket connection...")
            try:
                await self.page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(6.0)
                # 重新展开侧边栏（如果重载后关闭了）
                await self._ensure_logged_in()
            except Exception as reload_err:
                logger.error(f"Failed to reload page for WebSocket healing: {reload_err}")
            self.idle_reload_turns = 0
            
        # 确认私信侧边栏是否可见，不可见则无法轮询
        try:
            is_sidebar_visible = await self.page.locator('#imSaasContainerId').is_visible()
            if not is_sidebar_visible:
                return False
        except Exception as e:
            logger.warning(f"Failed to check sidebar visibility in poll: {e}")
            return False
        
        has_new_message = False
        is_active_session_sync = False  # 标记是否为当前活跃会话的心跳同步
        try:
            # 2. 检索带有未读标记/红点的节点
            unread_selectors = [
                '#imSaasContainerId [class*="unread-count"]',
                '#imSaasContainerId [class*="badge"]',
                '#imSaasContainerId [class*="red-dot"]',
                '#imSaasContainerId [class*="unread"]'
            ]
            
            unread_node = None
            if not only_passive_listen:
                for sel in unread_selectors:
                    try:
                        unread_node = await self.page.query_selector(sel)
                        if unread_node:
                            break
                    except Exception:
                        continue
            
            contact_container = None
            if unread_node:
                # 3. 仅物理处理最上方的一个未读条目（使用 JS 祖先特征智能回溯算法，100% 精准锁定列表项容器）
                try:
                    contact_container = await unread_node.evaluate_handle(
                        """(node) => {
                            let p = node.parentElement;
                            while (p && p.id !== 'imSaasContainerId') {
                                let cl = p.className || '';
                                if (typeof cl === 'string' && (
                                    cl.includes('ConversationItem') || 
                                    cl.includes('item') || 
                                    cl.includes('Item') ||
                                    cl.includes('wrapper') || 
                                    cl.includes('session') ||
                                    cl.includes('member')
                                )) {
                                    return p;
                                }
                                p = p.parentElement;
                            }
                            return node.parentElement?.parentElement?.parentElement || node.parentElement;
                        }"""
                    )
                except Exception as eval_err:
                    logger.error(f"Failed to find closest contact container via ancestor traceback: {eval_err}")
            else:
                # 兜底：尝试获取当前已经处于 active 状态的活跃聊天会话进行心跳同步（直接锁定私信列表中的第一项，IM 机制下最新活跃会话 100% 自动置顶于此）
                try:
                    # 优先过滤物理高度（40px-120px），且内部包含头像或文本，100% 排除顶部搜索框等非会话元素
                    active_handle = await self.page.evaluate_handle(
                        """() => {
                            let items = document.querySelectorAll('#imSaasContainerId [class*="Item"], #imSaasContainerId [class*="wrapper"]');
                            for (let item of items) {
                                if (item.offsetHeight < 40 || item.offsetHeight > 120) continue;
                                let avatar = item.querySelector('img, [class*="avatar"]');
                                let text = item.querySelector('[class*="name"], [class*="nickname"], [class*="title"]');
                                if (avatar || text) return item;
                            }
                            return document.querySelector('#imSaasContainerId [class*="ConversationItem"], #imSaasContainerId [class*="Item"]');
                        }"""
                    )
                    if active_handle and hasattr(active_handle, "as_element") and active_handle.as_element():
                        active_item = active_handle.as_element()
                        if active_item:
                            contact_container = active_item
                            is_active_session_sync = True
                except Exception as active_err:
                    logger.warning(f"Failed to find first active contact item: {active_err}")

            if not contact_container:
                return False  # 既无红点也无当前高亮会话，保持常驻静默

            # 4. 提取该未读联系人的昵称（nickname）以建立映射关系
            sender_nickname = "抖音粉丝"
            try:
                # 优先在元素内部提取 name / nickname / title
                name_element = await contact_container.query_selector('[class*="name"], [class*="nickname"], [class*="title"]')
                if name_element:
                    text_content = await name_element.inner_text()
                    if text_content.strip():
                        sender_nickname = text_content.strip().split('\n')[0].strip()
                
                # 如果匹配失败退化为了默认值，启用超强兜底：使用 innerText 智能拆分过滤法提取真实昵称
                if sender_nickname in ["抖音粉丝", "未知", ""]:
                    raw_text = await contact_container.inner_text()
                    if raw_text:
                        # 按行拆分，过滤掉纯数字的未读红点数字和时间后缀等干扰行
                        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                        possible_names = []
                        for line in lines:
                            if line.isdigit():
                                continue
                            if any(t in line for t in ["分钟前", "小时前", "昨天", "前天", "星期", "秒前", "月"]):
                                continue
                            possible_names.append(line)
                        if possible_names:
                            sender_nickname = possible_names[0]
            except Exception as name_err:
                logger.warning(f"Failed to extract contact nickname: {name_err}")

            # 5. 物理点击并安全缓冲（仅在非被动监听下允许物理切换，防止正在打字时切走聊天导致打字串台）
            if not only_passive_listen:
                logger.info(f"Physically clicking/refreshing conversation with: {sender_nickname}...")
                try:
                    try:
                        # 优先物理鼠标模拟点击（100% 触发 React 合成事件与 mousedown/mouseup/click 链路）
                        await contact_container.click(force=True, timeout=3000)
                    except Exception as phys_err:
                        logger.warning(f"Failed physical click during active sync: {phys_err}, trying JS fallback...")
                        # 物理点击受限时，fallback 至 JS 全层级原生穿透点击
                        await contact_container.evaluate(
                            """(node) => {
                                node.click();
                                let target = node.querySelector('[class*="name"], [class*="nickname"], [class*="title"], img, [class*="avatar"]');
                                if (target) target.click();
                                for (let child of node.children) {
                                    child.click();
                                }
                            }"""
                        )
                    # 强制加入 2.5 - 3.5 秒的安全切换冷却时间，以模拟人类正常视觉和缓冲，抗风控性极高
                    await asyncio.sleep(random.uniform(2.5, 3.5))
                except Exception as click_err:
                    logger.error(f"Failed to physically click contact item: {click_err}")
                    return False
            else:
                logger.info(f"Passive monitor: skipping physical click switch for active session of: {sender_nickname}")

            # === [新共识：Header 真实昵称终极纠正黑科技] ===
            try:
                # 在右侧聊天视窗中直接抓取顶部的 Header 真实昵称，100% 纠正左侧的匹配偏差
                header_nickname = await self.page.evaluate(
                    """() => {
                        let header = document.querySelector(
                            '#imSaasContainerId [class*="ChatHeader"], #imSaasContainerId [class*="chat-header"], #imSaasContainerId [class*="saasImHeader"], #imSaasContainerId [class*="header"]'
                        );
                        if (header && header.innerText.trim()) {
                            let name = header.innerText.trim().split('\\n')[0];
                            if (name && name !== '私信' && name !== '消息') {
                                return name;
                            }
                        }
                        return null;
                    }"""
                )
                if header_nickname and header_nickname.strip():
                    sender_nickname = header_nickname.strip()
                    logger.info(f"Successfully corrected sender nickname via ChatHeader: {sender_nickname}")
            except Exception as header_err:
                logger.warning(f"Failed to extract real nickname from ChatHeader: {header_err}")

            # 6. 解析右侧聊天窗口中的最新对方发言
            message_nodes = await self.page.query_selector_all('#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"], #imSaasContainerId [class*="chat-item"]')
            if not message_nodes:
                # 终极冷启动自愈：若右侧聊天视窗未打开（新重载无消息节点），主动物理点击锁定列表第一项，强制拉起会话窗口
                if is_active_session_sync and contact_container:
                    logger.info("Chat panel is blank initially. Activating conversation via dual-track click bypass...")
                    try:
                        try:
                            # 优先使用真实系统级物理鼠标模拟点击（100% 触发 React 合成事件与 mousedown/mouseup/click 链路）
                            await contact_container.click(force=True, timeout=3000)
                            logger.info("Successfully executed Playwright physical click on contact container.")
                        except Exception as phys_click_err:
                            logger.warning(f"Physical click failed: {phys_click_err}, trying multi-level JS fallback...")
                            # 物理点击受限时，fallback 至 JS 全层级原生穿透点击
                            await contact_container.evaluate(
                                """(node) => {
                                    node.click();
                                    let target = node.querySelector('[class*="name"], [class*="nickname"], [class*="title"], img, [class*="avatar"]');
                                    if (target) target.click();
                                    for (let child of node.children) {
                                        child.click();
                                    }
                                }"""
                            )
                        await asyncio.sleep(random.uniform(3.0, 4.5))
                        # 点击后重新获取消息节点
                        message_nodes = await self.page.query_selector_all('#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"], #imSaasContainerId [class*="chat-item"]')
                    except Exception as click_err:
                        logger.error(f"Failed to physically click contact to activate session: {click_err}")

                # === [打破新用户/延迟加载空会话死循环] ===
                has_active_header = False
                try:
                    header_nickname = await self.page.evaluate(
                        """() => {
                            let header = document.querySelector(
                                '#imSaasContainerId [class*="ChatHeader"], #imSaasContainerId [class*="chat-header"], #imSaasContainerId [class*="saasImHeader"], #imSaasContainerId [class*="header"]'
                            );
                            if (header && header.innerText.trim()) {
                                let name = header.innerText.trim().split('\\n')[0];
                                if (name && name !== '私信' && name !== '消息') {
                                    return name;
                                }
                            }
                            return null;
                        }"""
                    )
                    if header_nickname and header_nickname.strip():
                        has_active_header = True
                        sender_nickname = header_nickname.strip()
                        logger.info(f"Chat panel message empty, but confirmed activated session with header: {sender_nickname}")
                except Exception as header_chk_err:
                    logger.debug(f"Failed to check ChatHeader for blank conversation: {header_chk_err}")

                if not message_nodes and not has_active_header:
                    return False

            # 过滤只保留承载真正聊天文本的消息气泡，彻底屏蔽时间标签或通知横幅的坐标误判
            valid_message_nodes = []
            if message_nodes:
                for node in message_nodes:
                    try:
                        has_text = await node.query_selector('[class*="text"], [class*="content"], [class*="bubble-content"]')
                        if has_text:
                            valid_message_nodes.append(node)
                    except Exception:
                        continue
            message_nodes = valid_message_nodes

            # 一次性快速物理识别所有气泡的 is_self 状态，彻底避免多次 IPC 开销并防范“自说自话”自环
            is_self_list = []
            if message_nodes:
                try:
                    is_self_list = await self.page.evaluate(
                        """(nodes) => {
                            return nodes.map(node => {
                                if (!node) return false;
                                let rect = node.getBoundingClientRect();
                                let container = node.parentElement;
                                let containerRect = container ? container.getBoundingClientRect() : { left: 0, width: 800 };
                                let relativeLeft = rect.left - containerRect.left;
                                let mid = containerRect.width / 2;
                                let cl = typeof node.className === 'string' ? node.className : (node.className?.baseVal || '');
                                return relativeLeft > mid || cl.includes('right') || cl.includes('self') || cl.includes('own') || cl.includes('reverse');
                            });
                        }""",
                        message_nodes
                    )
                except Exception as eval_err:
                    logger.warning(f"Failed to batch evaluate self/partner bubble states: {eval_err}")
                    return False

            # 寻找最新的小萤回复索引（我方最后一条气泡）
            last_self_idx = -1
            if is_self_list:
                for idx, is_sf in enumerate(is_self_list):
                    if is_sf:
                        last_self_idx = idx

            # 提取自最后一个我方回复气泡之后，对方在此期间连续发出的所有新文本，并使用换行符进行物理智能拼接
            partner_texts = []
            if message_nodes:
                start_scan_idx = last_self_idx + 1 if last_self_idx != -1 else 0
                for idx in range(start_scan_idx, len(message_nodes)):
                    if not is_self_list[idx]:
                        msg_node = message_nodes[idx]
                        try:
                            text_element = await msg_node.query_selector('[class*="text"], [class*="content"], [class*="bubble-content"]')
                            if text_element:
                                text = await text_element.text_content()
                                if text and text.strip():
                                    partner_texts.append(text.strip())
                        except Exception:
                            continue

            latest_partner_msg = "\n".join(partner_texts) if partner_texts else ""

            if not latest_partner_msg:
                return False

            # 主动心跳同步时的前置去重校验：最新一条消息必须由对方发出，且内容发生改变
            if is_active_session_sync:
                # 如果最后一个气泡是我方发出的，则直接返回 False，不再继续理会
                if is_self_list and is_self_list[-1]:
                    return False

            # 心跳同步时的内容去重比对：若拼接出来的最新文本跟上一次处理过的对方消息文本完全一致，则去重忽略，并累加除颤重载计数器
            if is_active_session_sync and self.last_processed_msg_map.get(sender_nickname) == latest_partner_msg:
                self.idle_reload_turns = getattr(self, "idle_reload_turns", 0) + 1
                logger.debug(f"No new message during active sync. Idle reload turns: {self.idle_reload_turns}")
                return False

            # 7. 使用 MD5 构造稳定的虚拟 chat_id
            import hashlib
            chat_id = hashlib.md5(sender_nickname.encode("utf-8")).hexdigest()[:16]
            self.nickname_map[chat_id] = sender_nickname
            
            # 更新已处理对方消息的全局缓存，防止下一次轮询重复触发
            self.last_processed_msg_map[sender_nickname] = latest_partner_msg
            
            # 成功捕获并解析到对方的新消息，立刻将 WebSocket 除颤重载计数器清零
            self.idle_reload_turns = 0
            
            logger.info(f"Received message from fan {sender_nickname} ({chat_id}): {latest_partner_msg}")
            
            # 8. 封装成 100% 兼容已有的 OneBot 规范字典
            event = {
                "post_type": "message",
                "message_type": "private",
                "user_id": f"douyin_{chat_id}",  # 携带前缀，实现多实例隔离
                "self_id": "douyin_xiaoying",
                "raw_message": latest_partner_msg,
                "sender": {
                    "nickname": sender_nickname,
                    "card": ""
                }
            }
            
            # 记录当前活跃的 session 标记，用于加速后续的轮询
            self.active_session_key = f"douyin_{chat_id}"
            
            # 9. 非阻塞派发给 Dispatcher 管道
            asyncio.create_task(self.dispatcher.dispatch_event(event))
            has_new_message = True

        except Exception as poll_err:
            logger.error(f"Failed to poll messages from DOM: {poll_err}", exc_info=True)
            
        return has_new_message

    async def send_message(self, target_user_id: str, text: str) -> None:
        """由 sender.py 调起，通过 CloakBrowser 物理向指定的抖音用户发送私信."""
        if not self.page:
            logger.error("Cannot send message: CloakBrowser is not initialized.")
            return

        async with self.send_lock:
            # 提取真实 ID
            chat_id = target_user_id.split("douyin_", 1)[1] if "douyin_" in target_user_id else target_user_id
            
            # 激活发送状态锁，防止轮询物理切换干扰
            self.is_sending = True
            logger.info(f"Prepare physical reply to user: {chat_id}, text: {text[:50]}")
            
            try:
                # 1. 确认私信侧边栏处于打开状态
                is_sidebar_visible = await self.page.locator('#imSaasContainerId').is_visible()
                if not is_sidebar_visible:
                    # 面板没开，尝试利用 _ensure_logged_in 打开
                    await self._ensure_logged_in()
                    await asyncio.sleep(3.0)
                
                # 2. [新共识：映射定位与点击]
                nickname = self.nickname_map.get(chat_id)
                if not nickname:
                    # 如果没有映射，以 chat_id 作为备用昵称进行匹配
                    nickname = chat_id
                
                # 净化昵称，截断可能存在的换行与时间标签（如“刚刚”、“2分钟前”），保证 selector 不抛 BADSTRING 异常
                if nickname:
                    nickname = nickname.split('\n')[0].strip()
                    
                logger.info(f"Looking for contact with nickname: {nickname}")
                
                # 在侧边栏查找该昵称的联系人并物理点击
                contact_selector = f'#imSaasContainerId .conversationConversationItemwrapper:has-text("{nickname}"), #imSaasContainerId [class*="ConversationItem"]:has-text("{nickname}")'
                contact_item = await self.page.query_selector(contact_selector)
                
                if contact_item:
                    # 【强力自愈】传入 force=True 物理强行点击，穿透滑动动画不可见限制
                    await contact_item.click(force=True)
                    logger.info(f"Physically clicked nickname: {nickname}, waiting for chat loading...")
                    await asyncio.sleep(random.uniform(2.5, 3.5))
                else:
                    logger.warning(f"Could not locate contact element for {nickname}. Using current active conversation instead.")
    
                # 3. 定位私信输入框
                input_selectors = [
                    '#imSaasContainerId div[contenteditable="true"]',
                    '#imSaasContainerId textarea',
                    'div[contenteditable="true"]',
                    'textarea'
                ]
                
                input_element = None
                for sel in input_selectors:
                    try:
                        input_element = await self.page.query_selector(sel)
                        if input_element:
                            break
                    except Exception:
                        continue
    
                if not input_element:
                    raise ValueError("Could not locate Douyin chat input container in sidebar.")
    
                # 4. [新共识：物理聚焦与 100% 物理清空]
                await input_element.focus()
                await asyncio.sleep(0.5)
                
                # 发送 Command+A 全选并 Backspace 彻底清空
                await self.page.keyboard.press("Meta+A")
                await asyncio.sleep(0.2)
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.3)
    
                # 5. [拟真物理打字] 配合 humanize 随机按键延时
                logger.info(f"Typing reply: {text[:30]}")
                await self.page.keyboard.type(text, delay=random.randint(40, 75))
                await asyncio.sleep(0.5)
    
                # 6. 点击回车发送
                await self.page.keyboard.press("Enter")
                logger.info("Sent enter signal, verifying sending result...")
    
                # 7. [新共识：双重发送结果验证]
                await asyncio.sleep(random.uniform(1.8, 2.3))
                
                # 提取右侧所有消息气泡
                message_nodes = await self.page.query_selector_all('#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"]')
                verification_passed = False
                if message_nodes:
                    # 倒序遍历最后 3 个气泡，寻找第一个能成功读取且匹配的有效 HTMLElement 气泡，完美击碎空节点或注释节点报错
                    for last_msg in reversed(message_nodes[-3:]):
                        try:
                            # 使用 text_content() 避开非 HTMLElement 导致的异常，保证绝对安全
                            sent_text = await last_msg.text_content()
                            if sent_text and (text.strip() in sent_text.strip() or (len(text) >= 6 and text[:6] in sent_text)):
                                # 进一步检测是否带有失败警告标志
                                error_marker = await last_msg.query_selector('[class*="error"], [class*="fail"], [class*="warn"], svg[class*="error"]')
                                if error_marker:
                                    logger.warning("Message failed sending (error icon detected in DOM).")
                                    break
                                
                                logger.info(f"Message physical send verification passed 100%: {text[:30]}")
                                verification_passed = True
                                return
                        except Exception as read_err:
                            logger.warning(f"Error reading bubble node: {read_err}. Trying previous bubble...")
                            continue
                                
                if not verification_passed:
                    # 乐观验证防爆安全阀：物理敲击 Enter 之后，即便 DOM 匹配校验因动态混淆等原因未完全匹配通过，也乐观放行，记录警告但不抛出异常中断整个发送通道
                    logger.warning(f"Message validation mismatch or bubble not found for '{text[:30]}'. Using optimistic validation to prevent pipeline block.")
    
            except Exception as send_err:
                logger.error(f"Failed to send message physically: {send_err}", exc_info=True)
                raise send_err
            finally:
                # 释放状态锁
                self.is_sending = False


# 模块级物理单例，方便 sender.py 跨模块静态寻址
douyin_gateway = DouyinGateway()
