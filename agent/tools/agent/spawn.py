"""Spawn 子代理工具 — 支持内置角色和自定义角色 prompt.

同一进程新建 Agent 实例，隔离上下文，角色 prompt，await 返回结果。
"""

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

ROLES = {
    "coder": "你是一个资深软件工程师。写高质量、可维护的代码。简洁直接。",
    "reviewer": "你是一个代码审查专家。找出 bug、安全漏洞、性能问题。批判性思考。",
    "debugger": "你是一个调试专家。分析错误信息，定位根因，给出修复方案。",
    "architect": "你是一个系统架构师。从全局视角分析，关注扩展性、可靠性、性能。",
    "general": "你是一个通用助手。完成被分配的任务，不需要多余解释。",
}


class SpawnAgentTool(BaseTool):
    """派发子代理执行独立任务."""

    @property
    def name(self) -> str:
        return "spawn_agent"

    async def description(self) -> str:
        return "Spawn a sub-agent with a specific role to handle a task independently."

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False  # 子代理内部工具会各自审批

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"Spawn a sub-agent with a role: {', '.join(ROLES.keys())}. Use role_prompt for custom roles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "role": {
                            "type": "string",
                            "enum": list(ROLES.keys()),
                            "description": "Preset role. Use this OR role_prompt.",
                        },
                        "role_prompt": {
                            "type": "string",
                            "description": "Custom role prompt. Overrides 'role' if both given.",
                        },
                        "task": {
                            "type": "string",
                            "description": "The task description for the sub-agent",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional additional context"}, "tasks": {"type": "array", "description": "Parallel execution: list of {role, task} dicts",
                        },
                    },
                    "required": ["task"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        role = input_args.get("role", "")
        task = input_args.get("task", "")
        if role not in ROLES:
            return {"result": False, "message": f"Unknown role: {role}"}
        if not task.strip():
            return {"result": False, "message": "task is required"}
        return {"result": True, "message": ""}

    # 递归深度追踪 (hermes 模式: 最多 3 层)
    _depth: int = 0

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        role = input_args.get("role", "general")
        role_prompt = input_args.get("role_prompt", "")
        task = input_args["task"]
        tasks_list = input_args.get("tasks", [])  # 并发多任务
        ctx = input_args.get("context", "")

        # hermes: 防递归
        if SpawnAgentTool._depth >= 3:
            yield ToolResult(type="result", data="Error: max spawn depth reached")
            return

        # ── 并发多任务模式 ──
        if tasks_list:
            yield ToolResult(type="progress", data=f"🤖 并发 {len(tasks_list)} 个子代理工作中...")
            try:
                main = context if context else None
                if not main or not hasattr(main, "llm"):
                    yield ToolResult(type="result", data="Error: no agent context")
                    return
                from agent.core import Agent

                async def run_one(tinfo: dict):
                    r = tinfo.get("role", "general")
                    rp = tinfo.get("role_prompt", "")
                    t = tinfo.get("task", "")
                    p = rp if rp else ROLES.get(r, ROLES["general"])
                    sub = Agent(llm=main.llm, registry=main.registry, memory=main.memory, max_turns=4)
                    sub.system_prompt = p
                    parts = []
                    try:
                        sub._abort = asyncio.Event()
                        async for ev in asyncio.wait_for(self._collect_output(sub, t), timeout=90):
                            if isinstance(ev, str):
                                parts.append(ev)
                    except asyncio.TimeoutError:
                        parts.append(f"\n[timeout: {t[:30]}...]")
                    return (r, "".join(parts).strip())

                # 使用 asyncio.TaskGroup 彻底规避星号解包
                tasks = []
                async with asyncio.TaskGroup() as tg:
                    for t in tasks_list:
                        tasks.append(tg.create_task(run_one(t)))
                results = [task_obj.result() for task_obj in tasks]

                output = "\n\n".join(f"[{r}] {txt[:500]}" for r, txt in results if txt)
                yield ToolResult(
                    type="result",
                    data=f"并发完成 {len(results)} 个子代理:\n{output[:3000]}",
                    result_for_assistant=output[:5000],
                )
            except Exception as e:
                yield ToolResult(type="result", data=f"Multi spawn error: {e}")
            return

        yield ToolResult(type="progress", data=f"🤖 {role} 子代理工作中...")

        try:
            main = context if context else None
            if not main or not hasattr(main, "llm"):
                yield ToolResult(type="result", data="Error: no agent context")
                return

            from agent.core import Agent

            sub = Agent(llm=main.llm, registry=main.registry,
                        memory=main.memory, max_turns=5)
            prompt = role_prompt if role_prompt else ROLES.get(role, ROLES["general"])
            sub.system_prompt = prompt + "\n\n不能使用 spawn_agent 工具。"

            full_task = task
            if ctx:
                full_task = f"{task}\n\n上下文:\n{ctx}"

            # hermes: 超时 120s
            SpawnAgentTool._depth += 1
            output_parts = []
            tool_calls_made = []
            try:
                sub._abort = asyncio.Event()
                async for event in asyncio.wait_for(
                    self._collect_output(sub, full_task), timeout=120
                ):
                    if isinstance(event, str):
                        output_parts.append(event)
                    elif isinstance(event, dict):
                        tool_calls_made.append(event.get("name", "?"))
            except asyncio.TimeoutError:
                output_parts.append("\n[timeout: 120s]")
            finally:
                SpawnAgentTool._depth -= 1

            result_text = "".join(output_parts).strip() or "(no output)"
            summary = f" (用了 {len(tool_calls_made)} 次工具)" if tool_calls_made else ""

            # 格式化输出：结构化标记 (无星号粗体)
            formatted = (
                f"子代理 [{role}] 完成\n"
                f"  结论: {(result_text[:300] + '...') if len(result_text) > 300 else result_text}\n"
                f"  工具调用: {len(tool_calls_made)} 次\n"
                f"  输出长度: {len(result_text)} 字符"
            )
            yield ToolResult(
                type="result",
                data=f"[{role}] {result_text[:2000]}{summary}",
                result_for_assistant=formatted,
            )

        except Exception as e:
            logger.error(f"Spawn failed: {e}")
            yield ToolResult(type="result", data=f"Error: {e}")

    async def _collect_output(self, sub, full_task):
        """收集子代理输出事件."""
        async for event in sub.run(full_task):
            etype = event.get("type", "")
            if etype == "text_delta":
                yield str(event.get("content", ""))
            elif etype in ("tool_call", "tool_exec"):
                yield event
