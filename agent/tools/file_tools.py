"""内置文件工具：read_file, write_file.

遵循 ACI 原则：
- 强制绝对路径（Poka-yoke）
- 文件大小限制
- start_line/end_line 行号切片
- LRU 防抖缓存（60s内同文件同大小拦截）
"""

import os
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

MAX_READ_SIZE = 30 * 1024  # 30KB


class ReadFileTool(BaseTool):
    def __init__(self):
        self._read_cache: dict[str, tuple[float, int]] = {}  # path → (timestamp, size)

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
                    "file_path MUST be an absolute path. "
                    f"Files larger than {MAX_READ_SIZE // 1024}KB will be truncated. "
                    "Use start_line/end_line to read specific line ranges from large files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to read.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional: first line number to read (1-indexed). Use for large files.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional: last line number to read (inclusive). Use with start_line.",
                        },
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
        start = input_args.get("start_line")
        end = input_args.get("end_line")
        path = Path(file_path)

        # ── 行号参数校验 ──
        if start is not None and (not isinstance(start, int) or start < 1):
            yield ToolResult(type="result", data="Error: start_line must be a positive integer")
            return
        if end is not None and (not isinstance(end, int) or end < 1):
            yield ToolResult(type="result", data="Error: end_line must be a positive integer")
            return
        if start is not None and end is not None and start > end:
            yield ToolResult(type="result", data=f"Error: start_line ({start}) must be <= end_line ({end})")
            return

        if not path.exists():
            yield ToolResult(
                type="result",
                data=(
                    f"Error: file not found: {file_path}. "
                    f"[行为纠正]: 禁止盲猜路径！请先使用 bash 工具执行 find 或 ls "
                    f"命令获取准确的目录结构！"
                ),
            )
            return

        if path.is_dir():
            yield ToolResult(type="result", data=f"Error: path is a directory: {file_path}")
            return

        try:
            size = path.stat().st_size

            # ── LRU 防抖：60s内同路径同大小同行号范围 → 拦截 ──
            cache_key = f"{file_path}:{start}:{end}"
            now = time.time()
            cached = self._read_cache.get(cache_key)
            if cached:
                cached_ts, cached_size = cached
                if now - cached_ts < 60 and cached_size == size:
                    yield ToolResult(
                        type="result",
                        data=(
                            f"[系统拦截]: 警告！你在过去的 60 秒内已经读取过 {file_path} "
                            f"({size} bytes)，内容未变化。请直接查阅上文对话记忆中的文件内容，"
                            f"严禁重复无意义的读取消耗 Token！"
                        ),
                    )
                    return

            # 更新缓存
            self._read_cache[cache_key] = (now, size)
            if len(self._read_cache) > 20:
                oldest = min(self._read_cache, key=lambda k: self._read_cache[k][0])
                del self._read_cache[oldest]

            # ── 读取（支持行号切片）──
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")

            if start is not None or end is not None:
                s = start or 1
                e = end if end is not None else len(lines)
                content = "\n".join(lines[s - 1:e])
                yield ToolResult(
                    type="result",
                    data=f"Lines {s}-{e} of {file_path} ({len(lines)} lines total):\n{content}",
                )
                return

            content = "\n".join(lines)
            if len(content) > MAX_READ_SIZE:
                content = content[:MAX_READ_SIZE] + f"\n\n... (truncated, total {size} bytes)"

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
