"""XL TUI — 干净、简约的对话界面.

设计原则:
- 别处看不到的风格（不套模板）
- 用横线分隔代替粗边框
- 统一用紫色调（非蓝绿标准色）
- 留白多，不拥挤
"""

import time
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.table import Table
from rich.rule import Rule
from rich.align import Align

console = Console(highlight=False)

# ██████ 调色板 ██████
C_USER = "bright_blue"            # 用户
C_AI = "bright_magenta"          # AI 回复
C_TOOL = "yellow"                # 工具
C_DIM = "grey58"                 # 辅助文字
C_HEAD = "blue"                  # 头部
C_BORDER = "grey58"              # 边框
C_ACCENT = "bright_magenta"      # 强调色


def h(title: str = "XL Agent", subtitle: str = "") -> str:
    """顶栏 — 极简."""
    out = f"[bold]{title}[/bold]"
    if subtitle:
        out += f"  [dim]{subtitle}[/dim]"
    return out


def hr(char: str = "─") -> str:
    """分隔线."""
    return f"[dim]{' ' + char*50}[/dim]"


def user_msg(text: str) -> str:
    """用户消息."""
    lines = text.split("\n")
    out = f"  [bold {C_USER}]┃[/] {lines[0]}"
    for l in lines[1:]:
        out += f"\n  [bold {C_USER}]┃[/] {l}"
    return out


def thinking(duration: float) -> str:
    """思考中（隐藏内容，仅显示耗时 + 旋转动画）."""
    return f"  [italic dim]✦ 思考 {duration:.1f}s  ✦[/italic dim]"


def tool_call(name: str, args_preview: str) -> str:
    """工具调用 — 一行."""
    return f"  [{C_TOOL}]▸ {name}[/{C_TOOL}]  [dim]{args_preview}[/dim]"


def ai_msg(text: str, elapsed: float, tools: int, tokens: int, ctx: int) -> str:
    """AI 回复."""
    lines = text.split("\n")
    out = f"  [bold {C_AI}]┃[/] {lines[0]}"
    for l in lines[1:]:
        out += f"\n  [bold {C_AI}]┃[/] {l}"
    # 底部元数据
    meta = f"[dim]  ⏱ {elapsed:.1f}s · ~{tokens}t · {ctx}% ctx · {tools} tools[/dim]"
    out += "\n" + meta
    return out


def error_msg(msg: str) -> str:
    return f"  [bold red]✗ {msg}[/bold red]"


def cmd_bar() -> str:
    """底部命令."""
    return f"[dim]/exit | /clear | /memory | /tools | /mode[/dim]"


def compact_output(events: list[dict]) -> str:
    """将事件列表渲染为紧凑输出（用于单次模式）."""
    buffer = []
    text_parts = []
    tool_count = 0
    start = time.time()

    for ev in events:
        t = ev["type"]
        if t == "reasoning":
            continue  # 完全隐藏
        elif t == "text_delta":
            text_parts.append(ev["content"])
        elif t in ("tool_call",):
            tool_count += 1
            name = ev.get("name", "?")
            args = ev.get("arguments", {})
            preview = "  ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:2])
            buffer.append(f"  [{C_TOOL}]▸ {name}[/{C_TOOL}]  [dim]{preview}[/dim]")
        elif t == "error":
            buffer.append(f"  [bold red]✗ {ev['content']}[/bold red]")

    elapsed = time.time() - start
    text = "".join(text_parts).strip()
    if text:
        buffer.append(f"\n  {text}")
    if elapsed > 0.1:
        buffer.append(f"\n  [dim]⏱ {elapsed:.1f}s · {tool_count} tools[/dim]")
    return "\n".join(buffer)
