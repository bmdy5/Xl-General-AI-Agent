"""Bash 工具 — 执行 shell 命令。

安全策略（来自 CC 的分析）：
- 超时限制（默认 60s）
- 输出截断（默认 50KB）
- 需要用户审批（needs_permissions=True）
- 工作目录限制在项目内
"""

import asyncio
import re
import operator
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

BASH_TIMEOUT = 60  # 最长执行时间
MAX_OUTPUT = 10240  # 输出截断 10KB（省钱）


class BashTool(BaseTool):
    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = work_dir or "."

    @property
    def name(self) -> str:
        return "bash"

    async def description(self) -> str:
        return "Execute a shell command and return its output."

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        cmd = (input_args or {}).get("command", "")
        return self.classify_command(cmd) == "dangerous"

    # ── 命令分类 ───────────────────────────────────────────────

    SAFE_PREFIXES: frozenset = frozenset({
        'ls', 'cat', 'grep', 'find', 'wc', 'echo', 'head', 'tail',
        'sort', 'uniq', 'which', 'pwd', 'date', 'whoami', 'id',
        'env', 'printenv', 'uname', 'hostname', 'uptime', 'df', 'du',
        'ps', 'top', 'free', 'pgrep', 'stat', 'file', 'type',
    })

    DANGEROUS_PATTERNS: list = [
        # 文件删除操作需要明确允许，使用无星号正则
        re.compile(r'\brm\b'),
        re.compile(r'\brmdir\b'),
        re.compile(r'\bshred\b'),
        re.compile(r'`.{0,}rm\b'),
    ]


    SAFE_REGEX: list = [
        re.compile(r'^git\s+(status|log|diff|show|branch|tag|remote\b|stash\s+list|rev-parse|config\s+--get)', re.IGNORECASE),
        re.compile(r'^(pip|pip3)\s+(list|show|freeze)\b'),
        re.compile(r'^(npm|yarn|pnpm)\s+(list|view|info|outdated|why)\b'),
        re.compile(r'^docker\s+(ps|images|info|logs|stats|inspect)\b'),
        re.compile(r'^gh\s+(pr\s+view|issue\s+view|api\s+get|auth\s+status)\b'),
    ]

    @staticmethod
    def classify_command(command: str) -> str:
        """返回 'safe', 'write', 或 'dangerous'."""
        cmd = command.strip()
        if not cmd:
            return "safe"

        # 1. 经典显式删除与进程终止拦截
        for pat in BashTool.DANGEROUS_PATTERNS:
            if pat.search(cmd):
                return "dangerous"

        # 2. 经典 Python 代码删除行为 (os.remove / shutil.rmtree / unlink)
        if re.search(r'\bos\.remove\b|\bos\.unlink\b|\bshutil\.rmtree\b|\bPath\.unlink\b', cmd):
            return "dangerous"

        # 3. 隐式物理重定向与转移覆写保护区拦截
        protected_keywords = {
            "main.py", "Makefile", "Dockerfile", "docker-compose.yml",
            "pytest.ini", "requirements.txt", ".gitignore", ".env.example", "agent/"
        }
        has_redirect = ">" in cmd
        has_move = re.search(r'\bmv\b', cmd)
        if (has_redirect or has_move) and any(kw in cmd for kw in protected_keywords):
            return "dangerous"

        first_word = cmd.split()[0] if cmd.split() else ""
        first_word = first_word.lstrip('\\')
        if first_word in BashTool.SAFE_PREFIXES:
            return "safe"

        for pat in BashTool.SAFE_REGEX:
            if pat.match(cmd):
                return "safe"

        return "write"


    # ── tool definition ────────────────────────────────────────

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Execute a shell command in the working directory. "
                    f"Timeout: {BASH_TIMEOUT}s. Output truncated to {MAX_OUTPUT // 1024}KB. "
                    "Use with caution — the user must approve each execution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute.",
                        }
                    },
                    "required": ["command"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        command = input_args.get("command", "")
        if not command or not command.strip():
            return {"result": False, "message": "command is required"}
        return {"result": True, "message": ""}

    # sed -n 读文本文件，使用无星号正则
    _SED_ABUSE_RE = re.compile(r'sed\s+-n\b.{0,}\.(md|py|java|go|ts|js|json|yaml|yml|txt|log)',
                                re.IGNORECASE)

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        command = input_args["command"]

        # 智能防错纠偏：大模型常由于幻觉误用不支持的 git show ... --no-stat 导致 exit code 128
        # 此处在底层静默将 --no-stat 剔除，确保命令 100% 执行成功
        if "git show" in command and "--no-stat" in command:
            import logging
            local_logger = logging.getLogger(__name__)
            corrected_command = command.replace("--no-stat", "").strip()
            local_logger.info(f"[Auto-Correction] Corrected git command from '{command}' to '{corrected_command}'")
            command = corrected_command

        # sed -n 读文本文件 → 拦截并引导至 read_file
        if self._SED_ABUSE_RE.search(command):
            yield ToolResult(
                type="result",
                data=(
                    f"[行为纠正]: 严禁使用 bash sed 分段读取文件来规避长度限制！"
                    f"这会导致上下文爆炸。请直接使用 read_file 工具，"
                    f"并传入 start_line 和 end_line 参数进行精确行号切片！"
                ),
            )
            return

        try:
            import os
            # 注入 Homebrew 路径以防找不到 pip/ffmpeg 等命令
            custom_env = os.environ.copy()
            custom_env["PATH"] = "/opt/homebrew/bin:" + custom_env.get("PATH", "")

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.work_dir,
                env=custom_env,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(), timeout=BASH_TIMEOUT
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                yield ToolResult(
                    type="result",
                    data=f"Error: command timed out after {BASH_TIMEOUT}s\nCommand: {command}",
                )
                return

            output = stdout.decode("utf-8", errors="replace")
            if len(output) > MAX_OUTPUT:
                output = output[:MAX_OUTPUT] + f"\n\n... (truncated, {len(output)} bytes total)"

            yield ToolResult(
                type="result",
                data=f"$ {command}\n{output}\n(exit code: {process.returncode})",
            )

        except FileNotFoundError:
            yield ToolResult(type="result", data=f"Error: command not found: {command}")
        except Exception as e:
            yield ToolResult(type="result", data=f"Error executing command: {e}")
