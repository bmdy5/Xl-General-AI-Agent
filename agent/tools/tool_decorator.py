"""@tool 装饰器 — 把任意函数快速变成工具."""
from typing import Any, AsyncGenerator, Optional
from .base_tool import BaseTool, ToolResult

def tool(tool_name: str, tool_desc: str, read_only: bool = True):
    _name = tool_name
    _desc = tool_desc
    _ro = read_only

    def decorator(func):
        class DecoratedTool(BaseTool):
            @property
            def name(self) -> str:
                return _name
            async def description(self) -> str:
                return _desc
            def is_read_only(self) -> bool:
                return _ro
            def is_concurrency_safe(self) -> bool:
                return _ro
            def needs_permissions(self, input_args = None) -> bool:
                return not _ro
            def get_tool_definition(self) -> dict:
                return {"type":"function","function":{"name":_name,"description":_desc,
                        "parameters":{"type":"object","properties":{"prompt":{"type":"string","description":"Input."}}}}}
            async def validate_input(self, input_args, context=None):
                return {"result":True,"message":""}
            async def call(self, input_args, context=None):
                try:
                    import inspect
                    if inspect.iscoroutinefunction(func):
                        result = await func(input_args)
                    else:
                        result = func(input_args)
                    yield ToolResult(type="result", data=str(result))
                except Exception as e:
                    yield ToolResult(type="result", data=f"Error: {e}")
        return DecoratedTool
    return decorator
