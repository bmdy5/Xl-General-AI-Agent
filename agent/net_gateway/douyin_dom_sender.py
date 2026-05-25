# -*- coding: utf-8 -*-
"""抖音网关 - DOM 消息发送模块"""

import asyncio
import logging
import re

logger = logging.getLogger("net_gateway.douyin.sender")


class DouyinDomSender:
    """抖音私信 DOM 发送器。"""

    def __init__(self):
        self.is_sending = False
        self._send_lock = asyncio.Lock()
        self._active_count = 0

    async def send_message(self, page, target_user_id: str, text: str,
                           nickname_map: dict, ensure_logged_in_cb) -> bool:
        if not page:
            return False

        # CQ 码过滤
        if "[CQ:record" in text or "base64://" in text:
            text = "[收到语音消息]"
        elif "[CQ:image" in text:
            text = "[收到图片消息]"
        else:
            text = re.sub(r'\[CQ:[^\]]+\]', '', text).strip()
        if not text:
            return False

        self._active_count += 1
        self.is_sending = True

        async with self._send_lock:
            chat_id = target_user_id.split("douyin_", 1)[1] if "douyin_" in target_user_id else target_user_id
            nickname = nickname_map.get(target_user_id) or nickname_map.get(chat_id) or chat_id
            nickname = nickname.split('\n')[0].strip()

            logger.info(f"发送私信给 {nickname}: {text[:50]}")

            try:
                # 1. 确保面板可见
                sidebar_ok = await page.locator('#imSaasContainerId').is_visible()
                if not sidebar_ok:
                    await ensure_logged_in_cb()
                    await asyncio.sleep(2)

                # 2. 点击联系人
                contact = page.locator(f'*:has-text("{nickname}")').first
                try:
                    if await contact.is_visible():
                        await contact.click(timeout=3000)
                        await asyncio.sleep(2)
                except Exception:
                    logger.warning(f"未找到联系人 {nickname}，沿用当前会话")

                # 3. 找到输入框 (contenteditable div)
                input_locator = page.locator('div[contenteditable="true"]').first
                await input_locator.click(timeout=3000)
                await asyncio.sleep(0.2)

                # 4. 清空 (Meta+A → Backspace)
                await page.keyboard.press("Meta+A")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.1)

                # 5. 键盘输入 (触发 React onChange → 激活发送按钮)
                await page.keyboard.type(text, delay=20)
                await asyncio.sleep(0.3)

                # 6. 点击发送按钮 (此时 React 已激活)
                send_btn = page.locator('div:has-text("发送")').last
                try:
                    await send_btn.click(timeout=3000)
                    logger.info(f"点击发送按钮成功: {text[:30]}")
                except Exception:
                    logger.warning("点击发送按钮失败，尝试 Enter")
                    await page.keyboard.press("Enter")

                await asyncio.sleep(1.0)
                logger.info(f"发送完成: {text[:30]}")
                return True

            except Exception as e:
                logger.error(f"发送失败: {e}", exc_info=True)
                try:
                    await page.screenshot(path="/tmp/douyin_send_error.png")
                except Exception:
                    pass
                return False
            finally:
                self._active_count -= 1
                if self._active_count <= 0:
                    self.is_sending = False
                    self._active_count = 0
