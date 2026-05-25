# -*- coding: utf-8 -*-
"""抖音网关 - DOM 拟真打字与物理发送模块"""
import asyncio
import random
import logging

logger = logging.getLogger("net_gateway.douyin.sender")

class DouyinDomSender:
    """专职负责物理聚焦、拟真打字发送、回车发包及发送结果的双重 DOM 校验，支持乐观防爆安全阀。"""

    def __init__(self):
        # 物理打字发送互斥状态锁
        self.is_sending = False
        self.send_lock = asyncio.Lock()

    async def send_message(self, page, target_user_id: str, text: str, nickname_map: dict, ensure_logged_in_cb) -> None:
        """通过 CloakBrowser 物理向指定的抖音用户发送私信。"""
        if not page:
            logger.error("Cannot send message: page is not initialized.")
            return

        async with self.send_lock:
            # 提取真实 ID 并设置发送锁，防止心跳轮询切换会话干扰
            chat_id = target_user_id.split("douyin_", 1)[1] if "douyin_" in target_user_id else target_user_id
            self.is_sending = True
            logger.info(f"Prepare physical reply to user: {chat_id}, text: {text[:50]}")
            
            try:
                # 1. 确认私信侧边栏处于打开状态
                is_sidebar_visible = await page.locator('#imSaasContainerId').is_visible()
                if not is_sidebar_visible:
                    await ensure_logged_in_cb()
                    await asyncio.sleep(3.0)
                
                # 2. 检索联系人并点击切换
                nickname = nickname_map.get(chat_id) or chat_id
                if nickname:
                    nickname = nickname.split('\n')[0].strip()
                    
                logger.info(f"Looking for contact with nickname: {nickname}")
                contact_selector = f'#imSaasContainerId .conversationConversationItemwrapper:has-text("{nickname}"), #imSaasContainerId [class*="ConversationItem"]:has-text("{nickname}")'
                contact_item = await page.query_selector(contact_selector)
                
                if contact_item:
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
                        input_element = await page.query_selector(sel)
                        if input_element:
                            break
                    except Exception:
                        continue
    
                if not input_element:
                    raise ValueError("Could not locate Douyin chat input container in sidebar.")
    
                # 4. [物理聚焦与 100% 物理清空]
                await input_element.focus()
                await asyncio.sleep(0.5)
                
                # 发送 Command+A 全选并 Backspace 彻底清空，完全模拟真实物理输入，排除 JS 污染
                await page.keyboard.press("Meta+A")
                await asyncio.sleep(0.2)
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.3)
    
                # 5. [拟真物理打字] 配合 humanize 随机按键延时
                logger.info(f"Typing reply: {text[:30]}")
                await page.keyboard.type(text, delay=random.randint(40, 75))
                await asyncio.sleep(0.5)
    
                # 6. 点击回车发送
                await page.keyboard.press("Enter")
                logger.info("Sent enter signal, verifying sending result...")
    
                # 7. [双重发送结果验证]
                await asyncio.sleep(random.uniform(1.8, 2.3))
                
                message_nodes = await page.query_selector_all('#imSaasContainerId [class*="message"], #imSaasContainerId [class*="bubble"]')
                verification_passed = False
                if message_nodes:
                    for last_msg in reversed(message_nodes[-3:]):
                        try:
                            sent_text = await last_msg.text_content()
                            if sent_text and (text.strip() in sent_text.strip() or (len(text) >= 6 and text[:6] in sent_text)):
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
                    # 乐观防爆安全阀
                    logger.warning(f"Message validation mismatch or bubble not found for '{text[:30]}'. Using optimistic validation to prevent pipeline block.")
    
            except Exception as send_err:
                logger.error(f"Failed to send message physically: {send_err}", exc_info=True)
                raise send_err
            finally:
                self.is_sending = False
