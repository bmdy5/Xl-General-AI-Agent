# -*- coding: utf-8 -*-
"""抖音网关 - DOM 轮询与消息提取模块"""

import asyncio
import hashlib
import logging
import random

logger = logging.getLogger("net_gateway.douyin.poller")

DOUYIN_HOME_URL = "https://www.douyin.com"

# ── JS 探针: 定位私信容器 (#imSaasContainerId 或 Popover) ──
FIND_CONTAINER_JS = r"""() => {
    let sidebar = document.querySelector('#imSaasContainerId');
    if (sidebar && sidebar.offsetHeight > 100) return { type: 'im_page' };

    let best = null;
    let bestScore = 0;
    let divs = document.querySelectorAll('div');
    for (let div of divs) {
        let style = window.getComputedStyle(div);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        let w = div.offsetWidth, h = div.offsetHeight;
        if (w < 260 || w > 520 || h < 200) continue;

        let text = div.textContent || '';
        let score = 0;
        if (text.includes('发送消息')) score += 10;
        if (text.includes('关闭会话')) score += 5;
        if (text.includes('私信')) score += 3;
        if (/(分钟前|小时前|昨天|天前|在线)/.test(text)) score += 2;

        if (score > bestScore) { bestScore = score; best = div; }
    }
    if (best && bestScore >= 5) return { type: 'popover' };
    return null;
}"""

# ── JS 探针: 在 container 内找联系人并点击 ──
PROBE_CONTACTS_JS = r"""(onlyPassive) => {
    let container = null;
    let sidebar = document.querySelector('#imSaasContainerId');
    if (sidebar && sidebar.offsetHeight > 100) { container = sidebar; }
    else {
        let best = null, bestScore = 0;
        let divs = document.querySelectorAll('div');
        for (let div of divs) {
            let style = window.getComputedStyle(div);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            let w = div.offsetWidth, h = div.offsetHeight;
            if (w < 260 || w > 520 || h < 200) continue;
            let text = div.textContent || '';
            let score = 0;
            if (text.includes('发送消息')) score += 10;
            if (text.includes('关闭会话')) score += 5;
            if (text.includes('私信')) score += 3;
            if (/(分钟前|小时前|昨天|天前)/.test(text)) score += 2;
            if (score > bestScore) { bestScore = score; best = div; }
        }
        if (best && bestScore >= 5) container = best;
    }
    if (!container) return { found: false, reason: 'no_container' };

    // 找未读红点
    let unreadEl = null;
    if (!onlyPassive) {
        let badges = container.querySelectorAll('[class*="unread"], [class*="badge"], [class*="red-dot"]');
        for (let b of badges) {
            let t = (b.innerText || '').trim();
            if (t && /^[0-9]+$/.test(t)) { unreadEl = b; break; }
        }
    }

    // 回溯找到联系人容器
    let contactEl = null;
    let isFallback = false;
    if (unreadEl) {
        let p = unreadEl.parentElement;
        while (p && p !== container && p !== document.body) {
            if (p.offsetHeight > 40 && p.offsetHeight < 120 && p.offsetWidth > 200) {
                contactEl = p; break;
            }
            p = p.parentElement;
        }
    }

    // 兜底: 取第一个有效联系人
    if (!contactEl) {
        let items = container.querySelectorAll('div');
        for (let item of items) {
            if (item.offsetHeight < 40 || item.offsetHeight > 120 || item.offsetWidth < 200) continue;
            let text = (item.innerText || '').trim();
            if (!text || text.length > 80) continue;
            // 跳过系统通知和推广
            if (/发布视频|作品数据|创作者|剪映|直播|开直播|管理作品/.test(text)) continue;
            contactEl = item;
            isFallback = true;
            break;
        }
    }

    if (!contactEl) return { found: false, reason: 'no_contact' };

    // 提取昵称: 取第一行非数字非时间的文本
    let nickname = '抖音粉丝';
    let lines = (contactEl.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    for (let line of lines) {
        if (/^[0-9]+$/.test(line)) continue;
        if (/(分钟前|小时前|昨天|天前|秒前|星期|月[0-9])/.test(line)) continue;
        if (line.length > 25) continue;
        nickname = line;
        break;
    }

    // 点击联系人
    let clicked = false;
    if (!onlyPassive) {
        try { contactEl.click(); clicked = true; } catch(e) {}
    }

    return { found: true, nickname, isFallback, clicked };
}"""

