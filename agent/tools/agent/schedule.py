"""schedule_task tool — agent 自主创建/管理定时任务."""
import logging
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult
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
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        action = input_args.get("action", "") if input_args else ""
        return action in ("cancel",)

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
                            "description": "Short description (for 'add'). e.g. '重试GitHub搜索'",
                        },
                        "run_at": {
                            "type": "string",
                            "description": "When to run (for 'add'). e.g. 'in 10 minutes', 'tomorrow 9am', 'daily'",
                        },
                        "task": {
                            "type": "string",
                            "description": "What to do (for 'add'). A clear prompt for yourself describing the task.",
                        },
                        "auto_execute": {
                            "type": "boolean",
                            "description": "Execute without asking user? Default false. Only true for low-risk retry/check tasks.",
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
                    f"doc_checked_passed_success 定时任务已创建\n"
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
