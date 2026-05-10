"""内置文件工具：read_file, write_file.

遵循 ACI 原则：
- 强制绝对路径（Poka-yoke）
- 文件大小限制（read_file 默认 100KB）
- 清晰的错误消息
"""

import os
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

MAX_READ_SIZE = 100 * 1024  # 100KB


class ReadFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_file"

    async def description(self) -> str:
        return "Read the contents of a file at the given absolute path."

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Read a file from the local filesystem. "
                    "file_path MUST be an absolute path, relative paths are not accepted. "
                    f"Files larger than {MAX_READ_SIZE // 1024}KB will be truncated."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to read.",
                        }
                    },
                    "required": ["file_path"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        file_path = input_args.get("file_path", "")
        if not file_path:
            return {"result": False, "message": "file_path is required"}
        if not Path(file_path).is_absolute():
            return {"result": False, "message": "file_path must be absolute, e.g. /home/user/file.txt"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        file_path = input_args["file_path"]
        path = Path(file_path)

        if not path.exists():
            yield ToolResult(type="result", data=f"Error: file not found: {file_path}")
            return

        if path.is_dir():
            yield ToolResult(type="result", data=f"Error: path is a directory: {file_path}")
            return

        try:
            size = path.stat().st_size
            if size > MAX_READ_SIZE:
                content = path.read_text(encoding="utf-8", errors="replace")
                content = content[:MAX_READ_SIZE] + f"\n\n... (truncated, total {size} bytes)"
            else:
                content = path.read_text(encoding="utf-8", errors="replace")

            yield ToolResult(type="result", data=content)
        except UnicodeDecodeError:
            yield ToolResult(type="result", data=f"Error: binary file, cannot read as text: {file_path}")
        except PermissionError:
            yield ToolResult(type="result", data=f"Error: permission denied: {file_path}")
        except Exception as e:
            yield ToolResult(type="result", data=f"Error reading file: {e}")


class WriteFileTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_file"

    async def description(self) -> str:
        return "Create or overwrite a file at the given absolute path with the specified content."

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
                    "Write content to a file. Creates the file if it doesn't exist, "
                    "overwrites if it does. Requires user approval before writing. "
                    "file_path MUST be an absolute path."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to write.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The text content to write to the file.",
                        },
                    },
                    "required": ["file_path", "content"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        file_path = input_args.get("file_path", "")
        content = input_args.get("content")
        if not file_path:
            return {"result": False, "message": "file_path is required"}
        if not Path(file_path).is_absolute():
            return {"result": False, "message": "file_path must be absolute"}
        if content is None:
            return {"result": False, "message": "content is required"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        file_path = input_args["file_path"]
        content = input_args["content"]
        path = Path(file_path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            size = path.stat().st_size
            yield ToolResult(
                type="result",
                data=f"File written: {file_path} ({size} bytes)",
            )
        except PermissionError:
            yield ToolResult(type="result", data=f"Error: permission denied: {file_path}")
        except Exception as e:
            yield ToolResult(type="result", data=f"Error writing file: {e}")