# ── JS 探针: 从 container 提取气泡 ──
EXTRACT_BUBBLES_JS = r"""() => {
    let container = document.querySelector('#imSaasContainerId');
    if (!container) {
        let best = null, bestScore = 0;
        let divs = document.querySelectorAll('div');
        for (let div of divs) {
            let style = window.getComputedStyle(div);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            let w = div.offsetWidth, h = div.offsetHeight;
            if (w < 260 || w > 520 || h < 200) continue;
            let text = div.textContent || '';
            let score = 0;
            if (text.includes('发送消息')) score += 10;
            if (text.includes('关闭会话')) score += 5;
            if (/(分钟前|小时前|昨天)/.test(text)) score += 2;
            if (score > bestScore) { bestScore = score; best = div; }
        }
        if (best && bestScore >= 5) container = best;
    }
    if (!container) return [];

    let containerRect = container.getBoundingClientRect();
    let mid = containerRect.width / 2;
    let allDivs = container.querySelectorAll('div');
    let rows = [];

    for (let div of allDivs) {
        let rect = div.getBoundingClientRect();
        let top = rect.top - containerRect.top;
        let w = div.offsetWidth, h = div.offsetHeight;

        // 排除 header (顶部 50px) 和 input 区 (底部 80px)
        if (top < 50 || top > containerRect.height - 80) continue;
        if (h < 16 || w < 50) continue;

        let text = (div.innerText || '').trim();
        if (!text || text.length < 1 || text.length > 200) continue;

        // 取第一行作为消息文字 (去掉 点赞/回复/删除 等按钮)
        let firstLine = text.split('\n')[0].trim();
        if (!firstLine || firstLine.length < 1) continue;

        // 跳过纯 UI 噪声
        if (/^(点赞|回复|删除|举报|刚刚|在线)$/.test(firstLine)) continue;
        if (/^\d{2}:\d{2}$/.test(firstLine)) continue;
        if (/^\d+分钟前$/.test(firstLine)) continue;
        if (/^\d+小时前$/.test(firstLine)) continue;
        if (firstLine === '昨天' || firstLine === '发送消息' || firstLine === '关闭会话') continue;

        let left = rect.left - containerRect.left;
        let isSelf = left + rect.width / 2 > mid;

        // 垂直 5px 粒度去重，同一行同方向只保留第一行文本最长的
        let rowKey = Math.round(top / 5) * 5;
        let dup = rows.find(r => Math.abs(r.row - rowKey) < 3 && r.isSelf === isSelf);
        if (dup) {
            if (firstLine.length > dup.text.length) dup.text = firstLine;
            continue;
        }
        rows.push({ text: firstLine, isSelf, row: rowKey });
    }

    rows.sort((a, b) => a.row - b.row);
    return rows.map(r => ({ text: r.text, isSelf: r.isSelf }));
}"""


