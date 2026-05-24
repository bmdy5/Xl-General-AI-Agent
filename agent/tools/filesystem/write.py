"""内置文件工具：write_file.

遵循 ACI 原则：
- 强制绝对路径（Poka-yoke）
- 自动维护父目录
- 保护核心文件
"""

import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


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
        file_path = (input_args or {}).get("file_path", "")
        if not file_path:
            return True
        from pathlib import Path
        try:
            p = Path(file_path).resolve()
        except Exception:
            p = Path(file_path).absolute()
            
        root_dir = Path(__file__).resolve().parents[3]
        
        # 1. 检查是否在 agent/ 源码目录内
        agent_dir = root_dir / "agent"
        try:
            if agent_dir.resolve() in p.parents or p.resolve() == agent_dir.resolve():
                return True
        except Exception:
            if "agent/" in str(p) or "/agent" in str(p):
                return True

        # 2. 检查是否是根目录的关键系统元配置文件
        protected_root_files = {
            "main.py", "Makefile", "Dockerfile", "docker-compose.yml",
            "pytest.ini", "requirements.txt", ".gitignore", ".env.example"
        }
        try:
            is_in_root = p.parent.resolve() == root_dir.resolve()
        except Exception:
            is_in_root = p.parent.absolute() == root_dir.absolute()

        if is_in_root and p.name in protected_root_files:
            return True
            
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Write content to a file. Creates the file if it doesn't exist, "
                    "overwrites if it does. Requires approval ONLY if writing to core "
                    "source directories (like agent/) or root system files. "
                    "Writes to logs/ or note directories are auto-approved. "
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
        # 保护核心人设/系统文件不被 write_file 直接覆盖
        from ...memory.manager import PROTECTED_FILES
        _filename = Path(file_path).name
        if _filename in PROTECTED_FILES:
            return {
                "result": False,
                "message": (
                    f"{_filename} 是保护文件，禁止直接写入。"
                    f"如需修改人设，请用 save_memory action='merge_to_core' target_file='xl_identity.md'。"
                    f"tone_style/性格相关修改请走 persona_profile.json 的手动 review 流程。"
                ),
            }
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
