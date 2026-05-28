"""schedule_task tool — agent 自主创建/管理定时任务."""
import logging
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult
from agent.core.task_queue import TaskQueue

logger = logging.getLogger(__name__)


class ScheduleTaskTool(BaseTool):
    """Agent self-scheduling: create, list, cancel, or mark-done timed tasks."""

    @property
    def name(self) -> str:
        return "schedule_task"

    async def description(self) -> str:
        return "为自己安排未来的定时任务。可以创建定时提醒、周期性检查或延迟的跟进任务。"

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
                    "为自己创建、列出、取消或标记完成定时任务。"
                    "当你需要：稍后跟进、设置提醒、创建每日检查，或在延迟后重试某事时使用。\n\n"
                    "时间格式：'in 10 minutes'（10分钟后）, 'tomorrow 9am'（明天上午9点）, 'daily'（每天）, "
                    "'every Monday 8am'（每周一早上8点）, 'hourly'（每小时）。\n\n"
                    "仅当任务风险极低（如重试、检查状态）时，才将 auto_execute 设为 true。"
                    "如果任务涉及修改文件或需要用户审批，必须将 auto_execute 设为 false。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "list", "cancel", "done"],
                            "description": "add=创建任务, list=显示待办, cancel=删除任务, done=标记完成",
                        },
                        "description": {
                            "type": "string",
                            "description": "简短描述（用于 'add'）。例如 '重试GitHub搜索'",
                        },
                        "run_at": {
                            "type": "string",
                            "description": "何时执行（用于 'add'）。例如 'in 10 minutes', 'tomorrow 9am', 'daily'",
                        },
                        "task": {
                            "type": "string",
                            "description": "要做什么（用于 'add'）。给自己写的明确的任务提示词。",
                        },
                        "auto_execute": {
                            "type": "boolean",
                            "description": "是否不经用户询问自动执行？默认 false。仅对低风险的重试/检查任务设为 true。",
                            "default": False,
                        },
                        "task_id": {
                            "type": "string",
                            "description": "任务 ID（用于 'cancel'/'done' 操作）",
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
