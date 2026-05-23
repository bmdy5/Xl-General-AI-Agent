import asyncio
import json
import logging
import os
import re
import urllib.request
import aiohttp
from datetime import datetime

logger = logging.getLogger("net_gateway.sender")

NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000

class MessageSender:
    """OneBot HTTP 协议消息发送与流量控制管理器"""
    
    def __init__(self, bot):
        self.bot = bot
        
        # 初始化全局发包平滑流控令牌桶限流器（默认最大并发爆发5包，每1.5秒填充1包）
        from .bus import TokenBucketLimiter
        capacity = float(os.getenv("QQ_LIMITER_CAPACITY", "5.0"))
        refill_rate = float(os.getenv("QQ_LIMITER_REFILL_RATE", "0.67"))
        self.limiter = TokenBucketLimiter(capacity=capacity, refill_rate=refill_rate)

    async def send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """OneBot HTTP 协议消息发送，负责具体的网络包推送。"""
        # 0. 全局物理发包滑窗令牌桶平滑流控整流
        await self.limiter.acquire()

        # 2. 文本净化（QQ 不支持 Markdown 粗斜体渲染，在此进行自动降解）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)

        # 3. 构造发送 payload
        payload = {}
        endpoint = ""
        if msg_type == "group" and group_id:
            endpoint = "/send_group_msg"
            payload = {"group_id": int(group_id), "message": text}
        else:
            endpoint = "/send_private_msg"
            payload = {"user_id": int(user_id), "message": text}

        url = f"{NC_HTTP_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        try:
            # 采用异步 non-blocking 请求，并设置 5.0 秒超时限制防卡死
            if self.bot._http and not self.bot._http.closed:
                timeout = aiohttp.ClientTimeout(total=5.0)
                async with self.bot._http.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Message send failed ({resp.status}): {body[:100]}")
            else:
                # 兜底同步请求，防止 _http 被关闭时发生崩溃
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
                
            logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
            # 物理写入活动轨迹日志以实现输入与输出时间线完全融合
            session_key = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
            self.bot._log_activity("Agent回复", f"小萤 ({session_key}): {text}", user_id=user_id)
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def send_chunk(self, msg_type: str, user_id: str, group_id: str, text: str):
        """发送一个文本块，处理 [SPLIT] 和 [WAIT:N]"""
        wait = 0
        def _extract_wait(t):
            nonlocal wait
            m = re.search(r'\[WAIT:([\d.]+)\]', t)
            if m:
                wait = max(wait, float(m.group(1)))
                t = re.sub(r'\[WAIT:[\d.]+\]', '', t)
            return t

        parts = text.split("[SPLIT]")
        for i, part in enumerate(parts):
            part = _extract_wait(part.strip())
            if not part:
                continue
            if len(part) > MAX_REPLY_CHARS:
                part = part[:MAX_REPLY_CHARS - 20] + "\n...(truncated)"
            
            await self.bot._send(msg_type, user_id, group_id, part, skip_delay=True)
            if i < len(parts) - 1:
                if wait > 0:
                    await asyncio.sleep(wait)
                wait = 0
