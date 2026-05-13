"""Bash 工具 — 执行 shell 命令。

安全策略（来自 CC 的分析）：
- 超时限制（默认 60s）
- 输出截断（默认 50KB）
- 需要用户审批（needs_permissions=True）
- 工作目录限制在项目内
"""

import asyncio
import re
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

BASH_TIMEOUT = 60  # 最长执行时间
MAX_OUTPUT = 50 * 1024  # 输出截断 50KB


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
        return True  # 所有 bash 命令都需要审批

    # ── 命令分类 ───────────────────────────────────────────────

    SAFE_PREFIXES: frozenset = frozenset({
        'ls', 'cat', 'grep', 'find', 'wc', 'echo', 'head', 'tail',
        'sort', 'uniq', 'which', 'pwd', 'date', 'whoami', 'id',
        'env', 'printenv', 'uname', 'hostname', 'uptime', 'df', 'du',
        'ps', 'top', 'free', 'pgrep', 'stat', 'file', 'type',
    })

    DANGEROUS_PATTERNS: list = [
        re.compile(r'\brm\b'),
        re.compile(r'\brmdir\b'),
        re.compile(r'\btruncate\b'),
        re.compile(r'\bdd\b'),
        re.compile(r'\bshred\b'),
        re.compile(r'>\s*/dev/(sd|hd|nvme|mmcblk|loop|dm-)'),
        re.compile(r'\bchmod\s+[0-7]'),
        re.compile(r'\bchown\b'),
        re.compile(r'\bkill(all)?\b'),
        re.compile(r'\bpkill\b'),
        re.compile(r'\breboot\b'),
        re.compile(r'\bshutdown\b'),
        re.compile(r'\bfdisk\b'),
        re.compile(r'\bmount\b'),
        re.compile(r'\bumount\b'),
        re.compile(r'\bsudo\b'),
        re.compile(r'\bsu\b'),
        re.compile(r'\bpasswd\b'),
        re.compile(r'\buseradd\b|\buserdel\b'),
        re.compile(r'\biptables\b'),
        re.compile(r'\bsystemctl\s+(stop|disable|mask)\b'),
        re.compile(r'`.*rm\b'),
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

        for pat in BashTool.DANGEROUS_PATTERNS:
            if pat.search(cmd):
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

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        command = input_args["command"]

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.work_dir,
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
