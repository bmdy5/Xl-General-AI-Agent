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

NC_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3000")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000


class _PermEvent(asyncio.Event):
    """携带结果的异步事件 — 解决跨协程权限沟通的竞态条件。"""
    def __init__(self):
        super().__init__()
        self.result: bool = False


class QQGateway:
    """最小可行 QQ Gateway。WebSocket 收消息，HTTP API 发回复。"""

    def __init__(self, agent_factory):
        self._factory = agent_factory          # () → Agent
        self._agents: dict[str, object] = {}   # user_id/group_id → Agent
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, object] = {}  # session_key → _PermEvent
        self._reconnect_failures: int = 0      # 连续断连计数器
        self._last_offline_alert: float = 0.0  # 上次掉线报警时间戳（冷却防骚扰）

    async def run(self):
        """连接 NapCat WebSocket，循环处理消息."""
        async with aiohttp.ClientSession() as http:
            self._http = http
            # 开启后台守护巡检线程
            asyncio.create_task(self._daemon_loop())
            while True:
                try:
                    await self._ws_loop()
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    self._reconnect_failures += 1
                    logger.warning(f"WebSocket disconnected (Count: {self._reconnect_failures}/10): {e}, retry in 5s...")
                    
                    if self._reconnect_failures >= 10:
                        logger.error("WebSocket disconnected 10 times consecutively. Triggering NapCat self-healing restart...")
                        self._reconnect_failures = 0
                        try:
                            # 异步执行 Docker 重启指令
                            proc = await asyncio.create_subprocess_shell("docker restart napcat")
                            await proc.wait()
                            logger.info("NapCat container restarted successfully. Waiting 10s for initialization...")
                        except Exception as restart_err:
                            logger.error(f"Failed to restart NapCat container: {restart_err}")
                        await asyncio.sleep(10)  # 给 Docker 启动腾出 10 秒钟缓冲时间
                    else:
                        await asyncio.sleep(5)

    async def _daemon_loop(self):
        """后台守护巡检线程：定时检测到期任务，向管理员 QQ 推送确认并安全执行"""
        from agent.task_queue import TaskQueue
        import time
        logger.info("QQ Gateway Background Daemon Loop started.")
        q = TaskQueue()
        
        admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
        if not admin_id:
            logger.warning("QQ_ADMIN_ID not configured in .env. Background daemon is disabled.")
            return

        session_key = f"user_{admin_id}"

        while True:
            # 必须等 NapCat HTTP 连接就绪后才开始工作，避免 self._http 未就绪引发报错
            if not self._http:
                await asyncio.sleep(10)
                continue

            # ── 1. QQ 登录态主动感知与 macOS 警告环 ────────────────────────────────────
            try:
                url = f"{NC_HTTP_URL}/get_login_info"
                headers = {}
                if NC_TOKEN:
                    headers["Authorization"] = f"Bearer {NC_TOKEN}"
                
                async with self._http.get(url, headers=headers) as resp:
                    is_online = False
                    if resp.status == 200:
                        res_data = await resp.json()
                        if res_data.get("status") == "ok":
                            is_online = res_data.get("data", {}).get("online", False)
                    
                    if not is_online:
                        current_time = time.time()
                        if current_time - self._last_offline_alert > 1800:  # 30分钟防刷冷却
                            self._last_offline_alert = current_time
                            logger.error("QQ Login Session expired! Triggering macOS native alert notification...")
                            alert_cmd = (
                                'osascript -e \'display notification "QQ 机器人登录态已过期，请点击 WebUI 重新扫码登录！" '
                                'with title "⚠️ XL Agent 掉线警报" sound name "Glass"\''
                            )
                            proc = await asyncio.create_subprocess_shell(alert_cmd)
                            await proc.wait()
            except Exception as check_err:
                logger.warning(f"Failed to check QQ login status: {check_err}")

            # ── 2. 定时任务轮询逻辑 ──────────────────────────────────────────────────
            try:
                due_tasks = q.process_due()
                for task in due_tasks:
                    task_id = task["id"]
                    desc = task["description"]
                    action = task["action"]

                    # 1. 向管理员 QQ 私聊推送确认请求
                    await self._send("private", admin_id, "", 
                        f"⏰ [全天候中枢巡检]\n亮哥，检测到后台任务到期：\n【{desc}】\n\n回复「允许」或「y」授权我立即执行，回复其他取消。")

                    # 2. 注册等待锁，阻止线程并挂起 5 分钟等待用户在 QQ 上的答复
                    evt = _PermEvent()
                    self._pending_perms[session_key] = evt
                    try:
                        await asyncio.wait_for(evt.wait(), timeout=300)
                        approved = evt.result
                    except asyncio.TimeoutError:
                        approved = False
                    finally:
                        self._pending_perms.pop(session_key, None)

                    # 3. 根据主人确认结果进行后台调度
                    if approved:
                        await self._send("private", admin_id, "", f"🚀 正在后台执行任务: {desc}...")
                        agent = self._factory()
                        buf = ""
                        try:
                            async for evt in agent.run(action, stream=True):
                                if evt["type"] == "text_delta":
                                    buf += evt["content"]
                                elif evt["type"] == "permission_request":
                                    # 因为后台任务已经在 QQ 外层总揽确认过了，内层具体子权限自动放行
                                    agent.approve_permission()
                                elif evt["type"] == "error":
                                    buf += f"\n[错误: {evt['content']}]"
                        except Exception as e:
                            buf += f"\n[异常: {e}]"

                        # 4. 标记任务状态 (定时任务会更新 last_run 戳，普通任务标记 done)
                        q.mark_done(task_id)

                        # 5. 反馈结果
                        result_msg = f"✅ [执行完成]\n任务：{desc}\n\n执行结果反馈：\n{buf.strip()[:1500]}"
                        await self._send("private", admin_id, "", result_msg)
                    else:
                        await self._send("private", admin_id, "", f"⏸️ 已跳过任务：{desc}")

            except Exception as e:
                logger.error(f"Daemon loop encountered an error: {e}")

            # 每 5 分钟轮询一次
            await asyncio.sleep(300)

    async def _ws_loop(self):
        headers = {}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        async with aiohttp.ClientSession() as ws_session:
            async with ws_session.ws_connect(NC_WS_URL, headers=headers) as ws:
                logger.info(f"QQ Gateway connected: {NC_WS_URL}")
                self._reconnect_failures = 0  # 成功握手，计数器归零
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
                perm.result = True
            else:
                perm.result = False
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
            async for evt in agent.run(raw, stream=True):
                if evt["type"] == "text_delta":
                    buf += evt["content"]
                elif evt["type"] == "tool_call" and evt.get("name"):
                    if buf.strip() and not sent_ack:
                        sent_ack = True
                        await self._send_chunk(msg_type, user_id, group_id, buf.strip())
                        buf = ""
                    await self._send(msg_type, user_id, group_id,
                        f"正在{_tool_label(evt['name'])}...")
                elif evt["type"] == "permission_request":
                    cat = evt.get("category", "write")
                    if cat == "dangerous":
                        tools = [evt.get("tool_name", "?")]
                        approved = await self._ask_permission(msg_type, user_id, group_id,
                            evt.get("message", ""), tools)
                        if approved:
                            agent.approve_permission()
                        else:
                            agent.deny_permission()
                            await self._send(msg_type, user_id, group_id, "已拒绝。")
                    else:
                        agent.approve_permission()
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

        evt = _PermEvent()
        self._pending_perms[session_key] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=120)
            return evt.result
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
