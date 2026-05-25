# -*- coding: utf-8 -*-
"""抖音网关 - DOM 拟真轮询与消息提取模块"""
import re
import time
import random
import logging
import asyncio
import hashlib

logger = logging.getLogger("net_gateway.douyin.poller")

DOUYIN_HOME_URL = "https://www.douyin.com"

class DouyinDomPoller:
    """负责抖音网页端已登录状态验证、JS 联合爆破拉起私信侧边栏、未读红点与主动会话扫描、以及 JS 活体去重 DOM 提取。"""

    def __init__(self):
        # 闲置无新消息轮数计数器，用于触发 F5 假死重载
        self.idle_reload_turns = 0

    async def ensure_logged_in(self, page, report_qrcode_cb) -> bool:
        """判定当前是否处于已登录并打开了私信面板的状态。若失效自动触发扫码自愈。"""
        if not page:
            return False
        
        current_url = page.url
        if "login" in current_url or "sso.douyin" in current_url:
            await self._handle_login_self_healing(page, report_qrcode_cb)
            return False
        
        # 1. 优先使用私信面板可见性作为就绪标准 (兼容大页面和主页半高悬浮窗 - 基于物理特征探针)
        try:
            is_visible = await page.evaluate("""() => {
                let sidebar = document.querySelector('#imSaasContainerId');
                if (sidebar && sidebar.offsetHeight > 100) return true;
                
                let divs = document.querySelectorAll('div');
                for (let div of divs) {
                    if (div.offsetWidth > 150 && div.offsetWidth < 500 && div.offsetHeight > 150) {
                        let text = div.innerText || '';
                        if (
                            text.includes('前线') || 
                            text.includes('在线') || 
                            text.includes('天前') || 
                            text.includes('分钟') || 
                            text.includes('小时') ||
                            text.includes('昨天') || 
                            text.includes('今天') || 
                            text.includes('星期')
                        ) {
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if is_visible:
                return True
        except Exception:
            pass
            
        # 2. 面板不可见但主页显示登录（如头像存在），执行物理 Hover 并 Click 强力拉起私信
        avatar_element = None
        for avatar_sel in ['.dy-avatar', '[class*="avatar"]', '[class*="header"] [src*="avatar"]']:
            try:
                avatar_element = await page.query_selector(avatar_sel)
                if avatar_element:
                    break
            except Exception:
                continue
                
        if avatar_element:
            logger.info("[登录验证] 检测到登录头像。尝试物理拟真点击拉起右侧私信面板...")
            
            # 第一防线：Playwright 物理拟真 Hover + Click
            btn_selectors = [
                '[data-e2e="im-entry"]',
                'a[href*="message"]',
                '.igu2_FYl',
                'text=私信',
                '[class*="message"]'
            ]
            
            click_success = False
            for btn_sel in btn_selectors:
                try:
                    loc = page.locator(btn_sel).first
                    if await loc.is_visible():
                        # 物理 Hover 模拟真人
                        await loc.hover()
                        await asyncio.sleep(0.5)
                        # 物理 Click 触发 React 响应
                        await loc.click(force=True)
                        logger.info(f"Physically hovered and clicked private msg entry: {btn_sel}")
                        click_success = True
                        break
                except Exception as e:
                    logger.warning(f"Failed physical click on {btn_sel}: {e}")
                    continue
            
            # 第二防线：JS 联合爆破兜底（仅在物理点击未发生时）
            if not click_success:
                logger.warning("Physical click entries not visible. Invoking JS fallback click...")
                try:
                    js_clicked = await page.evaluate("""() => {
                        let btn = document.querySelector('[data-e2e="im-entry"]') || document.querySelector('.igu2_FYl');
                        if (btn) {
                            btn.click();
                            return true;
                        }
                        let all = document.querySelectorAll('*');
                        for (let el of all) {
                            if (el.innerText && el.innerText.trim() === '私信') {
                                el.click();
                                el.parentElement?.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    logger.info(f"JS fallback click result: {js_clicked}")
                except Exception as js_err:
                    logger.error(f"JS fallback click failed: {js_err}")
            
            # 3. 循环等待并校验私信面板是否真的显现 (基于物理特征探针)
            await asyncio.sleep(0.8)
            for _ in range(8):
                try:
                    is_visible = await page.evaluate("""() => {
                        let sidebar = document.querySelector('#imSaasContainerId');
                        if (sidebar && sidebar.offsetHeight > 100) return true;
                        
                        let divs = document.querySelectorAll('div');
                        for (let div of divs) {
                            if (div.offsetWidth > 150 && div.offsetWidth < 500 && div.offsetHeight > 150) {
                                let text = div.innerText || '';
                                if (
                                    text.includes('前线') || 
                                    text.includes('在线') || 
                                    text.includes('天前') || 
                                    text.includes('分钟') || 
                                    text.includes('小时') ||
                                    text.includes('昨天') || 
                                    text.includes('今天') || 
                                    text.includes('星期')
                                ) {
                                    return true;
                                }
                            }
                        }
                        return false;
                    }""")
                    if is_visible:
                        logger.info("🎉 ImSaas message panel or popover is successfully visible now!")
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                
        return False

    async def _handle_login_self_healing(self, page, report_qrcode_cb) -> None:
        """Cookie 登录失效时的扫码自愈流程"""
        logger.warning("[登录失效] 抖音登录态过期，启动扫码自愈...")
        try:
            await page.goto(DOUYIN_HOME_URL)
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"Failed to load homepage for healing: {e}")
            await asyncio.sleep(5)
            return

        local_path = "/tmp/douyin_login_qrcode.png"
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
                qrcode_element = await page.query_selector(sel)
                if qrcode_element:
                    logger.info(f"Located login QR code element via selector: {sel}")
                    break
            except Exception:
                continue
        
        try:
            if qrcode_element:
                await qrcode_element.screenshot(path=local_path)
            else:
                await page.screenshot(path=local_path)
                logger.warning("QR code element not found, screenshotted whole page as fallback.")

            # 通过回调将二维码报告给大主脑，由主脑调用公共发图工具推送给亮哥 QQ
            msg = "[抖音小萤扫码自愈提示]\n亮哥！我的抖音登录态失效啦，请在手机上扫码授权我重新恢复灵魂交互吧"
            await report_qrcode_cb(local_path, msg)
        except Exception as qrcode_err:
            logger.error(f"Failed to capture or send login QR code: {qrcode_err}")

        logger.info("QR code successfully reported. Waiting for scan...")

    async def poll_messages(self, page, is_sending: bool, is_first_poll: bool, nickname_map: dict, 
                            last_processed_msg_map: dict, active_session_key: str) -> list:
        """扫描主页侧边栏私信列表 DOM，捕获最新对方消息。返回待分发的事件 dict 列表。"""
        if not page:
            return []
        
        # 1. 物理动作防冲突：若正在发送中，开启“安全被动听觉轮询”，仅在当前活跃聊天窗口中监听，不进行切换
        only_passive_listen = False
        if is_sending:
            logger.info("In physical sending state. Passive monitoring without contact switching.")
            only_passive_listen = True
            
        # === [ WebSocket 假死重载除颤自愈 ] ===
        if self.idle_reload_turns >= 2:
            logger.info("ℹ️ Detected 2 consecutive idle turns. Triggering F5 reload to heal connection...")
            try:
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(6.0)
            except Exception as reload_err:
                logger.error(f"Failed to reload page for healing: {reload_err}")
            self.idle_reload_turns = 0
            return []
            
        try:
            # 兼容大页面和主页半高悬浮私信卡片 (基于高精度特征探针)
            is_visible = await page.evaluate("""() => {
                let sidebar = document.querySelector('#imSaasContainerId');
                if (sidebar && sidebar.offsetHeight > 100) return true;
                
                let divs = document.querySelectorAll('div');
                for (let div of divs) {
                    if (div.offsetWidth > 150 && div.offsetWidth < 500 && div.offsetHeight > 150) {
                        let text = div.innerText || '';
                        if (
                            text.includes('前线') || 
                            text.includes('在线') || 
                            text.includes('天前') || 
                            text.includes('分钟') || 
                            text.includes('小时') ||
                            text.includes('昨天') || 
                            text.includes('今天') || 
                            text.includes('星期')
                        ) {
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if not is_visible:
                return []
        except Exception:
            return []
        
        events = []
        try:
            # 2. 检索带有未读红点的节点 (加入 popover 及通用选择器)
            unread_selectors = [
                '[class*="unread-count"]',
                '[class*="badge"]',
                '[class*="red-dot"]',
                '[class*="unread"]',
                '#imSaasContainerId [class*="unread-count"]',
                '#imSaasContainerId [class*="badge"]',
                '#imSaasContainerId [class*="red-dot"]',
                '#imSaasContainerId [class*="unread"]'
            ]
            
            unread_node = None
            if not only_passive_listen:
                for sel in unread_selectors:
                    try:
                        unread_node = await page.query_selector(sel)
                        if unread_node:
                            break
                    except Exception:
                        continue
            
            contact_container = None
            is_active_session_sync = False
            
            if unread_node:
                # 3. 物理处理最上方的一个未读条目（JS 祖先特征智能回溯，兼容 popover）
                try:
                    contact_container = await unread_node.evaluate_handle(
                        """(node) => {
                            let p = node.parentElement;
                            while (p && p.id !== 'imSaasContainerId' && !p.className?.includes('popover')) {
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
                    logger.error(f"Ancestor traceback failed: {eval_err}")
            else:
                # 兜底：同步当前活跃聊天会话（列表第一项）
                try:
                    active_handle = await page.evaluate_handle(
                        """() => {
                            let items = document.querySelectorAll('#imSaasContainerId [class*="Item"], #imSaasContainerId [class*="wrapper"], [class*="popover"] [class*="Item"], [class*="popover"] [class*="wrapper"], div[class*="popover"] div[class*="Item"]');
                            for (let item of items) {
                                if (item.offsetHeight < 40 || item.offsetHeight > 120) continue;
                                let avatar = item.querySelector('img, [class*="avatar"]');
                                let text = item.querySelector('[class*="name"], [class*="nickname"], [class*="title"]');
                                if (avatar || text) return item;
                            }
                            return document.querySelector('#imSaasContainerId [class*="ConversationItem"], #imSaasContainerId [class*="Item"]') || document.querySelector('[class*="popover"] [class*="Item"]') || document.querySelector('div[class*="popover"] div[class*="Item"]');
                        }"""
                    )
                    if active_handle and hasattr(active_handle, "as_element") and active_handle.as_element():
                        active_item = active_handle.as_element()
                        if active_item:
                            contact_container = active_item
                            is_active_session_sync = True
                except Exception as active_err:
                    logger.warning(f"Failed to find first active contact: {active_err}")
 
            if not contact_container:
                return []
 
            # 4. 提取昵称
            sender_nickname = "抖音粉丝"
            try:
                name_element = await contact_container.query_selector('[class*="name"], [class*="nickname"], [class*="title"]')
                if name_element:
                    text_content = await name_element.inner_text()
                    if text_content.strip():
                        sender_nickname = text_content.strip().split('\n')[0].strip()
                
                if sender_nickname in ["抖音粉丝", "未知", ""]:
                    raw_text = await contact_container.inner_text()
                    if raw_text:
                        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
                        possible_names = []
                        for line in lines:
                            if line.isdigit() or any(t in line for t in ["分钟前", "小时前", "昨天", "前天", "星期", "秒前", "月"]):
                                continue
                            possible_names.append(line)
                        if possible_names:
                            sender_nickname = possible_names[0]
            except Exception as name_err:
                logger.warning(f"Failed to extract contact nickname: {name_err}")

            # 5. 切换会话
            if not only_passive_listen:
                logger.info(f"Physically switching conversation with sender: {sender_nickname}...")
                try:
                    try:
                        await contact_container.click(force=True, timeout=3000)
                    except Exception:
                        await contact_container.evaluate(
                            """(node) => {
                                node.click();
                                let target = node.querySelector('[class*="name"], [class*="nickname"], [class*="title"], img, [class*="avatar"]');
                                if (target) target.click();
                            }"""
                        )
                    await asyncio.sleep(random.uniform(2.5, 3.5))
                except Exception as click_err:
                    logger.error(f"Failed to click contact item: {click_err}")
                    return []

            # ChatHeader 真实昵称终极纠正
            try:
                header_nickname = await page.evaluate(
                    """() => {
                        let header = document.querySelector(
                            '#imSaasContainerId [class*="ChatHeader"], #imSaasContainerId [class*="chat-header"], #imSaasContainerId [class*="saasImHeader"], #imSaasContainerId [class*="header"], [class*="popover"] [class*="Header"], [class*="popover"] [class*="header"]'
                        );
                        if (header && header.innerText.trim()) {
                            let name = header.innerText.trim().split('\\n')[0];
                            if (name && name !== '私信' && name !== '消息') return name;
                        }
                        return null;
                    }"""
                )
                if header_nickname and header_nickname.strip():
                    sender_nickname = header_nickname.strip()
            except Exception:
                pass
 
            # 6. 解析最新对方发言 (支持大页面 #imSaasContainerId 与悬浮卡片 [class*="popover"])
            message_nodes = await page.query_selector_all(
                '#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"], #imSaasContainerId [class*="chat-item"], '
                '[class*="popover"] [class*="message"], [class*="popover"] [class*="bubble"], [class*="popover"] [class*="chat-item"]'
            )
            if not message_nodes:
                if is_active_session_sync and contact_container:
                    logger.info("Chat panel is blank initially. Activating via dual-track click...")
                    try:
                        await contact_container.click(force=True, timeout=3000)
                        await asyncio.sleep(random.uniform(3.0, 4.5))
                        message_nodes = await page.query_selector_all(
                            '#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"], #imSaasContainerId [class*="chat-item"], '
                            '[class*="popover"] [class*="message"], [class*="popover"] [class*="bubble"], [class*="popover"] [class*="chat-item"]'
                        )
                    except Exception:
                        pass
                if not message_nodes:
                    return []

            # 7. 去重与敌我气泡坐标判定提取
            bubbles_data = []
            try:
                bubbles_data = await page.evaluate(
                    """() => {
                        let container = document.querySelector('#imSaasContainerId') || document.querySelector('[class*="popover"]') || document.body;
                        if (!container) return [];
                        let rawNodes = Array.from(container.querySelectorAll('[class*="message"], [class*="bubble"], [class*="chat-item"]'));
                        let uniqueNodes = rawNodes.filter((node, index) => {
                            return !rawNodes.some((otherNode, otherIndex) => {
                                return otherIndex !== index && otherNode.contains(node);
                            });
                        });
                        let result = [];
                        for (let node of uniqueNodes) {
                            let textEl = node.querySelector('[class*="text"], [class*="content"], [class*="bubble-content"]');
                            if (!textEl) continue;
                            let text = (textEl.innerText || textEl.textContent || '').trim();
                            if (!text) continue;
                            
                            let rect = node.getBoundingClientRect();
                            let parent = node.parentElement;
                            let parentRect = parent ? parent.getBoundingClientRect() : { left: 0, width: 800 };
                            let relativeLeft = rect.left - parentRect.left;
                            let mid = parentRect.width / 2;
                            let cl = typeof node.className === 'string' ? node.className : (node.className?.baseVal || '');
                            let isSelf = relativeLeft > mid || cl.includes('right') || cl.includes('self') || cl.includes('own') || cl.includes('reverse');
                            
                            result.push({ text: text, isSelf: isSelf });
                        }
                        return result;
                    }"""
                )
            except Exception as eval_err:
                logger.error(f"Failed to batch extract bubbles: {eval_err}")
                return []

            # 8. 寻找最新的小萤回复索引
            last_self_idx = -1
            for idx, bubble in enumerate(bubbles_data):
                if bubble["isSelf"]:
                    last_self_idx = idx

            # 9. 提取我方最后回复之后，对方发出的所有新文本，进行换行符物理智能拼接
            partner_texts = []
            start_scan_idx = last_self_idx + 1 if last_self_idx != -1 else 0
            for idx in range(start_scan_idx, len(bubbles_data)):
                if not bubbles_data[idx]["isSelf"]:
                    partner_texts.append(bubbles_data[idx]["text"])

            latest_partner_msg = "\n".join(partner_texts) if partner_texts else ""
            if not latest_partner_msg:
                return []

            if is_active_session_sync:
                if bubbles_data and bubbles_data[-1]["isSelf"]:
                    return []

            if is_active_session_sync and sender_nickname not in last_processed_msg_map:
                last_processed_msg_map[sender_nickname] = latest_partner_msg
                if is_first_poll:
                    logger.info(f"Initialized active session message for legacy user: {sender_nickname}. Prevented legacy reply.")
                    return []
                else:
                    logger.info(f"New session detected for user: {sender_nickname} after init. Proceeding.")

            # 严格去重过滤：无论是否活跃同步，只要消息与上一次完全相同，就去重退避，坚决防止重复回复
            if last_processed_msg_map.get(sender_nickname) == latest_partner_msg:
                self.idle_reload_turns += 1
                logger.info(f"ℹ️ [严格去重拦截] 检测到粉丝 {sender_nickname} 消息无变化，自动退避，防止重复回复。")
                return []

            chat_id = hashlib.md5(sender_nickname.encode("utf-8")).hexdigest()[:16]
            nickname_map[chat_id] = sender_nickname
            last_processed_msg_map[sender_nickname] = latest_partner_msg
            self.idle_reload_turns = 0
            
            logger.info(f"Received message from fan {sender_nickname} ({chat_id}): {latest_partner_msg}")
            
            # 返回标准的 OneBot 消息事件 dict
            event = {
                "post_type": "message",
                "message_type": "private",
                "user_id": f"douyin_{chat_id}",
                "self_id": "douyin_xiaoying",
                "raw_message": latest_partner_msg,
                "sender": {
                    "nickname": sender_nickname,
                    "card": ""
                }
            }
            events.append(event)

        except Exception as poll_err:
            logger.error(f"Failed to poll messages: {poll_err}", exc_info=True)
            
        return events
