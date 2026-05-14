"""RunSequence — 链式执行多个工具调用（减少 LLM 往返）.

XL 可以一次性定义多个步骤，tools 会按顺序执行并返回所有结果。
参考: tinypace TaskTracker, CC coordinator
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class RunSequenceTool(BaseTool):
    """链式执行多个工具调用."""

    @property
    def name(self) -> str:
        return "run_sequence"

    async def description(self) -> str:
        return "Execute multiple tool calls in sequence. Pass a list of {tool, args} steps."

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return True

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Execute multiple tools in sequence. Each step: {tool: str, args: dict}. "
                    "Steps run one after another. All results are returned as a numbered list. "
                    "Use this when you need to do several operations that depend on each other."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "List of steps. Each step: {tool: str (tool name), args: dict (tool arguments)}",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tool": {"type": "string"},
                                    "args": {"type": "object"},
                                },
                                "required": ["tool", "args"],
                            },
                        },
                    },
                    "required": ["steps"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        steps = input_args.get("steps", [])
        if not steps:
            return {"result": False, "message": "steps is required and cannot be empty"}
        if not isinstance(steps, list):
            return {"result": False, "message": "steps must be a list"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        steps = input_args["steps"]
        registry = getattr(context, "registry", None) if context else None
        if not registry:
            yield ToolResult(type="result", data="Error: no tool registry available")
            return

        results = []
        for i, step in enumerate(steps):
            tool_name = step.get("tool", "")
            tool_args = step.get("args", {})
            try:
                result_str = await registry.dispatch(tool_name, tool_args, context=context)
                results.append(f"Step {i+1} ({tool_name}): {result_str[:300]}")
            except Exception as e:
                results.append(f"Step {i+1} ({tool_name}) FAILED: {e}")
                break

        yield ToolResult(
            type="result",
            data="\n".join(results),
            result_for_assistant="\n".join(results),
        )
