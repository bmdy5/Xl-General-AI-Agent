# Agent Self-Scheduling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give agent a `schedule_task` tool to create/manage its own scheduled tasks with natural-language time parsing, auto-execute support, and result delivery to QQ + learning notes.

**Architecture:** New tool (`schedule_task_tool.py`) wraps enhanced `TaskQueue` (natural time parsing + auto_execute). Gateway daemon delivers results to QQ (if online) and always archives to learning notes. Remove all hardcoded tasks.

**Tech Stack:** Python 3.14, aiohttp (QQ send), TaskQueue JSON persistence

---

### Task 1: Natural time parsing in TaskQueue

**Files:**
- Modify: `agent/task_queue.py`

Add `parse_natural_time()` function and `auto_execute` field support.

- [ ] **Step 1: Add parse_natural_time() function**

In `agent/task_queue.py`, add after the imports:

```python
import re
from datetime import timedelta

def parse_natural_time(text: str) -> tuple[str, Optional[str]]:
    """Parse natural-language time into (cron_expression, once_epoch).
    
    Returns:
        ("daily", None) for daily
        ("hourly", None) for hourly
        ("once", "<iso_timestamp>") for one-shot tasks
        ("cron expr", None) for cron expressions
    """
    text = text.strip().lower()
    
    # "in N minutes/hours"
    m = re.match(r'in\s+(\d+)\s*(min|minute|minutes|h|hour|hours)', text)
    if m:
        n = int(m.group(1))
        seconds = n * 60 if 'min' in m.group(2) else n * 3600
        run_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        return ("once", run_at)
    
    # "tomorrow [H:MM]"
    m = re.match(r'tomorrow\s*(\d{1,2}):(\d{2})?', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        run_at = tomorrow.replace(hour=h, minute=mi, second=0, microsecond=0).isoformat()
        return ("once", run_at)
    
    # "daily [HH:MM]"
    if text.startswith("daily"):
        m = re.search(r'(\d{1,2}):(\d{2})', text)
        if m:
            return (f"{m.group(2)} {m.group(1)} * * *", None)
        return ("daily", None)
    
    # "hourly"
    if text == "hourly":
        return ("hourly", None)
    
    # "every Monday 8am" / "every Mon 08:00"
    day_map = {"mon": 1, "monday": 1, "tue": 2, "tuesday": 2,
               "wed": 3, "wednesday": 3, "thu": 4, "thursday": 4,
               "fri": 5, "friday": 5, "sat": 6, "saturday": 6,
               "sun": 7, "sunday": 7}
    m = re.match(r'every\s+(\w+)\s*(\d{1,2}):(\d{2})', text)
    if m:
        day_name = m.group(1).lower()
        if day_name in day_map:
            day = day_map[day_name]
            mi, h = m.group(3), m.group(2)
            return (f"{mi} {h} * * {day}", None)
    
    # Already a cron expression? Pass through
    if re.match(r'^[\d\*,/\-\s]+$', text):
        return (text, None)
    
    # Default: once, run now (agent probably meant immediate)
    run_at = datetime.now(timezone.utc).isoformat()
    return ("once", run_at)
```

- [ ] **Step 2: Update add() method to support auto_execute and natural time**

Modify `TaskQueue.add()`:

```python
def add(self, description: str, action: str, cron: str = "", 
        priority: int = 0, auto_execute: bool = False) -> dict:
    """Add task with optional auto_execute flag."""
    parsed_cron, once_at = parse_natural_time(cron)
    
    task = {
        "id": f"task_{int(time.time())}_{len(self._tasks)}",
        "description": description,
        "action": action,
        "cron": parsed_cron,
        "priority": priority,
        "auto_execute": auto_execute,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
        "next_run": once_at,
        "done": False,
    }
    
    # Dedup: same description + same cron → skip
    for existing in self._tasks:
        if not existing.get("done") and existing["description"] == description \
           and existing.get("cron") == parsed_cron:
            logger.info(f"Dedup: skipping duplicate task '{description}'")
            return existing
    
    # Max pending limit
    pending = [t for t in self._tasks if not t.get("done")]
    if len(pending) >= 20:
        oldest = min(pending, key=lambda t: t.get("created", ""))
        self.mark_done(oldest["id"])
        logger.info(f"Task limit reached, auto-closed oldest: {oldest['description']}")
    
    self._tasks.append(task)
    self._save()
    return task
```

- [ ] **Step 3: Update process_due() to handle once-tasks with next_run**

