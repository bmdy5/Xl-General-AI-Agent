"""BaseTool ABC — adapted from tinypace-ai-agent.

统一工具接口：所有工具（内置、MCP）都实现这个抽象基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional


@dataclass
class ToolResult:
    """工具执行结果，支持 progress 流式推送."""
    type: str  # "result" | "progress"
    data: Any
    result_for_assistant: Optional[str] = None


class BaseTool(ABC):
    """工具基类 — 定义统一的工具接口.

    设计原则（来自 ACI）：
    - is_read_only() → 决定能否并发执行
    - is_concurrency_safe() → 默认 False（Fail-Closed）
    - needs_permissions() → 危险操作需要用户审批
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def description(self) -> str: ...

    @abstractmethod
    def is_read_only(self) -> bool:
        """只读工具可并发执行."""
        ...

    @abstractmethod
    def is_concurrency_safe(self) -> bool:
        """默认 False：新工具默认不安全，开发者必须显式声明安全（Fail-Closed）."""
        ...

    @abstractmethod
    def needs_permissions(self, input_args: Optional[dict] = None) -> bool: ...

    @abstractmethod
    def get_tool_definition(self) -> dict:
        """返回 OpenAI function calling 格式的工具定义.

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}  # JSON Schema
            }
        }
        """
        ...

    @abstractmethod
    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        """验证输入参数. 返回 {"result": bool, "message": str}."""
        ...

    @abstractmethod
    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        """调用工具，yield ToolResult(type="progress"|"result")."""
        ...
