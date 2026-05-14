"""XL TUI 事件处理器 — 接入 agent.run() 事件流."""

import time
from rich.console import Console
from . import tui

console = Console(highlight=False)


async def run_with_tui(agent, user_input: str):
    """运行 agent.run(user_input) 并用 TUI 渲染."""

    # 打印用户消息
    console.print()
    console.print(tui.user_msg(user_input))
    console.print()

    text_buffer = ""
    tool_count = 0
    tool_timings = {}
    think_start = 0.0
    last_tool_name = ""

    async for event in agent.run(user_input, stream=True):
        etype = event["type"]

        # ── 思考开始 ──
        if etype == "exploring_start":
            think_start = time.time()
            console.print(tui.thinking(0), end="\r")
            console.print()

        # ── 思考结束 ──
        elif etype == "exploring_done":
            elapsed = time.time() - think_start
            console.print(f"\033[K{tui.thinking(elapsed)}")

        # ── 思考内容 → 隐藏 ──
        elif etype == "reasoning":
            pass  # 不显示

        # ── 文字增量 → 收集 ──
        elif etype == "text_delta":
            text_buffer += event["content"]

        # ── 工具调用 → 打印一行 ──
        elif etype == "tool_call":
            tool_count += 1
            name = event.get("name", "?")
            args = event.get("arguments") or {}
            preview = "  ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:2])
            console.print(tui.tool_call(name, preview))
            last_tool_name = name

        elif etype == "tool_exec":
            name = event.get("data", {}).get("function", {}).get("name", "")
            if name and name != last_tool_name:
                # 如果 tool_call 没触发，这里兜底
                console.print(tui.tool_call(name, ""))
                last_tool_name = name

        elif etype == "tool_result":
            pass  # 结果隐藏

        # ── 完成 ──
        elif etype == "completed":
            elapsed = time.time() - think_start if think_start else 0
            tokens = sum(len(str(m.get("content", ""))) // 4 for m in agent.messages[-10:])
            ctx_pct = agent.compressor.estimate_tokens(agent.messages) * 100 // 1_000_000 if agent.compressor else 0
            text = text_buffer.strip()
            if text:
                console.print()
                console.print(tui.ai_msg(text, elapsed, tool_count, tokens, ctx_pct))
                console.print()
            # 清理
            text_buffer = ""
            tool_count = 0

        # ── 错误 ──
        elif etype == "error":
            console.print(tui.error_msg(event["content"]))

        elif etype == "timeout":
            mode = event.get("mode", "?")
            limit = event.get("limit", 0)
            ls = f"{limit}s" if limit < 120 else f"{limit // 60}min"
            console.print(f"[yellow]⏱ 超时: {mode} ({ls})[/yellow]")

        elif etype == "ctx_warning":
            console.print(f"[dim]⚡ 上下文 {event.get('pct',90)}%[/dim]")

        elif etype == "compacted":
            console.print(f"[dim]📦 压缩 {event.get('message_count','?')} 条消息[/dim]")

        elif etype == "aborted":
            console.print("[red]--- 已中断 ---[/red]")

        elif etype == "permission_request":
            cat = event.get("category", "?")
            name = event.get("tool_name", "?")
            msg = event.get("message", "")
            if cat == "dangerous":
                console.print(f"[bold red]⚠ 危险: {name}[/bold red]")
                console.print(f"[dim]{str(event.get('tool_args',{}))[:200]}[/dim]")
                console.print(f"[bold red]执行? [y/N][/bold red] ", end="")
            else:
                console.print(f"[bold yellow]✎ 写入: {name}[/bold yellow]")
                console.print(f"[dim]{msg}[/dim]")
                console.print(f"[bold yellow]允许? [Y/n][/bold yellow] ", end="")
            try:
                import asyncio
                ans = await asyncio.get_event_loop().run_in_executor(None, input)
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans.strip().lower() in ("y", "yes") or (cat != "dangerous" and ans.strip() in ("", "y", "yes")):
                agent.approve_permission()
                console.print("  [green]✓ 已允许[/green]")
            else:
                agent.deny_permission()
                console.print("  [red]✗ 已拒绝[/red]")
