import asyncio
import json
import logging
import os
import re
import time
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
        self.douyin_fail_count = 0
        
        capacity = float(os.getenv("QQ_LIMITER_CAPACITY", "5.0"))
        refill_rate = float(os.getenv("QQ_LIMITER_REFILL_RATE", "0.67"))
        self.limiter = TokenBucketLimiter(capacity=capacity, refill_rate=refill_rate)

    async def _get_douyin_nickname(self, user_id: str) -> str:
        """从网关获取当前用户的昵称"""
        try:
            url = f"http://127.0.0.1:{os.getenv('DOUYIN_PORT', '9000')}/get_nickname?user_id={user_id}"
            if self.bot._http and not self.bot._http.closed:
                timeout = aiohttp.ClientTimeout(total=3.0)
                async with self.bot._http.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "success":
                            return data.get("nickname") or ""
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=3.0) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("status") == "success":
                                return data.get("nickname") or ""
        except Exception as e:
            logger.error(f"Failed to get nickname from gateway: {e}")
        return ""

    async def _handle_douyin_fail(self, user_id: str, text: str):
        self.douyin_fail_count += 1
        logger.warning(f"Douyin sending failed. Consecutive count: {self.douyin_fail_count}")
        
        chat_id = user_id.split("douyin_", 1)[-1]
        nickname = await self._get_douyin_nickname(user_id) or chat_id

        # 启动后台异步任务执行 Mimo 视觉自愈补发
        async def _run_visual_healing():
            try:
                from agent.core.visual_agent import VisualAgent
                from agent.tools.visual_tools import send_qq_notification
                
                start_msg = (
                    f"🌸 **[小萤自愈启动]**\n"
                    f"亮哥，检测到常规 DOM 方式回复粉丝【{nickname}】失败。\n"
                    f"小萤正在激活 Mimo 视觉引擎，直连 CDP 操作浏览器进行物理自愈补发..."
                )
                await send_qq_notification(start_msg)
                
                visual = VisualAgent(
                    llm_client=self.bot.llm,
                    memory_manager=self.bot.memory,
                    cdp_url=os.getenv("VISUAL_CDP_URL", "http://127.0.0.1:9222")
                )
                async with visual:
                    res = await visual.execute(
                        task=f"在左侧私信联系人列表中寻找并点击名字为 '{nickname}' 的联系人，然后点击输入框，输入并发送私信消息：'{text}'"
                    )
                
                if res.get("success"):
                    success_msg = (
                        f"🌸 **[小萤专属自愈播报]**\n"
                        f"亮哥！刚才常规 DOM 发送私信失败了，小萤已自动启动 Mimo 视觉引擎，直连 CDP 操作浏览器，成功帮亮哥把消息补发出去啦！✨\n\n"
                        f"收信粉丝: {nickname}\n"
                        f"补发内容: {text}"
                    )
                    await send_qq_notification(success_msg)
                    logger.info(f"Visual self-healing succeeded for {nickname}")
                else:
                    err = res.get("error", "未知原因")
                    fail_msg = (
                        f"⚠️ **[小萤自愈失败]**\n"
                        f"抱歉亮哥，小萤尝试了 Mimo 视觉自愈补发给【{nickname}】但也失败了。\n"
                        f"失败原因: {err}\n"
                        f"待发送内容: {text}"
                    )
                    await send_qq_notification(fail_msg)
                    logger.error(f"Visual self-healing failed for {nickname}: {err}")
            except Exception as ex:
                logger.error(f"Error executing visual healing: {ex}", exc_info=True)
                try:
                    from agent.tools.visual_tools import send_qq_notification
                    await send_qq_notification(f"⚠️ **[小萤自愈异常]**\n自愈引擎执行时抛出异常: {ex}")
                except Exception:
                    pass
                    
        asyncio.create_task(_run_visual_healing())

    async def send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """OneBot HTTP 协议消息发送，负责具体的网络包推送。"""
        # 0.1 抖音消息专有通道拦截与路由分发 (以 HTTP POST 方式路由，实现物理无状态解耦)
        if str(user_id).startswith("douyin_"):
            try:
                url = f"http://127.0.0.1:{os.getenv('DOUYIN_PORT', '9000')}/send_private_msg"
                payload = {"user_id": str(user_id), "message": text}
                
                async def _do_post(sess, is_shared: bool):
                    timeout_val = aiohttp.ClientTimeout(total=15.0) if is_shared else 15.0
                    async with sess.post(url, json=payload, timeout=timeout_val) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("status") == "success" or data.get("status") == "ok":
                                self.douyin_fail_count = 0
                                logger.info(f"Douyin message sent successfully via DOM to {user_id}")
                            else:
                                logger.warning(f"Douyin gateway returned failed status: {data.get('reason')}")
                                await self._handle_douyin_fail(user_id, text)
                        else:
                            logger.warning(f"Douyin gateway returned status code {resp.status}")
                            await self._handle_douyin_fail(user_id, text)

                try:
                    if self.bot._http and not self.bot._http.closed:
                        await _do_post(self.bot._http, is_shared=True)
                    else:
                        async with aiohttp.ClientSession() as session:
                            await _do_post(session, is_shared=False)
                except Exception as post_err:
                    logger.error(f"Failed to HTTP POST private message to Douyin gateway: {post_err}")
                    await self._handle_douyin_fail(user_id, text)
            except Exception as router_err:
                logger.error(f"Failed to route message to Douyin HTTP gateway: {router_err}")
            return

        # 0. 全局物理发包滑窗令牌桶平滑流控整流
        await self.limiter.acquire()

        # 2. 文本净化（QQ 不支持 Markdown 粗斜体渲染，在此进行自动降解）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)

        # 2.5 修复 OneBot CQ 码中本地绝对路径漏掉 file:/// 的灾难性错误格式
        def _heal_cq_image(match):
            path = match.group(1)
            if path.startswith("/") and not any(path.startswith(prefix) for prefix in ("file://", "base64://", "http://", "https://")):
                return f"[CQ:image,file=file://{path}]"
            return match.group(0)
        text = re.sub(r'\[CQ:image,file=([^\]]+)\]', _heal_cq_image, text)

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


