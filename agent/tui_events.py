"""XL TUI 事件处理器 — Live 思考计时 + 实时工具展示."""

import time
import asyncio
from rich.console import Console
from . import tui

console = Console(highlight=False)


class ThinkingTimer:
    """实时思考计时器，在终端更新显示."""

    def __init__(self):
        self.start = 0.0
        self.task = None
        self._running = False

    async def _tick(self):
        """每秒更新计时."""
        while self._running:
            elapsed = time.time() - self.start
            line = f"\r  \033[36m⟳ 思考中... {elapsed:.1f}s\033[0m"
            console.file.write(line)
            console.file.flush()
            await asyncio.sleep(0.2)
        # 清除计时行
        console.file.write("\r\033[K")
        console.file.flush()

    def start_timer(self):
        self.start = time.time()
        self._running = True
        self.task = asyncio.create_task(self._tick())

    async def stop_timer(self):
        self._running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        # 清除
        console.file.write("\r\033[K")
        console.file.flush()


async def run_with_tui(agent, user_input: str):
    """运行 agent.run(user_input) 并用 TUI 渲染."""

    think = ThinkingTimer()
    text_buffer = ""
    tool_count = 0
    reason_preview = ""

    # 用户消息
    console.print()
    console.print(tui.user_msg(user_input))
    console.print()

    async for event in agent.run(user_input, stream=True):
        etype = event["type"]

        # ── 思考开始 → 启动实时计时 ──
        if etype == "exploring_start":
            think.start_timer()

        # ── 思考结束 → 停止计时 ──
        elif etype == "exploring_done":
            await think.stop_timer()

        # ── 推理内容 → 显示简短摘要（流式飞过）──
        elif etype == "reasoning":
            reason_preview += event["content"]
            # 只显示最近的 60 个字符
            short = reason_preview.strip()[-60:].replace("\n", " ")
            if short:
                msg = f"\r\033[K  \033[90m{short}\033[0m"
                console.file.write(msg)
                console.file.flush()

        # ── 文字 → 收集 ──
        elif etype == "text_delta":
            text_buffer += event["content"]

        # ── 工具调用 → 暂停计时 → 打印 → 重启计时 ──
        elif etype == "tool_call":
            tool_count += 1
            await think.stop_timer()
            name = event.get("name", "?")
            args = event.get("args") or event.get("arguments") or {}
            preview = "  ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:2]) if args else ""
            console.print(tui.tool_call(name, preview))
            think.start_timer()

        elif etype == "tool_exec":
            pass

        elif etype == "tool_result":
            pass  # 结果隐藏

        # ── 完成 → 显示回复 ──
        elif etype == "completed":
            await think.stop_timer()
            elapsed = time.time() - think.start if think.start else 0
            tokens = sum(len(str(m.get("content", ""))) // 4 for m in agent.messages[-10:])
            ctx_pct = agent.compressor.estimate_tokens(agent.messages) * 100 // 1_000_000 if agent.compressor else 0
            text = text_buffer.strip()
            if text:
                console.print()
                console.print(tui.ai_msg(text, elapsed, tool_count, tokens, ctx_pct))
                console.print()
            text_buffer = ""
            tool_count = 0
            reason_preview = ""

        # ── 错误 ──
        elif etype == "error":
            await think.stop_timer()
            console.print(tui.error_msg(event["content"]))

        elif etype == "timeout":
            await think.stop_timer()
            mode = event.get("mode", "?")
            limit = event.get("limit", 0)
            ls = f"{limit}s" if limit < 120 else f"{limit // 60}min"
            console.print(f"[yellow]⏱ 超时: {mode} ({ls})[/yellow]")

        elif etype == "ctx_warning":
            console.print(f"[dim]⚡ 上下文 {event.get('pct',90)}%[/dim]")

        elif etype == "compacted":
            console.print(f"[dim]📦 压缩 {event.get('message_count','?')} 条消息[/dim]")

        elif etype == "aborted":
            await think.stop_timer()
            console.print("[red]--- 已中断 ---[/red]")

        elif etype == "permission_request":
            await think.stop_timer()
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
                ans = await asyncio.get_event_loop().run_in_executor(None, input)
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans.strip().lower() in ("y", "yes") or (cat != "dangerous" and ans.strip() in ("", "y", "yes")):
                agent.approve_permission()
                console.print("  [green]✓ 已允许[/green]")
            else:
                agent.deny_permission()
                console.print("  [red]✗ 已拒绝[/red]")
            think.start_timer()
