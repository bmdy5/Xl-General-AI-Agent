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

        # 群聊：必须 @bot
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

        logger.info(f"QQ [{session_key}]: {raw[:80]}")

        # 获取或创建 per-user agent
        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory()
            self._agents[session_key] = agent

        # 调用 agent
        reply_parts = []
        tool_count = 0
        try:
            async for evt in agent.run(raw, stream=False):
                if evt["type"] == "text_delta":
                    reply_parts.append(evt["content"])
                elif evt["type"] == "tool_call":
                    tool_count += 1
                elif evt["type"] == "error":
                    reply_parts.append(f"\n[错误: {evt['content']}]")
        except Exception as e:
            reply_parts.append(f"[异常: {e}]")

        reply = "".join(reply_parts).strip()
        if not reply:
            return

        # 截断过长回复
        if len(reply) > MAX_REPLY_CHARS:
            reply = reply[:MAX_REPLY_CHARS - 20] + "\n...(truncated)"

        await self._send(msg_type, user_id, group_id, reply)

    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str):
        """通过 NapCat HTTP API 发送消息."""
        if msg_type == "private":
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
            async with self._http.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Send failed ({resp.status}): {body[:100]}")
                else:
                    logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
        except Exception as e:
            logger.error(f"Send error: {e}")
