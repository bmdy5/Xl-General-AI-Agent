"""ToolRegistry — simplified from hermes-agent.

中心化工具注册表：register → get_definitions → dispatch。
去掉 hermes 原版的 TTL 缓存、线程安全、toolset 管理——对单用户 agent 不需要。
"""

from typing import Any, Callable, Optional

from .base_tool import BaseTool


class ToolRegistry:
    """工具注册中心（简化版）.

    用法：
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        tools = registry.get_definitions()
        result = await registry.dispatch("read_file", {"file_path": "/tmp/x.txt"})
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._cached_definitions = None

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def deregister(self, name: str) -> None:
        """移除一个工具."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Optional[BaseTool]:
        """按名称获取工具实例."""
        return self._tools.get(name)

    def get_definitions(self) -> list[dict]:
        """返回所有已注册工具的 OpenAI function calling 格式定义."""
        if self._cached_definitions is None:
            self._cached_definitions = [tool.get_tool_definition() for tool in self._tools.values()]
        return self._cached_definitions

    def list_names(self) -> list[str]:
        """列出所有已注册工具的名称."""
        return list(self._tools.keys())

    async def dispatch(self, name: str, args: dict, context: Any = None) -> str:
        """执行工具并返回结果字符串.

        对 agent 核心循环来说，这是最直接的调用方式：
        result_str = await registry.dispatch("bash", {"command": "ls"})
        """
        tool = self._tools.get(name)
        if not tool:
            return f'{{"error": "Unknown tool: {name}"}}'

        try:
            val_res = await tool.validate_input(args, context)
            if not val_res.get("result", True):
                return f'{{"error": "Invalid input arguments: {val_res.get("message", "Validation failed")}"}}'

            async for tr in tool.call(args, context):
                if tr.type == "result":
                    return tr.result_for_assistant or str(tr.data)
            return "(tool executed, no result)"
        except Exception as e:
            return f'{{"error": "{type(e).__name__}: {e}"}}'


# 模块级单例
registry = ToolRegistry()
