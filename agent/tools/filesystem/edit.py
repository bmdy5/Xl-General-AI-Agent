"""EditFileTool — SEARCH/REPLACE 精准编辑（手术刀模式）.

设计原则（参考 Claude Code FileEditTool）：
- old_string → new_string，只改指定段落，别处不动
- old_string 在文件中必须恰好出现一次，否则报错
- 绝对路径必填（Poka-yoke）
- 需要写权限（needs_permissions = True）
"""

from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult


class EditFileTool(BaseTool):
    """精准文件编辑工具 — SEARCH/REPLACE 模式."""

    @property
    def name(self) -> str:
        return "edit_file"

    async def description(self) -> str:
        return (
            "编辑文件——找到旧文本，替换为新文本。"
            "old_string 必须在文件中恰好出现一次。"
            "对于小幅修改，优先用此工具而非 write_file。"
        )

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
                    "Use SEARCH/REPLACE to make precise edits to a text file. "
                    "The `search` text must match exactly (including whitespace) and appear "
                    "exactly once in the file. Use this for small, targeted changes — "
                    "it avoids rewriting the entire file. "
                    "file_path MUST be an absolute path. "
                    "For creating a new file, use write_file instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to edit.",
                        },
                        "search": {
                            "type": "string",
                            "description": (
                                "The exact text to find and replace. Must match byte-for-byte "
                                "in the file and appear exactly once."
                            ),
                        },
                        "replace": {
                            "type": "string",
                            "description": "The text to substitute in place of `search`.",
                        },
                    },
                    "required": ["file_path", "search", "replace"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        file_path = input_args.get("file_path", "")
        search = input_args.get("search")
        replace = input_args.get("replace")

        if not file_path:
            return {"result": False, "message": "file_path is required"}
        if not Path(file_path).is_absolute():
            return {"result": False, "message": "file_path must be absolute"}
        if search is None:
            return {"result": False, "message": "search is required"}
        if replace is None:
            return {"result": False, "message": "replace is required"}
        if not search:
            return {"result": False, "message": "search cannot be empty"}

        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        file_path = input_args["file_path"]
        search = input_args["search"]
        replace = input_args["replace"]
        path = Path(file_path)

        # ── 前置检查 ──
        if not path.exists():
            yield ToolResult(type="result", data=f"Error: file not found: {file_path}")
            return

        if path.is_dir():
            yield ToolResult(type="result", data=f"Error: path is a directory: {file_path}")
            return

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            yield ToolResult(type="result", data=f"Error: binary file, cannot edit as text: {file_path}")
            return
        except PermissionError:
            yield ToolResult(type="result", data=f"Error: permission denied: {file_path}")
            return

        # ── 查找 old_string ──
        count = content.count(search)

        if count == 0:
            yield ToolResult(
                type="result",
                data=(
                    f"Error: search text not found in file. "
                    f"Tried to find: {search[:80]}..."
                ),
            )
            return

        if count > 1:
            yield ToolResult(
                type="result",
                data=(
                    f"Error: search text found {count} times in file. "
                    f"It must appear exactly once. \n\n"
                    f"提示：search 文本太短了，加上前后几行上下文让它唯一。\n"
                    f"例如改成：\n"
                    f"  search='background: #16213e;\\n    border: 2px solid #533483;\\n    border-radius: 4px;\\n    overflow: hidden;'\n"
                    f"  search='background: #1a1a2e;\\n    border-radius: 4px;\\n    color: #e0e0e0;'"
                ),
            )
            return

        # ── 执行替换 ──
        new_content = content.replace(search, replace, 1)
        try:
            path.write_text(new_content, encoding="utf-8")
        except PermissionError:
            yield ToolResult(type="result", data=f"Error: permission denied: {file_path}")
            return
        except Exception as e:
            yield ToolResult(type="result", data=f"Error writing file: {e}")
            return

        yield ToolResult(
            type="result",
            data=f"File edited: {file_path}. Replaced {len(search)}→{len(replace)} chars.",
        )
