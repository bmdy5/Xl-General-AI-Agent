"""Swarm 蜂群工具 — 调度者 + 多工作者并行执行复杂任务.

流程: 任务 → 调度者拆分子任务 → 并发 spawn worker → 聚合结果 → 返回.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class SwarmTool(BaseTool):
    """蜂群调度: 1 调度者 + N 工作者并行执行."""

    @property
    def name(self) -> str:
        return "swarm"

    async def description(self) -> str:
        return "Split a complex task into subtasks, run them concurrently, and combine results."

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return True

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Distribute a task across multiple sub-agents. "
                               "Each worker gets a piece and reports back. "
                               "Results are assembled into one answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The complex task to break down.",
                        },
                        "workers": {
                            "type": "integer",
                            "description": "Number of parallel workers (2-5). Default 3.",
                        },
                        "worker_roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional custom prompts for each worker.",
                        },
                    },
                    "required": ["task"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if not input_args.get("task", "").strip():
            return {"result": False, "message": "task is required"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        task = input_args["task"]
        worker_count = min(input_args.get("workers", 3), 5)
        worker_roles = input_args.get("worker_roles", [])

        main = context if context else None
        if not main or not hasattr(main, "registry") or not hasattr(main, "llm"):
            yield ToolResult(type="result", data="Error: no agent context")
            return

        from agent.core import Agent

        yield ToolResult(type="progress", data=f"🐝 Swarm: 分配 {worker_count} 个 worker 处理任务...")

        # 生成子任务描述
        subtasks = [
            f"你是 Worker-{i+1}，负责该任务的第 {i+1} 个方面。\n\n原始任务: {task}\n\n"
            f"请从你的视角分析并输出结论。简洁直接。"
            for i in range(worker_count)
        ]
        if worker_roles:
            for i, role in enumerate(worker_roles):
                if i < len(subtasks):
                    subtasks[i] = role + "\n\n任务: " + task

        # 并发执行
        async def run_worker(idx: int, subtask: str):
            sub = Agent(
                llm=main.llm,
                registry=main.registry,
                memory=main.memory,
                max_turns=4,
            )
            sub.system_prompt = f"你是蜂群 Worker-{idx+1}。完成分配给您的子任务。"
            parts = []
            try:
                sub._abort = asyncio.Event()
                async for ev in asyncio.wait_for(
                    self._collect(sub, subtask), timeout=90
                ):
                    if isinstance(ev, str):
                        parts.append(ev)
            except asyncio.TimeoutError:
                parts.append(f"\n[Worker-{idx+1} 超时]")
            return (idx + 1, "".join(parts).strip())

        results = await asyncio.gather(*[run_worker(i, s) for i, s in enumerate(subtasks)])

        # 聚合
        output_parts = []
        for idx, text in results:
            if text:
                output_parts.append(f"── Worker-{idx} ──\n{text[:800]}")

        combined = "\n\n".join(output_parts) if output_parts else "(workers 无输出)"

        yield ToolResult(
            type="result",
            data=f"🐝 Swarm 完成 ({worker_count} workers):\n{combined[:3000]}",
            result_for_assistant=f"蜂群执行结果:\n\n{combined[:5000]}",
        )

    async def _collect(self, sub, task: str):
        async for event in sub.run(task):
            if event.get("type") == "text_delta":
                yield str(event.get("content", ""))