```python
def process_due(self, agent=None) -> list[dict]:
    """Process due tasks. Supports once-tasks with exact next_run timestamps."""
    now = datetime.now(timezone.utc)
    due = []
    for t in self._tasks:
        if t.get("done"):
            continue
        cron = t.get("cron", "") or "once"
        
        if cron == "once":
            next_run = t.get("next_run")
            if next_run:
                run_at = datetime.fromisoformat(next_run)
                if now >= run_at:
                    due.append(t)
            elif t.get("last_run") is None:
                due.append(t)
        elif cron == "daily":
            last = t.get("last_run")
            if last:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() > 86400:
                    due.append(t)
            else:
                due.append(t)
        elif cron == "hourly":
            last = t.get("last_run")
            if last:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() > 3600:
                    due.append(t)
            else:
                due.append(t)
        else:
            # Real cron expression — simple check: if last_run was before today
            last = t.get("last_run")
            if not last or datetime.fromisoformat(last).date() < now.date():
                due.append(t)
    return due
```

- [ ] **Step 4: Commit**

```bash
git add agent/task_queue.py
git commit -m "feat(task): add natural time parsing and auto_execute support"
```

---

### Task 2: schedule_task tool

**Files:**
- Create: `agent/tools/schedule_task_tool.py`

New tool so agent can manage its own scheduled tasks.

- [ ] **Step 1: Create the tool file**

```python
"""schedule_task tool — agent 自主创建/管理定时任务."""
import logging
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult
from agent.task_queue import TaskQueue

logger = logging.getLogger(__name__)


class ScheduleTaskTool(BaseTool):
    """Agent self-scheduling: create, list, cancel, or mark-done timed tasks."""

    @property
    def name(self) -> str:
        return "schedule_task"

    async def description(self) -> str:
        return "Schedule a future task for yourself. Create timed reminders, recurring checks, or delayed follow-ups."

    def is_read_only(self) -> bool:
        return False  # modifies task queue

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        action = input_args.get("action", "") if input_args else ""
        return action in ("cancel",)  # only cancel needs permission

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Create, list, cancel, or mark-done scheduled tasks for yourself. "
                    "Use this when you need to: follow up later, set a reminder, "
                    "create a daily check, or retry something after a delay.\n\n"
                    "Time formats: 'in 10 minutes', 'tomorrow 9am', 'daily', "
                    "'every Monday 8am', 'hourly'.\n\n"
                    "Set auto_execute=true ONLY for low-risk tasks (retry, check status). "
                    "Set auto_execute=false when the task involves file changes or needs user approval."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "list", "cancel", "done"],
                            "description": "add=create task, list=show pending, cancel=remove, done=mark complete",
                        },
                        "description": {
                            "type": "string",
                            "description": "Short description (for 'add' action). e.g. '重试GitHub搜索'",
                        },
                        "run_at": {
                            "type": "string",
                            "description": "When to run (for 'add'). e.g. 'in 10 minutes', 'tomorrow 9am', 'daily', 'every Monday 8am'",
                        },
                        "task": {
                            "type": "string",
                            "description": "What to do (for 'add'). A clear prompt for yourself describing the task.",
                        },
                        "auto_execute": {
                            "type": "boolean",
                            "description": "Execute without asking user? (for 'add'). Default false. Only true for low-risk retry/check tasks.",
                            "default": False,
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Task ID (for 'cancel'/'done' actions)",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        action = input_args.get("action", "")
        if action not in ("add", "list", "cancel", "done"):
            return {"result": False, "message": "action must be add/list/cancel/done"}

        if action == "add":
            if not input_args.get("description"):
                return {"result": False, "message": "description is required for 'add'"}
            if not input_args.get("task"):
                return {"result": False, "message": "task is required for 'add'"}

        if action in ("cancel", "done"):
            if not input_args.get("task_id"):
                return {"result": False, "message": "task_id is required for 'cancel'/'done'"}

        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]
        q = TaskQueue()

        try:
            if action == "add":
                description = input_args["description"]
                run_at = input_args.get("run_at", "")
                task_prompt = input_args["task"]
                auto_execute = input_args.get("auto_execute", False)

                result = q.add(
                    description=description,
                    action=task_prompt,
                    cron=run_at,
                    auto_execute=auto_execute,
                )

                auto_str = "自动执行" if auto_execute else "需确认"
                msg = (
                    f"✅ 定时任务已创建\n"
                    f"ID: {result['id']}\n"
                    f"描述: {description}\n"
                    f"执行时间: {run_at}\n"
                    f"模式: {auto_str}\n"
                    f"当前待办: {len(q.list())} 个"
                )

            elif action == "list":
                tasks = q.list()
                if not tasks:
                    msg = "📋 当前没有待执行的定时任务。"
                else:
                    lines = ["📋 待执行的定时任务:"]
                    for t in tasks:
                        auto = "🤖" if t.get("auto_execute") else "🔔"
                        cron = t.get("cron", "")
                        next_run = t.get("next_run", "") or cron
                        lines.append(
                            f"  {auto} [{t['id'][:12]}...] {t['description']} "
                            f"({next_run})"
                        )
                    msg = "\n".join(lines)

            elif action == "cancel":
                task_id = input_args["task_id"]
                q.remove(task_id)
                msg = f"🗑️ 已取消任务: {task_id[:20]}..."

            elif action == "done":
                task_id = input_args["task_id"]
                q.mark_done(task_id)
                msg = f"✅ 已标记完成: {task_id[:20]}..."

            yield ToolResult(type="result", data=msg, result_for_assistant=msg)

        except Exception as e:
            logger.error(f"schedule_task failed: {e}")
            yield ToolResult(type="result", data=f"Error: {e}", result_for_assistant=f"schedule_task failed: {e}")
```

