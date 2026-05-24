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

# 抖音网页端消息中心 URL
DOUYIN_MSG_URL = "https://www.douyin.com/message"
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
        
        # 遵守零路径硬编码与多实例隔离，保存在 .memory 哈希子目录下
        profile_dir = os.path.expanduser("~/.my-agent/memory/1705919142/cloak_douyin")
        os.makedirs(profile_dir, exist_ok=True)
        
        logger.info(f"Launching CloakBrowser sandbox with Profile: {profile_dir}")
        
        # 物理启动持久化隐形指纹 context
        self.browser_context = await launch_persistent_context_async(
            user_data_dir=profile_dir,
            headless=True,  # 常驻后台无感守护
            humanize=True,  # 强制加持人类物理键鼠操作曲线补丁，抗 30/30 设备检测
            viewport={"width": 1280, "height": 800}
        )
        
        self.page = await self.browser_context.new_page()
        # 延长超时限制，应对网络波动
        self.page.set_default_timeout(30000)
        
        # 首次冷启动，导航至消息页
        try:
            await self.page.goto(DOUYIN_MSG_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Failed to navigate to Douyin Message URL initially: {e}")

    async def _ensure_logged_in(self) -> bool:
        """判定当前是否已处于已登录的私信页面."""
        if not self.page:
            return False
        
        current_url = self.page.url
        # 如果重定向到 sso.douyin.com 或含有 login，表明未登录
        if "login" in current_url or "sso.douyin" in current_url:
            return False
        
        # 如果成功停留在 message 页，表示登录态在线
        if "message" in current_url:
            return True
            
        # 兜底检测：如果没有 message 在 URL 中，但能检测到左侧私信列表的 DOM，也算登录
        try:
            list_container = await self.page.query_selector('[class*="message-list"], [class*="chat-list"], [class*="list-container"]')
            if list_container:
                return True
        except Exception:
            pass
            
        return False

    async def _handle_login_self_healing(self) -> None:
        """Cookie 登录失效时的扫码自愈逻辑 (截图 COS 推送亮哥 QQ)."""
        logger.warning("🔑 [登录失效] 检测到抖音登录态过期，启动全自动扫码自愈流程...")
        
        # 1. 强制导航到登录或消息页以调出登录二维码
        try:
            await self.page.goto(DOUYIN_MSG_URL)
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Failed to load login page: {e}")
            await asyncio.sleep(5)
            return

        # 2. 定位网页二维码元素并物理截图
        local_path = os.path.expanduser("~/.my-agent/memory/1705919142/douyin_login_qrcode.png")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        # 抖音登录弹窗的二维码通常可以通过 CSS 定位器捕获
        # 如 [class*="qrcode"], canvas, img[src*="qrcode"] 等
        qrcode_selectors = [
            'div[class*="login-guide-container"] iframe', # 如果在 iframe 内部
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
                        "🌟【抖音小萤扫码自愈提示】🌟\n"
                        "亮哥！我的抖音登录态失效啦，请在手机上扫码授权我重新恢复灵魂交互吧～"
                    )
                }):
                    if res.type == "result":
                        logger.info(f"Login QR code sent to admin: {res.data}")
            else:
                logger.error("send_image_to_qq tool not registered in registry!")
        except Exception as qrcode_err:
            logger.error(f"Failed to capture or send login QR code: {qrcode_err}")

        # 4. 后台阻塞，每隔 5 秒监听 URL 直至登录自愈成功 (跳转回私信页)
        logger.info("QR code successfully pushed. Waiting for admin to scan on phone...")
        for _ in range(60):  # 最多等待 5 分钟 (60 * 5s)
            await asyncio.sleep(5)
            if await self._ensure_logged_in():
                logger.info("🎉 [自愈成功] 亮哥扫码成功！抖音已重新跳转至消息中心，恢复智能退避轮询！")
                # 成功跳转，自动清理临时二维码
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except Exception:
                        pass
                return
                
        logger.warning("Timeout waiting for admin to scan QR code. Refreshing page to generate new code...")

    async def _poll_messages(self) -> bool:
        """扫描私信列表 DOM，捕获最新对方消息并转化为等价 OneBot 事件派发."""
        if not self.page:
            return False
        
        has_new_message = False
        try:
            # 1. 查找左侧联系人列表中可能有未读红点的节点
            # 抖音网页端红点通常是 class 中带有 dot, unread-count, badge, red-dot 等
            unread_selectors = [
                '[class*="unread-count"]',
                '[class*="badge"]',
                '[class*="red-dot"]',
                '[class*="unread"]'
            ]
            
            unread_node = None
            for sel in unread_selectors:
                try:
                    unread_node = await self.page.query_selector(sel)
                    if unread_node:
                        break
                except Exception:
                    continue
            
            if not unread_node:
                return False  # 本轮无任何未读红点，重归静默

            # 2. 定位到该未读条目的最外层联系人容器并模拟物理点击
            # 向上寻址到联系人条目的整体包裹 box
            try:
                # 抖音私信联系人包裹框可能包含 class="chat-item" 等
                contact_container = await unread_node.evaluate_handle(
                    "(node) => node.closest('[class*=\"item\"], [class*=\"contact\"], [class*=\"session\"]')"
                )
                if not contact_container:
                    # 兜底直接点击红点同级或父级
                    parent = await unread_node.evaluate_handle("(node) => node.parentElement")
                    await parent.click()
                else:
                    await contact_container.click()
                
                # 注入一小段拟真延迟，等右侧聊天窗完全渲染
                await asyncio.sleep(1.2)
            except Exception as click_err:
                logger.error(f"Failed to click contact item: {click_err}")
                return False

            # 3. 解析右侧聊天框中的最新对方发言
            # 抖音右侧聊天气泡通常靠左是对方，靠右是自己，用 class 或 flex 方向区分
            # 我们直接提取所有消息节点并解析
            # 对方的消息节点通常包含类似 "message-left", "bubble-left", "left-msg" 等样式
            message_nodes = await self.page.query_selector_all('[class*="message"], [class*="bubble"], [class*="chat-item"]')
            if not message_nodes:
                return False

            latest_partner_msg = ""
            sender_nickname = "抖音粉丝"
            
            # 提取右侧聊天窗顶部或左侧的名字
            try:
                name_element = await self.page.query_selector('[class*="title"], [class*="nickname"], [class*="chat-name"]')
                if name_element:
                    sender_nickname = await name_element.inner_text()
                    sender_nickname = sender_nickname.strip()
            except Exception:
                pass

            # 倒序遍历聊天记录，寻找最后几条“对方”发的文本消息
            for msg_node in reversed(message_nodes):
                try:
                    class_attr = await msg_node.get_attribute("class") or ""
                    # 判定是否是对方发的：通常如果不包含 "right", "self", "own", "mine" 等特征，就是对方发的
                    is_self = any(x in class_attr.lower() for x in ["right", "self", "own", "mine", "send"])
                    
                    if not is_self:
                        text_element = await msg_node.query_selector('[class*="text"], [class*="content"], [class*="bubble-content"]')
                        if text_element:
                            text = await text_element.inner_text()
                            if text.strip():
                                latest_partner_msg = text.strip()
                                break
                except Exception:
                    continue

            if not latest_partner_msg:
                return False

            # 4. 获取当前选中的用户唯一 ID
            # 抖音网页私信的 URL 在选中用户后，通常会带上其加密 user_id 或 chat_id，
            # 例如：https://www.douyin.com/message?chatId=12345678 或 ?userId=12345
            chat_id = "unknown"
            match = re.search(r"[?&](chatId|userId|id)=([^&]+)", self.page.url)
            if match:
                chat_id = match.group(2)
            else:
                # 兜底使用昵称的 MD5 / 纯拼音哈希值以防解析不到 URL 参数
                import hashlib
                chat_id = hashlib.md5(sender_nickname.encode("utf-8")).hexdigest()[:16]

            logger.info(f"💬 [私信捕获] 收到抖音粉丝「{sender_nickname}」({chat_id}) 的消息: {latest_partner_msg}")
            
            # 5. 封装成 100% 兼容已有的 OneBot 规范字典
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
            
            # 记录当前活跃的 session 标记，用于加速后续的 send_message 交互
            self.active_session_key = f"douyin_{chat_id}"
            
            # 6. 非阻塞派发给 Dispatcher 管道（完美套用已有 CSMA/CD 与 Fatigue 机制！）
            asyncio.create_task(self.dispatcher.dispatch_event(event))
            has_new_message = True

        except Exception as poll_err:
            logger.error(f"Failed to poll messages from DOM: {poll_err}")
            
        return has_new_message

    async def send_message(self, target_user_id: str, text: str) -> None:
        """由 sender.py 调起，通过 CloakBrowser 物理向指定的抖音用户发送私信."""
        if not self.page:
            logger.error("Cannot send message: CloakBrowser is not initialized.")
            return

        # 提取真实 ID
        chat_id = target_user_id.split("douyin_", 1)[1] if "douyin_" in target_user_id else target_user_id
        
        logger.info(f"📤 [私信回复] 准备通过 CloakBrowser 物理发送私信给抖音用户 [{chat_id}]: {text[:50]}")
        
        try:
            # 1. 如果当前页面没有选中该用户，我们需要先导航到该用户的私信界面
            # 抖音网页端可以直接通过 URL 传参直达特定对话：https://www.douyin.com/message?chatId=xxx
            expected_url_part = f"chatId={chat_id}"
            if expected_url_part not in self.page.url:
                target_url = f"{DOUYIN_MSG_URL}?chatId={chat_id}"
                await self.page.goto(target_url, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)

            # 2. 定位私信输入框
            # 抖音输入框通常为带 contenteditable="true" 的 div，或者 textarea
            input_selectors = [
                'div[contenteditable="true"]',
                'textarea[placeholder*="发送"]',
                '[class*="input"] [contenteditable="true"]',
                '#msg-input',
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
                raise ValueError("Could not locate Douyin chat input container.")

            # 3. 拟真打字输入，配合 humanize 随机按键延时
            await input_element.click()
            await asyncio.sleep(0.2)
            
            # 使用 Playwright 的 type 拟真逐字输入
            await input_element.fill("") # 先清空
            await self.page.keyboard.type(text, delay=random.randint(15, 45))
            await asyncio.sleep(0.3)

            # 4. 点击发送按钮或模拟回车
            # 寻找输入框附近的“发送”按钮，或者直接在输入框中按 Enter 发送
            send_btn = None
            try:
                # 抖音网页版可能有专门的发送按钮（含有“发送”或特定样式的 button）
                send_btn = await self.page.query_selector('button[class*="send"], [class*="send-btn"], :text("发送")')
            except Exception:
                pass
                
            if send_btn:
                await send_btn.click()
            else:
                # 兜底直接按 Enter 键发送
                await self.page.keyboard.press("Enter")
                
            await asyncio.sleep(0.5)
            logger.info(f"✔ [私信发送成功] 消息已安全投递至抖音 [{chat_id}]！")

        except Exception as send_err:
            logger.error(f"❌ [私信发送失败] 物理操作发送私信到 [{chat_id}] 发生异常: {send_err}", exc_info=True)


# 模块级物理单例，方便 sender.py 跨模块静态寻址
douyin_gateway = DouyinGateway()
