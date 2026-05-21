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
        self._tts = None
        self._last_voice_time: float = 0.0

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

        # ── 5.5. 数据飞轮：夜间教练分析 ──
        coach_report = ""
        try:
            from agent.evo_coach import run_coach_analysis, save_coach_report, auto_apply_rules
            agent_coach = self._factory(session_key)
            agent_coach.max_turns = 5
            analysis = await run_coach_analysis(agent_coach.llm, today)
            if analysis:
                coach_report = save_coach_report(analysis) or ""
                applied = await auto_apply_rules(analysis, agent_coach.memory)
                if applied:
                    logger.info(f"Coach auto-applied {applied} rule(s)")

                # 数据飞轮阶段3: 自测验证
                from agent.evo_tester import run_self_test, save_test_report
                test_report = await run_self_test(agent_coach.llm, agent_coach.memory)
                if test_report["total"] > 0:
                    save_test_report(test_report)
            try:
                agent_coach._abort.set()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Coach analysis skipped: {e}")

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

                    is_silent = ("清理旧会话" in desc)

                    if is_silent:
                        approved = True
                    elif auto:
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
                        if not is_silent:
                            await self._send("private", admin_id, "", f"🚀 执行中: {desc}...")

                        agent = self._factory(session_key)
                        buf = ""
                        try:
                            # 特殊拦截：如果是自我审计任务，直接强行调用教练核心分析！
                            if "自我审计" in desc:
                                try:
                                    from agent.evo_coach import run_coach_analysis, save_coach_report, auto_apply_rules
                                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                                    if not is_silent:
                                        await self._send("private", admin_id, "", "🔍 正在调取教练录像带，深度剖析工具调用 traces...")
                                    
                                    agent.max_turns = 5
                                    analysis = await run_coach_analysis(agent.llm, today)
                                    if analysis:
                                        report_path = save_coach_report(analysis)
                                        applied = await auto_apply_rules(analysis, agent.memory)
                                        
                                        # 构造返回摘要
                                        buf = f"📊 【自我审计报告】\n\n一句话摘要：{analysis.get('summary', '')}\n"
                                        
                                        patterns = analysis.get("patterns", [])
                                        if patterns:
                                            buf += "\n🚨 发现的问题模式：\n"
                                            for i, p in enumerate(patterns, 1):
                                                buf += f"{i}. [{p.get('severity', 'medium')}] {p.get('pattern', '')}\n"
                                        else:
                                            buf += "\n✨ 今日一切完美，没有发现失误模式！\n"
                                            
                                        rule_updates = analysis.get("rule_updates", [])
                                        if rule_updates:
                                            buf += f"\n💡 建议规则更新 ({len(rule_updates)}条)：\n"
                                            for ru in rule_updates:
                                                buf += f"- {ru.get('new_rule')}\n"
                                                
                                        if report_path:
                                            buf += f"\n详细报告已生成，等待亮哥审核：\n{report_path}"
                                        if applied:
                                            buf += f"\n已自动在 EVOLVED_RULES.md 中应用 {applied} 条轻量规则。"
                                    else:
                                        buf = "📊 今日未发现明显的工具调用失误，没有生成新的改进提案。继续保持！"
                                except Exception as coach_err:
                                    logger.error(f"Coach task failed: {coach_err}")
                                    buf = f"❌ 自我审计任务执行失败: {coach_err}"
                            elif "学习早报" in desc or "播客" in desc:
                                try:
                                    if not is_silent:
                                        await self._send("private", admin_id, "", "🌅 正在扫描过去 48h 变动的 Obsidian 笔记并呼叫 NotebookLM 自动合成高保真中文播客...")
                                    
                                    from agent.auto_podcast import generate_podcast_workflow
                                    import os
                                    import base64
                                    
                                    local_path = await generate_podcast_workflow()
                                    if local_path and os.path.exists(local_path):
                                        with open(local_path, "rb") as vf:
                                            voice_bytes = vf.read()
                                        
                                        # 高保真静音填充，确保音频在 1.8 秒以上以支持 QQ 完美转码和播放
                                        voice_bytes = self._pad_wav(voice_bytes)
                                        b64_data = base64.b64encode(voice_bytes).decode("utf-8")
                                        cq_record = f"[CQ:record,file=base64://{b64_data}]"
                                        
                                        await self._send("private", admin_id, "", cq_record, skip_delay=True)
                                        buf = f"🎉 亮哥专属每日学习早报播客合成成功！音频已通过 QQ 语音推送到您的手机。\n本地保存路径：{local_path}"
                                    else:
                                        buf = "🌅 过去 48 小时内没有检测到任何学习笔记变动，今日学习早报自动跳过。"
                                except Exception as pod_err:
                                    logger.error(f"Podcast task failed: {pod_err}", exc_info=True)
                                    buf = f"❌ 学习早报播客生成失败: {pod_err}"
                            else:
                                async for evt in agent.run(action, stream=True):
                                    if evt["type"] == "text_delta":
                                        buf += evt["content"]
                                    elif evt["type"] == "tool_call" and evt.get("name"):
                                        if not is_silent:
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

                            if is_online and not is_silent:
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
                            if not is_silent:
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
        import html
        msg_type = event.get("message_type", "private")
        raw = html.unescape(event.get("raw_message", "").strip())
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

        # 检查是否在等权限确认 — 仅短文本(<5字)视为纯确认，长文本传给agent
        perm = self._pending_perms.get(session_key)
        if perm is not None:
            lower = raw.lower().strip()
            if len(lower) <= 4 and lower in ("允许", "y", "yes", "ok", "好", "可以", "行", "可", "对的", "是的"):
                perm.result = True
                perm.set()
                return
            elif len(lower) <= 4:
                perm.result = False
                perm.set()
                return
            # 长文本 → 既确认又继续对话
            perm.result = True
            perm.set()
            # 不return，继续走下面的对话流程

        # 下载QQ图片到本地持久化目录（bot可通过read_image查看）
        import re as _re
        import hashlib as _hl
        def _download_cq_images(text: str) -> str:
            def _dl(match):
                cq = match.group(0)
                url_m = _re.search(r'url=([^,\]]+)', cq)
                file_m = _re.search(r'file=([^,\]]+)', cq)
                if not url_m:
                    return cq
                img_url = url_m.group(1)
                fname = file_m.group(1) if file_m else "qq_image"
                # 持久化到 ~/.my-agent/images/，重启不丢
                img_dir = os.path.expanduser("~/.my-agent/images")
                os.makedirs(img_dir, exist_ok=True)
                local_path = os.path.join(img_dir, fname)
                try:
                    import urllib.request as _ur
                    # 先试直接下载
                    req = _ur.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with _ur.urlopen(req, timeout=15) as resp:
                        with open(local_path, "wb") as f:
                            f.write(resp.read())
                except Exception:
                    # 降级：通过 NapCat get_image API 下载
                    try:
                        nc_url = f"{NC_HTTP_URL}/get_image?file={fname}"
                        import urllib.request as _ur2
                        hdrs = {"User-Agent": "Mozilla/5.0"}
                        if NC_TOKEN:
                            hdrs["Authorization"] = f"Bearer {NC_TOKEN}"
                        req2 = _ur2.Request(nc_url, headers=hdrs)
                        with _ur2.urlopen(req2, timeout=15) as resp2:
                            data = json.loads(resp2.read())
                            img_url2 = data.get("data", {}).get("url", "")
                            if img_url2:
                                req3 = _ur2.Request(img_url2, headers={"User-Agent": "Mozilla/5.0"})
                                with _ur2.urlopen(req3, timeout=15) as resp3:
                                    with open(local_path, "wb") as f:
                                        f.write(resp3.read())
                    except Exception:
                        pass
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    return f"[图片已保存: {local_path} (可用read_image查看)]"
                return f"[图片: {fname}] (自动下载失败，请用文字描述图片内容。URL: {img_url[:60]}...)"
            return _re.sub(r'\[CQ:image,[^\]]+\]', _dl, text)

        raw = _download_cq_images(raw)

        # 数据飞轮：检测并记录用户纠正信号
        from agent.evo_traces import record_correction
        record_correction(raw)

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
            # 紧急抢占检测：纯关键词，无 LLM 调用
            is_preempt = any(kw in raw for kw in ["停", "别跑了", "取消", "刹车", "先别", "停下"])

            if is_preempt:
                active_task.cancel()
                self._log_activity("系统调度", f"紧急强占中断当前任务: {session_key}")
                old_raw = getattr(active_task, "raw_prompt", "之前的开发任务")
                interruption_note = (
                    f"[系统提示：{_ua}在刚才的任务中途发送了这条新命令。"
                    f"先简短确认停下上一个任务，然后切入新指令：\"{raw}\"]"
                )
                raw = interruption_note
            else:
                # CC 模式注入。但如果上一个 assistant 有未完成的 tool_calls，
                # 暂存排队（DeepSeek 要求 tool_calls 后紧跟 tool_result）
                last_msg = agent.messages[-1] if agent.messages else {}
                has_pending = (last_msg.get("role") == "assistant" and last_msg.get("tool_calls"))
                if has_pending:
                    self._message_queues.setdefault(session_key, []).append((event, raw))
                    self._log_activity("系统调度", f"工具执行中，消息暂存: {raw[:40]}...")
                    return

                agent.messages.append({"role": "user", "content": raw})
                if agent.session:
                    asyncio.create_task(agent.session.append_message(
                        {"role": "user", "content": raw}))
                self._log_activity("系统调度", f"消息注入当前任务: {raw[:60]}...")
                return

        # 启动任务执行
        task = asyncio.create_task(self._execute_task(session_key, event, raw))
        task.raw_prompt = raw
        self._current_tasks[session_key] = task

    async def _execute_task(self, session_key: str, event: dict, raw: str):
        msg_type = event.get("message_type", "private")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        
        # 0. 100% 还原 HTML 实体转义字符，修复中括号被转义为 &#91; 导致的匹配失败
        import html
        raw = html.unescape(raw)
        
        # 拦截小萤专属语音测试指令，方便亮哥在 QQ 中即时调音
        raw_strip = raw.strip()
        if raw_strip.startswith(("小萤语音测试：", "小萤语音测试:")):
            test_cmd = raw_strip[7:].strip()
            # 格式：[情绪] 说话内容 或 情绪 说话内容（支持无中括号、支持冒号/空格等分割）
            test_style = "撒娇"
            test_text = test_cmd
            
            import re as _re
            # 1. 尝试匹配中括号形式，如 [喜] 太好了 或 [喜]太好了
            m = _re.match(r'^\[([^\]]+)\](.*)', test_cmd, _re.DOTALL)
            if m:
                test_style = m.group(1).strip()
                test_text = m.group(2).strip()
            else:
                # 2. 尝试匹配带分隔符的任何前置词 (1-4个字)
                m_sep = _re.match(r'^([^\s：，:,\s]{1,4})(?:\s+|[：，:,\s]+)(.*)', test_cmd, _re.DOTALL)
                if m_sep:
                    potential_style = m_sep.group(1).strip()
                    known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "呆萌", "娇嗔", "元气", "活力", "温柔", "贴心", "软萌"}
                    if potential_style in known_styles or len(potential_style) <= 2:
                        test_style = potential_style
                        test_text = m_sep.group(2).strip()
                else:
                    # 3. 匹配无分隔符的情况，只允许 >= 2个字的已知情感词（避免“喜欢”等单字词误匹配）
                    known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "呆萌", "娇嗔", "元气", "活力", "温柔", "贴心", "软萌"}
                    for style in sorted(known_styles, key=len, reverse=True):
                        if len(style) >= 2 and test_cmd.startswith(style):
                            test_style = style
                            test_text = test_cmd[len(style):].strip()
                            break
            
            if test_text:
                # 调试提示：根据亮哥要求，已去除冗余的聊天框提示文本，只输出纯净语音
                await self._send_voice(msg_type, user_id, group_id, test_text, test_style, is_test=True)
            else:
                await self._send(msg_type, user_id, group_id, "⚠️ 请输入要合成的文本，格式如：小萤语音测试：[委屈] 小萤好难过呀", skip_delay=True)
            return

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
        # ── 1. 物理具身声带状态同步感知计算与注入 ──
        import time as _time
        now = _time.time()
        last_voice = getattr(self, "_last_voice_time", 0.0)
        time_diff = now - last_voice
        
        last_voice_str = f"{time_diff:.1f}秒前" if last_voice > 0.0 else "首次聊天（尚未发声）"
        
        # 隐性通知，将客观流逝事实通知大脑，发声控制权 100% 归还于大模型
        state_prefix = (
            f"[系统通知：网关物理发声限制已全面解除，发声权限 100% 归还于你。你上一次发送语音是：{last_voice_str}。"
            f"请展现你的高情商与克制力，自主评估当前是否符合“惊喜、感动或亮哥明确请求”的黄金契景，"
            f"从而自主掌控是否使用 [语音:情绪] 发声。普通聊天绝不多发，少发、精发才能带给亮哥惊喜。]"
        )
        
        # 拼接物理状态客观事实隐性前缀，投递给大脑
        raw = f"{state_prefix}\n{raw}"

        # 状态提示，模拟真人对话的自然过渡
        # 自然过渡：AI 自主决定，在 core.py _run_loop 中 yield transition 事件
        sent_transition = False
        buf = ""
        is_voice_reply = False
        voice_style = "知性"
 
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
 
                    if not is_voice_reply:
                        import re as _re
                        m_voice = _re.match(r'^\[语音(?::([^\]]+))?\]', buf.strip())
                        if m_voice:
                            # 发声权限已全面解除硬强杀，100% 放行并更新物理时间戳
                            is_voice_reply = True
                            voice_style = m_voice.group(1) or "知性"
                            self._last_voice_time = _time.time()
                            last_voice_str_log = f"{time_diff:.1f}秒" if last_voice > 0.0 else "首次发声"
                            self._log_activity("语音控频", f"✅ AI自主触发语音合成，情绪: {voice_style}，距离上次发声: {last_voice_str_log}")


 
                    if is_voice_reply:
                        # 语音回复时不进行流式分句发送，全量缓存在 buf 中以保持语音连贯性
                        pass
                    else:
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
                        if is_voice_reply:
                            import re as _re
                            pure_text = _re.sub(r'^\[语音(?::[^\]]+)?\]', '', buf.strip()).strip()
                            if pure_text:
                                await self._send_voice(msg_type, user_id, group_id, pure_text, voice_style)
                        else:
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
                    err_content = evt.get("content", "")
                    err_lower = err_content.lower()
                    # 强力拦截大模型高负载拥堵的特征词，维护小萤真人感
                    if any(x in err_lower for x in ["service is too busy", "serviceunavailableerror", "deepseekexception", "service_unavailable_error", "503", "unavailable"]):
                        buf += "\n（揉了揉太阳穴）唔……亮哥，我刚才大脑好像突然走神发呆了，感觉脑子里懵懵的，让我稍微缓一两分钟再陪你聊呀～"
                    else:
                        buf += f"\n[错误: {err_content}]"
                    self._log_activity("系统异常", f"Agent 报错: {err_content}")
        except asyncio.CancelledError:
            self._log_activity("系统调度", f"任务被外部 Cancel 取消: {raw[:50]}")
            raise
        except Exception as e:
            err_str = str(e)
            err_lower = err_str.lower()
            if any(x in err_lower for x in ["service is too busy", "serviceunavailableerror", "deepseekexception", "service_unavailable_error", "503", "unavailable"]):
                buf += "\n（揉了揉太阳穴）唔……亮哥，我刚才大脑好像突然走神发呆了，感觉脑子里懵懵的，让我稍微缓一两分钟再陪你聊呀～"
            else:
                buf += f"[异常: {e}]"
            self._log_activity("系统异常", f"运行时崩溃: {e}")
        finally:
            if buf.strip():
                self._log_activity("AI 计划/答复", buf.strip())
                if is_voice_reply:
                    import re as _re
                    pure_text = _re.sub(r'^\[语音(?::[^\]]+)?\]', '', buf.strip()).strip()
                    if pure_text:
                        await self._send_voice(msg_type, user_id, group_id, pure_text, voice_style)
                else:
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

            # 处理工具执行期间暂存的消息
            queue = self._message_queues.get(session_key, [])
            if queue:
                next_event, next_raw = queue.pop(0)
                self._log_activity("系统调度", f"暂存消息出队: {next_raw[:40]}...")
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

    def _pad_wav(self, wav_bytes: bytes, start_silence_sec: float = 0.3, min_duration_sec: float = 1.8) -> bytes:
        """为 WAV 音频首尾填充静音缓冲，确保总时长在 min_duration_sec 以上，防止 QQ 编码丢失和播放无声"""
        import wave
        import io
        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as w_in:
                params = w_in.getparams()
                nchannels, sampwidth, framerate, nframes = params[:4]
                data = w_in.readframes(nframes)
                
                frame_bytes_size = nchannels * sampwidth
                
                # 头部静音
                start_silence_frames = int(framerate * start_silence_sec)
                start_silence_data = b'\x00' * (start_silence_frames * frame_bytes_size)
                
                # 原始音频时长
                orig_duration = nframes / framerate
                
                # 尾部静音，确保总时长不低于 min_duration_sec
                current_total_duration = orig_duration + start_silence_sec
                if current_total_duration < min_duration_sec:
                    end_silence_sec = min_duration_sec - current_total_duration
                else:
                    end_silence_sec = 0.2  # 默认尾部补 0.2 秒以防播放提前截断
                    
                end_silence_frames = int(framerate * end_silence_sec)
                end_silence_data = b'\x00' * (end_silence_frames * frame_bytes_size)
                
                # 重新打包
                out_buf = io.BytesIO()
                with wave.open(out_buf, 'wb') as w_out:
                    w_out.setparams(params)
                    w_out.writeframes(start_silence_data + data + end_silence_data)
                return out_buf.getvalue()
        except Exception as e:
            logger.warning(f"Failed to pad WAV: {e}")
            return wav_bytes

    async def _send_voice(self, msg_type: str, user_id: str, group_id: str, text: str, style: str = "知性", is_test: bool = False):
        """使用 GPT-SoVITS 专属二次元原声 (和泉纱雾) 异步合成语音并发送"""
        import base64
        import re
        import asyncio
        import aiohttp
        import os

        # 1. 情感精调锁定配置表（参考音轨、Few-shot 日文 Prompt ＆ 采样精调参数）
        EMOTION_LOCKED_CONFIG = {
            # 🌸 撒娇：黄金 01 参考
            "撒娇": {
                "subdir": "coquettish",
                "wav_file": "slice_01.wav",
                "prompt_text": "お兄ちゃん、大好き！",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.65,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.03,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # ⚡ 元气：黄金 07 参考 (11:28 终极特调参数)
            "元气": {
                "subdir": "happy",
                "wav_file": "slice_07.wav",
                "prompt_text": "お兄ちゃん、朝だよ！起きて！",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 12,
                    "top_p": 0.85,
                    "speed_factor": 1.05,
                    "text_split_method": "cut3",
                    "repetition_penalty": 1.35
                }
            },
            # 💢 傲娇：采用 11:28 终极满意的 slice_15 傲娇灵魂特调！
            "傲娇": {
                "subdir": "aggrieved",
                "wav_file": "slice_15.wav",
                "prompt_text": "お兄ちゃんが意地悪するから...",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.75,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.03,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # 😢 委屈
            "委屈": {
                "subdir": "aggrieved",
                "wav_file": "slice_15.wav",
                "prompt_text": "お兄ちゃんが意地悪するから...",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 0.95,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # 正常/知性
            "正常": {
                "subdir": "normal",
                "wav_file": "slice_10.wav",
                "prompt_text": "お兄ちゃん、何？",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.00,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            "知性": {
                "subdir": "normal",
                "wav_file": "slice_10.wav",
                "prompt_text": "お兄ちゃん、何？",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.00,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            }
        }

        if not text.strip():
            return
            
        # 1.5. 网关硬性限字智能截断机制：限制语音长度在 15-20 字内，剩下的文本作为纯文本在语音发出后追加
        voice_text = text.strip()
        remaining_text = ""
        
        # 35字宽限策略：如果总字数不超过 35 字，则完全不截断，保留完整高保真情感朗读效果
        if len(voice_text) > 35:
            # 智能在 20 到 28 字之间倒序寻找合适的标点切分，避免截断吞字
            split_idx = 25
            found_split = False
            for i in range(28, 18, -1):
                if i < len(voice_text) and voice_text[i] in ("，", "。", "！", "？", ",", ".", "!", "?", "；", ";"):
                    split_idx = i + 1
                    found_split = True
                    break
            
            voice_text = text[:split_idx].strip()
            remaining_text = text[split_idx:].strip()

        try:
            # 2. 文本清洗，过滤特殊的 Markdown 标记，以及旁白动作括号“（动作描述）”使其不出现在语音合成中
            clean = voice_text
            clean = re.sub(r'（[^）]+）', '', clean)  # 过滤中文括号及其内容
            clean = re.sub(r'\([^)]+\)', '', clean)  # 过滤英文括号及其内容
            clean = re.sub(r'\[[^\]]+\]', '', clean)  # 过滤任何中括号格式的情感标签，确保不念出杂音
            clean = re.sub(r'\*+', '', clean)
            clean = re.sub(r'`+', '', clean)
            clean = re.sub(r'#+', '', clean)
            clean = clean.replace("&", "和").replace("<", " ").replace(">", " ")
            clean = clean.replace("……", "").replace("...", "")
            clean = clean.strip()
            
            if not clean:
                # 若截断后无可用发音字符，直接将整段文本通过普通文字发走
                await self._send(msg_type, user_id, group_id, text, skip_delay=is_test)
                return

            # 获取具体情感的黄金锁定配置，若无则降级为正常
            config = EMOTION_LOCKED_CONFIG.get(style)
            if not config:
                config = EMOTION_LOCKED_CONFIG["正常"]
            
            resources_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/sagiri_emotions"
            ref_wav_path = os.path.join(resources_dir, config["subdir"], config["wav_file"])
            
            if not os.path.exists(ref_wav_path):
                raise FileNotFoundError(f"Locked reference audio not found: {ref_wav_path}")
                
            logger.info(f"🎙️ [TTS] 开始使用 GPT-SoVITS 锁定原声音色合成. 情绪: [{style}] -> 参考音轨: '{config['wav_file']}' | 文本: '{clean}'")

            # 3. 读取环境变量 API 并构建请求 payload，完全透传特调参数
            api_url = os.getenv("GPT_SOVITS_API_URL", "http://127.0.0.1:9880")
            
            payload = {
                "text": clean,
                "text_lang": "zh",
                "ref_audio_path": ref_wav_path,
                "prompt_text": config["prompt_text"],
                "prompt_lang": config["prompt_lang"],
                "top_k": config["params"]["top_k"],
                "top_p": config["params"]["top_p"],
                "temperature": config["params"]["temperature"],
                "speed_factor": config["params"]["speed_factor"],
                "text_split_method": config["params"].get("text_split_method", "cut2"),  # 动态读取各自锁定特优的切分算法
                "repetition_penalty": config["params"]["repetition_penalty"],
                "media_type": "wav"
            }

            # 4. 设置 6.0 秒的安全超时，超过 6.0 秒立刻自动秒级无感降级到纯文本，兼顾高可用与冷启动容错
            timeout = aiohttp.ClientTimeout(total=6.0)
            voice_bytes = b""
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 优先采用 POST /tts 发送 JSON，确保语速 (speed_factor) 等特调参数在 SoVITS 引擎中 100% 成功解析生效
                async with session.post(f"{api_url}/tts", json=payload) as resp:
                    if resp.status == 200:
                        voice_bytes = await resp.read()
                    else:
                        err_text = await resp.text()
                        logger.warning(f"POST /tts failed with {resp.status}, trying GET /tts fallback... Error: {err_text}")
                        # 降级尝试 GET /tts
                        async with session.get(f"{api_url}/tts", params=payload) as resp_get:
                            if resp_get.status == 200:
                                voice_bytes = await resp_get.read()
                            else:
                                raise ValueError(f"GPT-SoVITS API both POST/GET /tts failed (status: {resp_get.status})")

            if len(voice_bytes) > 0:
                # 实施首尾高保真静音填充，确保音频在 1.8 秒以上以支持 QQ 完美转码和播放
                voice_bytes = self._pad_wav(voice_bytes)
                b64_data = base64.b64encode(voice_bytes).decode("utf-8")
                cq_record = f"[CQ:record,file=base64://{b64_data}]"
                await self._send(msg_type, user_id, group_id, cq_record, skip_delay=is_test)
                logger.info(f"✅ [TTS] GPT-SoVITS 动漫语音合成并发送成功！大小: {len(voice_bytes)} 字节")
                
                # 若有剩余溢出长文本，异步追加发送为纯文本（使其平滑应用拟人打字延迟）
                if remaining_text:
                    asyncio.create_task(self._send(msg_type, user_id, group_id, remaining_text, skip_delay=is_test))
            else:
                raise ValueError("Generated audio byte stream is empty")
                
        except Exception as e:
            logger.error(f"❌ [TTS] GPT-SoVITS 语音合成失败，已执行纯文本降级: {e}")
            # 高可用降级为纯文本，保障 100% 可用性
            await self._send(msg_type, user_id, group_id, text, skip_delay=is_test)


    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """通过 NapCat HTTP API 发送消息."""
        import re
        import os
        import random as _rand
        import asyncio

        # 检查是否为多媒体消息（语音/图片等）
        is_media = text.strip().startswith("[CQ:") or text.strip().startswith("[ CQ:")
        if not skip_delay and not is_media:
            # 拟真打字延迟算法：模拟思考 + 打字速度
            n_chars = len(text)
            base_delay = _rand.uniform(0.3, 0.8)
            char_delay = n_chars * 0.05
            total_delay = min(base_delay + char_delay, 3.5)
            self._log_activity("打字延迟", f"纯文本打字延迟：计算延迟 {total_delay:.2f}秒 (字数: {n_chars})，开始等待...")
            await asyncio.sleep(total_delay)

        # 去除 markdown 格式（QQ 不支持 markdown 渲染）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **粗体**
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # *斜体*
        text = re.sub(r'__(.+?)__', r'\1', text)      # __粗体2__

        def escape_invalid_cq(match):
            import base64
            cq_str = match.group(0)
            if cq_str.startswith("[CQ:record,"):
                return cq_str
            if cq_str.startswith("[CQ:image,file="):
                m_file = re.search(r'file=([^,\]\\]+)', cq_str)
                if m_file:
                    file_path = m_file.group(1)
                    if file_path.startswith("http://") or file_path.startswith("https://") or file_path.startswith("base64://"):
                        return cq_str
                    
                    # 路径双向纠偏（适配容器与宿主机环境）
                    resolved_path = file_path
                    if not os.path.exists(resolved_path):
                        host_prefix = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
                        current_file_dir = os.path.dirname(os.path.abspath(__file__))
                        container_root = os.path.dirname(current_file_dir)
                        if resolved_path.startswith(host_prefix):
                            relative_part = resolved_path[len(host_prefix):].lstrip("/")
                            alt_path = os.path.join(container_root, relative_part)
                            if os.path.exists(alt_path):
                                resolved_path = alt_path
                    
                    # 如果本地文件存在，转为 base64 发送以突破 Docker 隔离限制
                    if os.path.exists(resolved_path):
                        try:
                            with open(resolved_path, "rb") as f:
                                b64_data = base64.b64encode(f.read()).decode("utf-8")
                            return f"[CQ:image,file=base64://{b64_data}]"
                        except Exception as err:
                            logger.error(f"Failed to encode local image {resolved_path} to base64: {err}")
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