class DouyinDomPoller:
    """抖音私信 DOM 轮询器。"""

    def __init__(self):
        self.idle_reload_turns = 0

    # ── 容器检测 ──

    async def _find_container(self, page) -> dict | None:
        """定位当前私信容器。"""
        if not page:
            return None
        try:
            return await page.evaluate(FIND_CONTAINER_JS)
        except Exception:
            return None

    async def _container_visible(self, page) -> bool:
        c = await self._find_container(page)
        return c is not None

    # ── 登录与面板就绪 ──

    async def ensure_logged_in(self, page, report_qrcode_cb) -> bool:
        """确保已登录且私信面板已打开。"""
        if not page:
            return False

        url = page.url
        if "login" in url or "sso.douyin" in url:
            await self._handle_login_self_healing(page, report_qrcode_cb)
            return False

        if await self._container_visible(page):
            return True

        # 检测登录态 (有头像即认为已登录)
        avatar = await page.query_selector('.dy-avatar, [class*="avatar"]')
        if not avatar:
            return False

        logger.info("[登录验证] 点击私信入口打开面板...")
        btn = page.locator('[data-e2e="im-entry"]').first
        try:
            if await btn.is_visible():
                await btn.click(timeout=2000)
        except Exception:
            try:
                await page.evaluate(
                    """() => { let b = document.querySelector('[data-e2e="im-entry"]'); if (b) b.click(); }"""
                )
            except Exception:
                pass

        # 等待面板渲染
        await asyncio.sleep(0.6)
        for _ in range(10):
            if await self._container_visible(page):
                logger.info("私信面板已打开")
                return True
            await asyncio.sleep(0.6)

        return False

    async def _handle_login_self_healing(self, page, report_qrcode_cb) -> None:
        """扫码自愈流程。"""
        logger.warning("[登录失效] 抖音登录态过期，启动扫码自愈...")
        try:
            await page.goto(DOUYIN_HOME_URL)
            await asyncio.sleep(4)
        except Exception as e:
            logger.error(f"Failed to load homepage for healing: {e}")
            return

        local_path = "/tmp/douyin_login_qrcode.png"
        selectors = [
            'div[class*="login-guide-container"] iframe',
            'div[class*="qrcode"]', 'canvas',
            'img[src*="qrcode"]', '[class*="qrcode-image"]', 'div[class*="qr-code"]'
        ]
        qr_el = None
        for sel in selectors:
            try:
                qr_el = await page.query_selector(sel)
                if qr_el:
                    break
            except Exception:
                continue

        try:
            if qr_el:
                await qr_el.screenshot(path=local_path)
            else:
                await page.screenshot(path=local_path)
            msg = "[抖音小萤扫码自愈提示]\n亮哥！我的抖音登录态失效啦，请在手机上扫码授权我重新恢复灵魂交互吧"
            await report_qrcode_cb(local_path, msg)
        except Exception as e:
            logger.error(f"Failed to capture/send QR code: {e}")

        logger.info("QR code reported. Waiting for scan...")

    # ── 消息轮询 ──

    async def poll_messages(self, page, is_sending: bool, is_first_poll: bool,
                            nickname_map: dict, last_processed_msg_map: dict,
                            active_session_key: str) -> list:
        """扫描私信面板，提取未回复的粉丝消息。"""
        if not page:
            return []

        only_passive = is_sending

        # F5 假死重载
        if self.idle_reload_turns >= 2:
            logger.info("触发 F5 假死重载自愈...")
            try:
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(6)
            except Exception as e:
                logger.error(f"F5 reload failed: {e}")
            self.idle_reload_turns = 0
            return []

        if not await self._container_visible(page):
            return []

        # Phase 1: 探针扫描联系人并点击
        probe = await page.evaluate(PROBE_CONTACTS_JS, only_passive)
        if not probe or not probe.get("found"):
            logger.info(f"未找到联系人: {probe}")
            return []

        nickname = probe.get("nickname", "抖音粉丝")
        clicked = probe.get("clicked", False)
        is_fallback = probe.get("isFallback", False)
        logger.info(f"找到联系人: {nickname} (clicked={clicked}, fallback={is_fallback})")

        # 广告过滤
        ad_keywords = ["发布视频", "管理作品", "数据", "创作者", "剪映", "开直播", "直播"]
        if len(nickname) > 25 or any(k in nickname for k in ad_keywords):
            logger.info(f"[广告过滤] 忽略: {nickname[:15]}...")
            return []

        # Phase 2: 等待聊天加载后提取气泡
        if clicked:
            logger.info(f"等待聊天加载...")
            await asyncio.sleep(random.uniform(2.0, 3.0))

        bubbles = await page.evaluate(EXTRACT_BUBBLES_JS)
        logger.info(f"提取到 {len(bubbles)} 个气泡")
        if not bubbles:
            # 诊断: dump 容器的文本结构
            try:
                dump = await page.evaluate(r"""() => {
                    let c = document.querySelector('#imSaasContainerId');
                    if (!c) {
                        let best = null, bestScore = 0;
                        let divs = document.querySelectorAll('div');
                        for (let d of divs) {
                            let s = window.getComputedStyle(d);
                            if (s.display === 'none' || s.visibility === 'hidden') continue;
                            let w = d.offsetWidth, h = d.offsetHeight;
                            if (w < 260 || w > 520 || h < 200) continue;
                            let t = d.textContent || '';
                            let sc = 0;
                            if (t.includes('发送消息')) sc += 10;
                            if (t.includes('关闭会话')) sc += 5;
                            if (/(分钟前|小时前|昨天)/.test(t)) sc += 2;
                            if (sc > bestScore) { bestScore = sc; best = d; }
                        }
                        if (best && bestScore >= 5) c = best;
                    }
                    if (!c) return {found: false};
                    // 收集子 div 的摘要
                    let kids = [];
                    let divs = c.querySelectorAll('div');
                    for (let d of divs) {
                        if (d.offsetHeight < 10) continue;
                        let t = (d.innerText || '').trim().substring(0, 60);
                        if (t) kids.push({h: d.offsetHeight, w: d.offsetWidth, t});
                    }
                    return {found: true, totalDivs: divs.length, kids: kids.slice(0, 20)};
                }""")
                logger.info(f"容器诊断: {dump}")
                await page.screenshot(path="/tmp/douyin_no_bubbles.png")
            except Exception as e:
                logger.error(f"诊断失败: {e}")
            return []

        # ChatHeader 昵称纠正 — 暂禁用，probe 提取的昵称更可靠

        # 找最后一条自己发的消息，提取之后的所有对方消息
        last_self = -1
        for i, b in enumerate(bubbles):
            if b["isSelf"]:
                last_self = i

        partner_msgs = []
        start = last_self + 1 if last_self >= 0 else 0
        for i in range(start, len(bubbles)):
            if not bubbles[i]["isSelf"]:
                partner_msgs.append(bubbles[i]["text"])

        latest = "\n".join(partner_msgs) if partner_msgs else ""
        if not latest:
            return []

        # 去重
        prev = last_processed_msg_map.get(nickname)
        if prev == latest:
            self.idle_reload_turns += 1
            return []

        chat_id = hashlib.md5(nickname.encode()).hexdigest()[:16]
        nickname_map[chat_id] = nickname
        nickname_map[f"douyin_{chat_id}"] = nickname
        last_processed_msg_map[nickname] = latest
        self.idle_reload_turns = 0

        logger.info(f"收到粉丝消息 {nickname}({chat_id}): {latest[:80]}")

        return [{
            "post_type": "message",
            "message_type": "private",
            "user_id": f"douyin_{chat_id}",
            "self_id": "douyin_xiaoying",
            "raw_message": latest,
            "sender": {"nickname": nickname, "card": ""}
        }]
