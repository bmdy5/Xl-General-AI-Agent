"""XL TUI — 干净、简约的对话界面."""

import time
import sys
from rich.console import Console
from rich.text import Text
from rich import box

console = Console(highlight=False)

# 调色
C_USER = "bright_blue"
C_AI = "bright_magenta"
C_TOOL = "yellow"
C_DIM = "grey58"
C_HEAD = "blue"


def h(title: str = "XL Agent", subtitle: str = "") -> str:
    out = f"[bold]{title}[/bold]"
    if subtitle:
        out += f"  [dim]{subtitle}[/dim]"
    return out


def hr(char: str = "─") -> str:
    return f"[dim]{' ' + char*50}[/dim]"


def user_msg(text: str) -> str:
    """用户消息."""
    lines = text.split("\n")
    out = f"  [bold {C_USER}]┃[/] {lines[0]}"
    for l in lines[1:]:
        out += f"\n  [bold {C_USER}]┃[/] {l}"
    return out


def thinking_live() -> str:
    """思考中（清空后重绘，终端里用 \\r 刷新）. """
    return f"  \033[36m⟳ 思考中...\033[0m"


def tool_call(name: str, args_preview: str) -> str:
    """工具调用 — 一行."""
    return f"  [{C_TOOL}]▸ {name}[/{C_TOOL}]  [dim]{args_preview}[/dim]"


def ai_msg(text: str, elapsed: float, tools: int, tokens: int, ctx: int) -> str:
    """AI 回复."""
    lines = text.split("\n")
    out = f"  [bold {C_AI}]┃[/] {lines[0]}"
    for l in lines[1:]:
        out += f"\n  [bold {C_AI}]┃[/] {l}"
    meta = f"\n  [dim]⏱ {elapsed:.1f}s · ~{tokens}t · {ctx}% ctx · {tools} tools[/dim]"
    out += meta
    return out


def error_msg(msg: str) -> str:
    return f"  [bold red]✗ {msg}[/bold red]"


def cmd_bar() -> str:
    return f"[dim]/exit | /clear | /memory | /tools | /mode[/dim]"


def input_box(mode: str, model: str, ctx_pct: int, tool_count: int) -> str:
    """方框输入区."""
    ctx_bar = "█" * (ctx_pct // 5) + "░" * (20 - ctx_pct // 5)
    ctx_bar = ctx_bar[:20]
    # 顶部栏
    header = f"[dim]┌─[/dim] [{C_HEAD}]{mode}[/{C_HEAD}] [dim]─[/dim] [dim]Model: {model}[/dim] [dim]─[/dim] [dim]{tool_count} tools[/dim]"
    # 中间 ctx 进度
    body = f"[dim]│ ctx: {ctx_bar} {ctx_pct}%[/dim]"
    # 分隔
    sep = f"[dim]├{'─'*55}┤[/dim]"
    # 底部输入提示
    input_line = f"[dim]│[/dim]  "
    return f"{header}\n{body}\n{sep}\n{input_line}"


def input_box_footer() -> str:
    """输入框底边."""
    return f"[dim]└{'─'*55}┘[/dim]"
