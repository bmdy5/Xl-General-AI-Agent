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
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

NC_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000

# 硬编码每日维护 — LLM 只用 merge_to_core，文件操作全由 Python 处理
MAINTENANCE_MERGE_PROMPT = """把以下 reflect 内容合并到核心记忆：

{reflect_list}

对每条 reflect:
1. 判断归属的核心文件（user_profile / communication_rules / operation_rules / xl_tool_guide / xl_architecture / xl_code_review / xl_identity / xl_debugging / xl_requirement_analysis）
2. 调用 merge_to_core(target_file="{文件名}", description="{简短描述}", content="{萃取后的内容}")
3. merge_to_core 自动批准，直接跑

只萃取每条 reflect 中唯一的、有价值的信息写入对应核心文件，重复的跳过。
完成后输出 "merge_done: N"。"""


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
        self._activity_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent_activity.log"
        self._current_tasks: dict[str, asyncio.Task] = {}
        self._message_queues: dict[str, list[tuple[dict, str]]] = {}

    def _load_persona(self) -> tuple:
        """从运行期画像读取 (name, user_address)，兜底返回默认值。"""
        import json
        from pathlib import Path
        try:
            pf = Path.home() / ".my-agent" / "memory" / "persona_profile.json"
            if pf.exists():
                d = json.loads(pf.read_text(encoding="utf-8"))
                return d.get("name", "小萤"), d.get("user_address", "亮哥")
        except Exception:
            pass
        return "小萤", "亮哥"

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

    async def _run_daily_maintenance(self, session_key: str):
        """硬编码每日维护 — Python 管文件，LLM 只调 merge_to_core."""
        import json
        import shutil
        from pathlib import Path as _Path

        mem_dir = _Path.home() / ".my-agent" / "memory"
        backup_base = _Path.home() / ".my-agent" / "memory_backup"
        backup_base.mkdir(parents=True, exist_ok=True)

        # 防重入由 daemon loop 的 maintenance.json 状态管理保证，此处直接执行
        maint_file = _Path.home() / ".my-agent" / "maintenance.json"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ── 1. Python: 备份 .md 文件 ──
        backup_dir = backup_base / f"auto_{today}"
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in mem_dir.glob("*.md"):
            if f.name != "MEMORY.md":
                shutil.copy2(f, backup_dir / f.name)
                count += 1
        logger.info(f"Maintenance backup: {count} files → {backup_dir}")

        # ── 2. Python: 列出未合并的 reflect（按文件名判断，未加 merged_ 前缀=未处理）──
        reflect_files = sorted(mem_dir.glob("reflect_*.md"))
        unmerged = []
        for rf in reflect_files:
            try:
                content = rf.read_text(encoding="utf-8")
                unmerged.append((rf, content[:600]))  # 保存 Path 对象，后续直接 rename
            except Exception:
                pass

        merged_count = 0
        if not unmerged:
            logger.info("No reflect files to merge")
        else:
            # ── 3. Python: 构建 prompt → LLM: merge_to_core ──
            batch = unmerged[:10]
            reflect_list = "\n\n".join(
                f"### {rf.name}\n```\n{content}\n```"
                for rf, content in batch
            )
            prompt = MAINTENANCE_MERGE_PROMPT.format(reflect_list=reflect_list)

            agent = self._factory(session_key)
            agent.max_turns = 10
            agent.is_maintenance = True
            try:
                async for evt in agent.run(prompt, stream=True):
                    if evt["type"] == "permission_request":
                        agent.approve_permission()
                    elif evt["type"] == "error":
                        logger.warning(f"Maintenance merge error: {evt['content']}")
            except Exception as e:
                logger.error(f"Maintenance merge failed: {e}")
            finally:
                try:
                    agent._abort.set()
                except Exception:
                    pass

            # ── 4. Python: rename 已发给LLM的reflect → merged_ 前缀，移到backup ──
            for rf, _ in batch:
                try:
                    new_name = f"merged_{rf.name}"
                    shutil.move(str(rf), str(backup_dir / new_name))
                    merged_count += 1
                except Exception:
                    pass

        # ── 5. Python: rm 过期备份目录（>7天）──
        cleaned = 0
        for bd in sorted(backup_base.glob("auto_*")):
            try:
                dir_date = bd.name.replace("auto_", "")
                dir_dt = datetime.fromisoformat(dir_date).replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - dir_dt).days > 7:
                    shutil.rmtree(bd)
                    cleaned += 1
            except Exception:
                pass

        # ── 6. Python: 持久化维护状态 ──
        maint_state = {
            "last_date": today,
            "status": "ok",
            "merged": merged_count,
            "cleaned": cleaned,
            "backup_files": count,
        }
        maint_file.write_text(json.dumps(maint_state, ensure_ascii=False, indent=2))

        logger.info(
            f"Maintenance done: backup={count}, merged={merged_count}, cleaned_dirs={cleaned}"
        )

    async def _daemon_loop(self):
        """后台守护巡检线程：定时检测到期任务 + 硬编码每日维护"""
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
                            is_online = resp.status == 200 and res_data.get("retcode") == 0
                    
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

            # ── 1.5. 硬编码每日维护（每天跑一次，背景执行不阻塞守护循环）─────
            try:
                import json
                from pathlib import Path as _Path
                maint_file = _Path.home() / ".my-agent" / "maintenance.json"
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                need_maint = True
                if maint_file.exists():
                    try:
                        st = json.loads(maint_file.read_text())
                        if st.get("last_date") == today_str:
                            need_maint = False
                    except Exception:
                        pass
                if need_maint:
                    logger.info("Daily maintenance triggered")
                    maint_file.write_text(json.dumps(
                        {"last_date": today_str, "status": "running"}, ensure_ascii=False))
                    asyncio.create_task(self._run_daily_maintenance(session_key))
            except Exception as maint_err:
                logger.error(f"Daily maintenance error: {maint_err}")

            # ── 2. 定时任务轮询逻辑 ──────────────────────────────────────────────────
            try:
                q._load()  # 重新加载 tasks.json（bot 可能已创建新任务）
                due_tasks = q.process_due()
                for task in due_tasks:
                    task_id = task["id"]
                    desc = task["description"]
                    action = task["action"]
                    auto = task.get("auto_execute", False)

                    _pn, _ua = self._load_persona()

                    if auto:
                        # Auto-execute: run immediately, no permission prompt
                        await self._send("private", admin_id, "",
                            f"🤖 [自动任务] {desc} — 正在执行...")
                        approved = True
                    else:
                        # Need user confirmation
                        await self._send("private", admin_id, "",
                            f"⏰ [定时任务到期]\n{_ua}，任务「{desc}」到期。\n回复「允许」执行，回复其他跳过。")
                        evt = _PermEvent()
                        self._pending_perms[session_key] = evt
                        try:
                            await asyncio.wait_for(evt.wait(), timeout=300)
                            approved = evt.result
                        except asyncio.TimeoutError:
                            approved = False
                        finally:
                            self._pending_perms.pop(session_key, None)

                    if approved:
                        await self._send("private", admin_id, "", f"🚀 执行中: {desc}...")

                        agent = self._factory(session_key)
                        buf = ""
                        try:
                            async for evt in agent.run(action, stream=True):
                                if evt["type"] == "text_delta":
                                    buf += evt["content"]
                                elif evt["type"] == "tool_call" and evt.get("name"):
                                    await self._send("private", admin_id, "",
                                        f"⚙️ [{desc}] {_tool_label(evt['name'])}...")
                                elif evt["type"] == "permission_request":
                                    agent.approve_permission()
                                elif evt["type"] == "error":
                                    buf += f"\n[错误: {evt['content']}]"

                            # Deliver result
                            result_summary = buf[:800] + ("..." if len(buf) > 800 else "")

                            # Check if QQ is online
                            is_online = False
                            try:
                                async with self._http.get(
                                    f"{NC_HTTP_URL}/get_login_info",
                                    headers={"Authorization": f"Bearer {NC_TOKEN}"} if NC_TOKEN else {}
                                ) as resp:
                                    is_online = resp.status == 200
                            except Exception:
                                pass

                            if is_online:
                                await self._send("private", admin_id, "",
                                    f"✅ [任务完成] {desc}\n\n{result_summary}")

                            # Always save result to learning notes
                            try:
                                note_content = (
                                    f"# 定时任务: {desc}\n\n"
                                    f"执行时间: {datetime.now(timezone.utc).isoformat()}\n\n"
                                    f"## 结果\n{result_summary}"
                                )
                                await agent.memory.save_to_notes(
                                    dir_path="06-工作记录/定时任务",
                                    filename=f"task_{task_id}.md",
                                    content=note_content,
                                )
                            except Exception as save_err:
                                logger.warning(f"Failed to save task result to notes: {save_err}")

                        except Exception as run_err:
                            logger.error(f"Task execution failed: {run_err}")
                            await self._send("private", admin_id, "",
                                f"❌ [任务失败] {desc}: {str(run_err)[:200]}")

                    q.mark_done(task_id)

            except Exception as e:
                logger.error(f"Daemon task processing error: {e}")

            # 每 5 分钟轮询一次
            await asyncio.sleep(30)  # 每30秒巡检一次

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

        # 写入用户指令审计日志
        _pn, _ua = self._load_persona()
        self._log_activity("用户输入", f"{_ua} ({session_key}): {raw}")

        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory(session_key)
            self._agents[session_key] = agent

        # 判定是否有旧任务正在运行
        active_task = self._current_tasks.get(session_key)
        if active_task and not active_task.done():
            # AI 直觉语义分类器（极速 LiteLLM 判定）
            is_preempt = False
            try:
                classify_prompt = [
                    {"role": "system", "content": f"{_ua}发送了新消息。当前后台正有一个长任务在运行。请根据中文语义理解判定这是否属于一个紧急的抢占式打断指令（即{_ua}要求你立刻强行停下当前的工作去干新任务，例如'别跑了先看这个'、'停！'、'你先做这个'）？若是，只输出 True，否则只输出 False。绝对不要输出任何其他多余字符！"},
                    {"role": "user", "content": f"新消息内容: '{raw}'"}
                ]
                res = await agent.llm.chat(classify_prompt)
                ans = res.get("content", "").strip().lower()
                is_preempt = "true" in ans
            except Exception as e:
                logger.error(f"Classifier failed: {e}")
                # 关键词兜底
                is_preempt = any(kw in raw for kw in ["先", "别", "停", "等", "急", "刹车", "取消"])

            if is_preempt:
                # 强占式中断：取消旧任务，原地刹车
                active_task.cancel()
                self._log_activity("系统调度", f"紧急强占中断当前任务: {session_key}")
                
                # 记录被取消的指令，用于注入记忆插梢
                old_raw = getattr(active_task, "raw_prompt", "之前的开发任务")
                interruption_note = (
                    f"[系统提示：{_ua}在刚才的开发任务 \"{old_raw}\" 运行中途，发送了这条新命令。"
                    f"请你根据你最新的人格手册，首先简短、自然地确认你已经停下了上一个任务，然后立刻切入分析{_ua}的新指令：\"{raw}\"]"
                )
                raw = interruption_note
            else:
                # 非强占式：进入排队队列
                self._message_queues.setdefault(session_key, []).append((event, raw))
                self._log_activity("系统调度", f"新任务加入排队队列: {raw}")
                
                # 异步 AI 动态安抚秒回（不发任何硬编码气泡）
                async def async_fast_reply():
                    try:
                         prompt_msg = [
                             {"role": "system", "content": f"请读取{_ua}对你的性格要求和纠正记忆，用极具个性、俏皮、懂事的女性程序员语气，写一句极短（15字内）的话，告诉{_ua}你收到新任务并排在待办清单里了，等手头忙完马上自动跑。直接输出答复内容，绝对不要带任何多余字眼！"},
                             {"role": "user", "content": f"{_ua}追加发送的新任务是：{raw}"}
                         ]
                         res = await agent.llm.chat(prompt_msg)
                         reply = res.get("content", "").strip()
                         if reply:
                             await self._send(msg_type, user_id, group_id, reply)
                    except Exception as err:
                         logger.error(f"Fast reply failed: {err}")
                         await self._send(msg_type, user_id, group_id, f"{_ua}，新任务记下了，手头这步忙完马上自动跑！")
                
                asyncio.create_task(async_fast_reply())
                return

        # 启动任务执行
        task = asyncio.create_task(self._execute_task(session_key, event, raw))
        task.raw_prompt = raw
        self._current_tasks[session_key] = task

    async def _execute_task(self, session_key: str, event: dict, raw: str):
        msg_type = event.get("message_type", "private")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        
        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory(session_key)
            self._agents[session_key] = agent

        import json
        _persona_name = "小萤"
        _user_address = "亮哥"
        try:
            _pf = agent.memory.base_dir / "persona_profile.json"
            if _pf.exists():
                _prof = json.loads(_pf.read_text(encoding="utf-8"))
                _persona_name = _prof.get("name", "小萤")
                _user_address = _prof.get("user_address", "亮哥")
        except Exception:
            pass
        # 状态提示，模拟真人对话的自然过渡
        import random as _rand
        # 自然过渡：AI 自主决定，在 core.py _run_loop 中 yield transition 事件
        sent_transition = False
        buf = ""

        # 流式段落/句子分发清洗逻辑，消除憋字挂起感
        try:
            async for evt in agent.run(raw, stream=True):
                if evt["type"] == "transition":
                    if not sent_transition:
                        sent_transition = True
                        await self._send(msg_type, user_id, group_id, evt['content'])
                elif evt["type"] == "text_delta":
                    if not sent_transition:
                        sent_transition = True
                    buf += evt["content"]

                    if "[SPLIT]" in buf:
                        parts = buf.split("[SPLIT]")
                        for part in parts[:-1]:
                            if part.strip():
                                self._log_activity("AI 计划/答复", part.strip())
                                await self._send_chunk(msg_type, user_id, group_id, part.strip())
                        buf = parts[-1]
                    elif "\n\n" in buf and len(buf) > 40:
                        idx = buf.rfind("\n\n")
                        to_send = buf[:idx]
                        if to_send.strip():
                            self._log_activity("AI 计划/答复", to_send.strip())
                            await self._send_chunk(msg_type, user_id, group_id, to_send.strip())
                        buf = buf[idx+2:]
                    elif len(buf) > 100 and any(p in buf for p in ("。", "！", "？")):
                        idx = -1
                        for p in ("。", "！", "？"):
                            p_idx = buf.rfind(p)
                            if p_idx > idx:
                                idx = p_idx
                        if idx != -1:
                            to_send = buf[:idx+1]
                            if to_send.strip():
                                self._log_activity("AI 计划/答复", to_send.strip())
                                await self._send_chunk(msg_type, user_id, group_id, to_send.strip())
                            buf = buf[idx+1:]

                elif evt["type"] == "tool_call" and evt.get("name"):
                    t_name = evt["name"]
                    t_args = evt.get("args", {})
                    detail = self._tool_detail(t_name, t_args)
                    
                    if buf.strip():
                        self._log_activity("AI 计划/答复", buf.strip())
                        await self._send_chunk(msg_type, user_id, group_id, buf.strip())
                        buf = ""
                    
                    self._log_activity("工具调用", f"{t_name} | 参数: {t_args}")
                    # 让 agent 自己决定怎么说，系统不发硬编码状态
                    
                elif evt["type"] == "tool_result":
                    res = evt.get("result", "")
                    t_name = evt.get("name", "tool")
                    self._log_activity("工具返回", f"{t_name} | 结果大小: {len(res)} 字节")
                    
                    # 错情判定
                    has_error = False
                    if "exit code:" in res:
                        m = re.search(r'exit code:\s*(\d+)', res)
                        if m and m.group(1) != "0":
                            has_error = True
                    elif res.strip().startswith("Error"):
                        has_error = True
                    
                    if has_error:
                        self._log_activity("系统异常", f"工具 {t_name} 执行失败: {res[:200]}")
                        await self._send(msg_type, user_id, group_id, f"⚠️ [警告] 刚才跑命令失败了！错误反馈：\n{res[:300]}")
                        
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
                    self._log_activity("系统异常", f"Agent 报错: {evt['content']}")
        except asyncio.CancelledError:
            self._log_activity("系统调度", f"任务被外部 Cancel 取消: {raw[:50]}")
            raise
        except Exception as e:
            buf += f"[异常: {e}]"
            self._log_activity("系统异常", f"运行时崩溃: {e}")
        finally:
            if buf.strip():
                self._log_activity("AI 计划/答复", buf.strip())
                await self._send_chunk(msg_type, user_id, group_id, buf.strip())
            
            # 后台异步触发人格自画像整理 (Consolidation)，每 5 轮一次省 token
            _consolidate_count = getattr(self, "_consolidate_count", 0) + 1
            self._consolidate_count = _consolidate_count
            async def async_consolidate_persona():
                if _consolidate_count % 5 != 0:
                    return
                profile_file = agent.memory.base_dir / "persona_profile.json"
                if profile_file.exists():
                    try:
                        import json
                        current_profile = profile_file.read_text(encoding="utf-8")
                        feedback_mems = agent.memory.search_memories("纠正 语气 态度 称呼 性格 说话方式", limit=5)
                        if feedback_mems:
                            feedback_text = "\n".join([f"- {m.get('content')}" for m in feedback_mems])
                            consolidation_prompt = [
                                {"role": "system", "content": f"你是{_persona_name}，{_user_address}的女性极客合伙人。这里有你当前的人格画像手册 (json 格式) 以及{_user_address}对你的最新语气与态度纠正反馈。请进行深刻自我反思，合并和覆盖旧的配置规则，解决任何自相矛盾的部分（比如{_user_address}让你严肃你就要把撒娇权重调低，让称呼亲近就要把官腔规则删掉），更新生成一份极其精炼、地道的全新 JSON 手册（保持和原格式 schema 100% 一致）。只输出合法的 JSON 文本，不要有任何 Markdown 包裹或解释字眼！"},
                                {"role": "user", "content": f"旧人格手册:\n{current_profile}\n\n{_user_address}最新的性格调教指示:\n{feedback_text}"}
                            ]
                            res = await agent.llm.chat(consolidation_prompt)
                            new_json = res.get("content", "").strip()
                            new_json = re.sub(r'^```json\s*', '', new_json)
                            new_json = re.sub(r'\s*```$', '', new_json)
                            # 校验合法性再写入
                            json.loads(new_json)
                            profile_file.write_text(new_json, encoding="utf-8")
                            self._log_activity("系统调度", f"{_persona_name}成功完成人格自画像整合反思更新。")
                    except Exception as e:
                        logger.error(f"Persona consolidation failed: {e}")

            # 启动后台异步自反思
            asyncio.create_task(async_consolidate_persona())
            
            self._current_tasks.pop(session_key, None)
            
            # 自动拉起下一个排队任务
            queue = self._message_queues.get(session_key, [])
            if queue:
                next_event, next_raw = queue.pop(0)
                self._log_activity("系统调度", f"自动拉起下一个排队任务: {next_raw}")
                task = asyncio.create_task(self._execute_task(session_key, next_event, next_raw))
                task.raw_prompt = next_raw
                self._current_tasks[session_key] = task

    def _tool_detail(self, name: str, args: dict) -> str:
        """解包常用开发工具的核心参数，用于高度可视化的微广播."""
        import os
        if not args:
            return ""
        try:
            if name == "bash":
                return f"执行命令: {args.get('command')}"
            elif name in ("read_file", "write_file", "edit_file"):
                path = args.get("file_path", "")
                basename = os.path.basename(path) if path else "未知文件"
                action_map = {"read_file": "审查文件", "write_file": "保存文件", "edit_file": "精准修改文件"}
                return f"{action_map.get(name, '处理')}: {basename}"
        except Exception:
            pass
        return ""

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
            f"🔧 需要执行以下操作：\n{plan_text[:200]}\n\n回复「允许」放行，回复其他取消。")

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
        import re
        import os

        # 去除 markdown 格式（QQ 不支持 markdown 渲染）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **粗体**
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # *斜体*
        text = re.sub(r'__(.+?)__', r'\1', text)      # __粗体2__

        def escape_invalid_cq(match):
            cq_str = match.group(0)
            if cq_str.startswith("[CQ:image,file="):
                m_file = re.search(r'file=([^,\\]]+)', cq_str)
                if m_file:
                    file_path = m_file.group(1)
                    if os.path.exists(file_path) or file_path.startswith("http://") or file_path.startswith("https://"):
                        return cq_str
            return cq_str.replace("[CQ:", "[ CQ:")
        
        text = re.sub(r'\[CQ:[^\]]+\]', escape_invalid_cq, text)

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

    def _log_activity(self, category: str, content: str):
        """记录 Agent 的核心活动轨迹，结构化追加写入日志."""
        import datetime
        import re
        
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        try:
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")


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
