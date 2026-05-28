"""ManageTool — 运行时注册/注销/列出工具（工具工厂）.

让 XL 能在运行时动态管理工具集。
"""

import logging
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ManageToolTool(BaseTool):
    """运行时工具管理."""

    @property
    def name(self) -> str:
        return "manage_tool"

    async def description(self) -> str:
        return "Register, deregister, or list tools at runtime."

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
                "description": "在运行时动态管理底层的工具箱。支持的操作：list (列出所有工具), register (注册新工具), deregister (卸载工具)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "register", "deregister"],
                            "description": "list: 显示当前挂载的所有工具；register: 注册并挂载新工具；deregister: 卸载指定的工具。",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "工具的名称（用于 register/deregister）。",
                        },
                        "tool_code": {
                            "type": "string",
                            "description": "新工具类的 Python 源码（仅限 register 操作，必须是继承自 BaseTool 的合法 Python 类）。",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if input_args.get("action") == "register" and not input_args.get("tool_code"):
            return {"result": False, "message": "tool_code required for register"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]
        registry = getattr(context, "registry", None) if context else None
        if not registry:
            yield ToolResult(type="result", data="Error: no registry available")
            return

        if action == "list":
            names = registry.list_names()
            yield ToolResult(type="result", data=f"Registered tools ({len(names)}):\n" + "\n".join(f"  - {n}" for n in sorted(names)))

        elif action == "deregister":
            name = input_args.get("tool_name", "")
            if not name:
                yield ToolResult(type="result", data="Error: tool_name required")
                return
            registry.deregister(name)
            yield ToolResult(type="result", data=f"Tool deregistered: {name}")

        elif action == "register":
            name = input_args.get("tool_name", "")
            code = input_args.get("tool_code", "")
            if not name or not code:
                yield ToolResult(type="result", data="Error: tool_name and tool_code required")
                return
            try:
                exec_globals = {}
                exec(code, exec_globals)
                for cls_name, cls in exec_globals.items():
                    if isinstance(cls, type) and hasattr(cls, "name"):
                        registry.register(cls())
                        yield ToolResult(type="result", data=f"Tool registered: {getattr(cls, 'name', cls_name)}")
                        return
                yield ToolResult(type="result", data=f"Error: no valid tool class found in code")
            except Exception as e:
                yield ToolResult(type="result", data=f"Error registering tool: {e}")

        else:
            yield ToolResult(type="result", data=f"Unknown action: {action}")
