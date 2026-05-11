"""QQ Gateway — NapCat WebSocket + HTTP API → Agent.

NapCat 是 QQ 机器人框架，暴露 OneBot v11 协议：
  - WebSocket server (默认 :3001) → 推送消息事件
  - HTTP API server (默认 :3000) → 发送消息

用法:
    python main.py --gateway
    NAPCAT_WS_URL=ws://localhost:3001 NAPCAT_HTTP_URL=http://localhost:3000 python main.py --gateway
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

NC_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://localhost:3001")
NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://localhost:3000")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000


class QQGateway:
    """最小可行 QQ Gateway。WebSocket 收消息，HTTP API 发回复。"""

    def __init__(self, agent_factory):
        self._factory = agent_factory          # () → Agent
        self._agents: dict[str, object] = {}   # user_id/group_id → Agent
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, asyncio.Event] = {}  # session_key → Event

    async def run(self):
        """连接 NapCat WebSocket，循环处理消息."""
        async with aiohttp.ClientSession() as http:
            self._http = http
            while True:
                try:
                    await self._ws_loop()
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    logger.warning(f"WebSocket disconnected: {e}, retry in 5s...")
                    await asyncio.sleep(5)

    async def _ws_loop(self):
        headers = {}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        async with aiohttp.ClientSession() as ws_session:
            async with ws_session.ws_connect(NC_WS_URL, headers=headers) as ws:
                logger.info(f"QQ Gateway connected: {NC_WS_URL}")
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        event = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("post_type") == "message":
                        asyncio.create_task(self._handle(event))

    # ── message handling ─────────────────────────────────────

    async def _handle(self, event: dict):
        msg_type = event.get("message_type", "private")
        raw = event.get("raw_message", "").strip()
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""

        if msg_type == "group":
            self_id = str(event.get("self_id", ""))
            if f"[CQ:at,qq={self_id}]" not in raw:
                return
            raw = re.sub(r'\[CQ:at,qq=\d+\]', '', raw).strip()
            if not raw:
                return
            session_key = f"group_{group_id}"
        else:
            session_key = f"user_{user_id}"

        # 检查是否在等权限确认
        perm = self._pending_perms.get(session_key)
        if perm is not None:
            lower = raw.lower().strip()
            if lower in ("允许", "y", "yes", "ok", "好", "可以", "行"):
                self._perm_result = True
            else:
                self._perm_result = False
            perm.set()
            return

        logger.info(f"QQ [{session_key}]: {raw[:80]}")

        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory()
            self._agents[session_key] = agent

        # 流式调用 — plan mode 默认开启，工具执行前弹 macOS 对话框
        buf = ""
        sent_ack = False
        try:
            async for evt in agent.run(raw, stream=True, plan_mode=True):
                if evt["type"] == "text_delta":
                    buf += evt["content"]
                elif evt["type"] == "tool_call" and evt.get("name"):
                    if buf.strip() and not sent_ack:
                        sent_ack = True
                        await self._send_chunk(msg_type, user_id, group_id, buf.strip())
                        buf = ""
                    await self._send(msg_type, user_id, group_id,
                        f"正在{_tool_label(evt['name'])}...")
                elif evt["type"] == "plan_ready":
                    tools = evt.get("tools", [])
                    plan_text = evt.get("content", "")[:300]
                    approved = await self._ask_permission(msg_type, user_id, group_id, plan_text, tools)
                    if approved:
                        agent.approve_plan()
                    else:
                        agent.abort()
                        await self._send(msg_type, user_id, group_id, "已取消。")
                        return
                elif evt["type"] == "error":
                    buf += f"\n[错误: {evt['content']}]"
        except Exception as e:
            buf += f"[异常: {e}]"

        # 发送剩余文本（按 [SPLIT] 分段，处理 [WAIT:N]）
        if buf.strip():
            await self._send_chunk(msg_type, user_id, group_id, buf.strip())

    async def _send_chunk(self, msg_type, user_id, group_id, text):
        """发送一个文本块，处理 [SPLIT] 和 [WAIT:N]."""
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
            await self._send(msg_type, user_id, group_id, part)
            if i < len(parts) - 1:
                delay = max(0.5, wait) if wait > 0 else _natural_delay(part)
                await asyncio.sleep(delay)
                wait = 0

    async def _ask_permission(self, msg_type, user_id, group_id, plan_text, tools) -> bool:
        """QQ 上发确认消息，等用户回复."""
        session_key = f"group_{group_id}" if group_id else f"user_{user_id}"
        tool_list = ", ".join(tools)
        await self._send(msg_type, user_id, group_id,
            f"准备执行: {tool_list}\n{plan_text[:200]}\n\n回复「允许」继续，其他取消。")

        evt = asyncio.Event()
        self._pending_perms[session_key] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=120)
            return self._perm_result
        except asyncio.TimeoutError:
            await self._send(msg_type, user_id, group_id, "超时，已取消。")
            return False
        finally:
            self._pending_perms.pop(session_key, None)


    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str):
        """通过 NapCat HTTP API 发送消息."""
        if msg_type in ("private", "temp"):
            endpoint = "/send_private_msg"
            payload = {"user_id": int(user_id), "message": text}
        else:
            endpoint = "/send_group_msg"
            payload = {"group_id": int(group_id), "message": text}

        url = f"{NC_HTTP_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        try:
            if self._http:
                async with self._http.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Send failed ({resp.status}): {body[:100]}")
                    else:
                        logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
                logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
        except Exception as e:
            logger.error(f"Send error: {e}")


# ── 模块级工具 ─────────────────────────────────────────────


def _tool_label(name: str) -> str:
    return {
        "web_search": "搜索资料", "web_fetch": "读取网页",
        "read_file": "读取文件", "write_file": "写入文件",
        "bash": "执行命令", "spawn_agent": "派子Agent干活",
        "save_memory": "保存记忆", "read_image": "分析图片",
    }.get(name, f"调用{name}")


def _natural_delay(text: str) -> float:
    """根据文本长度自然计算发送间隔."""
    n = len(text)
    if n < 10:
        return 0.3
    if n < 30:
        return 0.6
    if n < 80:
        return 1.0
    return 1.5