- [ ] **Step 2: Register tool in main.py**

In `agent/main.py`, in `build_agent()`, add the import and registration:

```python
# Add import near other tool imports
from agent.tools.schedule_task_tool import ScheduleTaskTool

# Add registration near other registry.register() calls
registry.register(ScheduleTaskTool())
```

- [ ] **Step 3: Commit**

```bash
git add agent/tools/schedule_task_tool.py agent/main.py
git commit -m "feat(tool): add schedule_task tool for agent self-scheduling"
```

---

### Task 3: Daemon supports auto-execute + result delivery

**Files:**
- Modify: `agent/gateway.py:139-180`

Update the daemon loop to: skip permission prompt for auto_execute tasks, deliver results to QQ (if online) + learning notes.

- [ ] **Step 1: Update task processing in _daemon_loop**

Replace the task processing section (lines 139-180) in `_daemon_loop()`:

```python
# ── 2. 定时任务轮询逻辑 ──
try:
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
            # Need confirmation
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
            
            # Execute via agent
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
                try:
                    async with self._http.get(
                        f"{NC_HTTP_URL}/get_login_info",
                        headers={"Authorization": f"Bearer {NC_TOKEN}"} if NC_TOKEN else {}
                    ) as resp:
                        is_online = resp.status == 200
                except Exception:
                    is_online = False

                if is_online:
                    await self._send("private", admin_id, "",
                        f"✅ [任务完成] {desc}\n\n{result_summary}")

                # Always save result to learning notes
                try:
                    note_content = f"# 定时任务: {desc}\n\n执行时间: {datetime.now(timezone.utc).isoformat()}\n\n## 结果\n{result_summary}"
                    agent.memory.save_to_notes(
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
```

- [ ] **Step 2: Add datetime import at top of gateway.py**

```python
# Add to imports
from datetime import datetime, timezone
```

- [ ] **Step 3: Commit**

```bash
git add agent/gateway.py
git commit -m "feat(gateway): daemon supports auto-execute tasks and result delivery"
```

---

### Task 4: Remove hardcoded tasks, let agent self-initialize

**Files:**
- Check: `agent/task_queue.py` (QUEUE_DIR contents)
- Check: any init code that creates fixed tasks

- [ ] **Step 1: Check for hardcoded task creation**

```bash
grep -rn "queue.add\|TaskQueue\|\.add(" agent/ main.py start-agent.sh 2>/dev/null | grep -v __pycache__ | grep -v "schedule_task"
```

- [ ] **Step 2: Remove any hardcoded task creation found**

If any file creates tasks like `queue.add("清理旧会话", ...)`, remove those lines.

- [ ] **Step 3: Let agent create its own tasks on first run**

The agent's persona/system prompt should include: "On first conversation each day, check if you need recurring tasks (daily cleanup, log check). If so, use schedule_task to create them."

Add to persona or system prompt in `agent/core.py` or persona config.

- [ ] **Step 4: Commit**

```bash
git commit -m "fix(task): remove hardcoded tasks, agent self-initializes"
```

---

### Task 5: Integration test

**Files:** None (manual verification)

- [ ] **Step 1: Restart gateway**

```bash
pkill -f "main.py --gateway" && sleep 1
nohup venv/bin/python main.py --gateway >> gateway.log 2>&1 &
sleep 5
ps aux | grep "[m]ain.py --gateway"
```

- [ ] **Step 2: Verify import works**

```bash
venv/bin/python -c "from agent.tools.schedule_task_tool import ScheduleTaskTool; print('Tool OK')"
venv/bin/python -c "from agent.task_queue import parse_natural_time; print(parse_natural_time('in 10 minutes')); print(parse_natural_time('tomorrow 9am')); print(parse_natural_time('daily'))"
```

- [ ] **Step 3: Test via QQ**

Send bot: "小萤，用 schedule_task 创建一个 1 分钟后的测试任务，内容是 echo hello"

- [ ] **Step 4: Verify task was created and executed**

```bash
cat ~/bot-我的自搭建agent/agent培养/xl进化/任务队列/tasks.json | python3 -m json.tool
```

- [ ] **Step 5: Commit any remaining changes**

```bash
git status
git add -A
git commit -m "test(schedule): verify self-scheduling integration"
```

---

## Summary

| Task | Files | What |
|------|-------|------|
| 1 | `task_queue.py` | Natural time parsing + auto_execute field |
| 2 | `schedule_task_tool.py` (new), `main.py` | Tool definition + registration |
| 3 | `gateway.py` | Auto-execute + QQ delivery + notes archive |
| 4 | Various | Remove hardcoded tasks |
| 5 | Manual | Integration test |