class TokenBucketLimiter:
    """全局物理发包平滑流控令牌桶限流器。
    
    采用高并发无锁死 (Lock-free/Timer-based) 排队算法，
    并发调用时仅在微秒级入锁更新未来的绝对时间指针，
    并在锁的外部执行 asyncio.sleep() 等待，彻底解决 sleep 霸占锁导致的高并发串行死锁。
    """
    
    def __init__(self, capacity: float = 5.0, refill_rate: float = 0.67):
        self.capacity = capacity
        self.refill_rate = refill_rate
        # 1.0 / refill_rate 表示产生 1 个令牌所需的物理时间 (秒)
        self.interval = 1.0 / refill_rate
        self.max_tokens = capacity
        
        # 记录下一次允许无延时发送的单调时间戳
        self.allow_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取发包令牌。采用微秒级短锁计算发包排队时间，并在锁外并发挂起。"""
        wait_time = 0.0
        async with self._lock:
            now = time.monotonic()
            
            # 如果 allow_at 在过去太远，说明长期未发包，令牌爆满，重置 allow_at
            # 最多允许积攒容量为 max_tokens 的爆发力，即最多回退 (max_tokens * interval)
            max_backlog = self.max_tokens * self.interval
            if now - self.allow_at > max_backlog:
                self.allow_at = now - max_backlog
                
            # 判定当前时间是否已经到了允许发包的时间
            if now >= self.allow_at:
                # 扣除 1 个令牌的等价时间
                self.allow_at = self.allow_at + self.interval
                # 如果 allow_at 仍小于 now，重置为 now + interval
                if self.allow_at < now:
                    self.allow_at = now + self.interval
                wait_time = 0.0
            else:
                # 令牌不足，为当前协程预支分配下一次允许的时间
                wait_time = self.allow_at - now
                self.allow_at = self.allow_at + self.interval
                
        # 锁外挂起：锁已被微秒级释放，其他协程可以瞬间入锁参与令牌时间排队
        if wait_time > 0.0:
            await asyncio.sleep(wait_time)
