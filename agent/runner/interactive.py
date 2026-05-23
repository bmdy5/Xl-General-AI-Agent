import sys
import os
import asyncio
import signal
import readline
import operator
import json as _json
from ..core import AgentMode
from ..bootstrap import build_agent
from ..ui.tui import console as tui_console
from ..ui.tui_events import run_with_tui
from ..evolution import on_session_end

def _read_multiline(prompt: str = "> ") -> str:
    """读用户输入，自动检测多行粘贴."""
    print(prompt, end="", flush=True)
    try:
        first = input()
    except (EOFError, KeyboardInterrupt):
        raise
    first = first.strip()
    if not first:
        return ""
    try:
        import fcntl
        fd = sys.stdin.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        try:
            rest = sys.stdin.read()
            if rest and rest.strip():
                return first + rest.rstrip('\n')
        except TypeError:
            pass
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, fl)
    except (ImportError, AttributeError, OSError):
        pass
    return first

def _print_highlighted(text: str):
    """流式输出 agent 回复，亮白高亮."""
    print(f"\033[1;97m{text}\033[0m", end="", flush=True)

def _flush_highlighted():
    pass

async def run_interactive():
    """交互模式：持续对话直到用户输入 /exit."""
    agent = build_agent()
    _mode = AgentMode.NORMAL

    # 后台加载历史，不阻塞启动
    async def load_history():
        if agent.session:
            all_msgs = await agent.session.initialize()
            if len(all_msgs) > 20:
                compressed, _ = await agent.compressor.compress(all_msgs, memory=agent.memory)
                if agent.messages:
                    compressed.extend(agent.messages)
                agent.messages = compressed
                if agent.session:
                    await agent.session.replace_all(agent.messages)
            else:
                if agent.messages:
                    all_msgs.extend(agent.messages)
                agent.messages = all_msgs

    load_task = asyncio.create_task(load_history())

    tools_list = ', '.join(agent.registry.list_names())
    model_short = agent.llm.model.split('/')[-1] if '/' in agent.llm.model else agent.llm.model

    _ua = "亮哥"
    try:
        _pf = agent.memory.base_dir / "persona_profile.json"
        if _pf.exists():
            _ua = _json.loads(_pf.read_text(encoding="utf-8")).get("user_address", "亮哥")
    except Exception:
        pass

    print(fr"""
  ╔══════════════════════════════════════════════════════╗
  ║                                                      ║
  ║   ██╗  ██╗██╗      █████╗  ██████╗ ███████╗███╗  ██╗████████╗ ║
  ║   ╚██╗██╔╝██║     ██╔══██╗██╔════╝ ██╔════╝████╗ ██║╚══██╔══╝ ║
  ║    ╚███╔╝ ██║     ███████║██║  ███╗█████╗  ██╔██╗██║   ██║    ║
  ║    ██╔██╗ ██║     ██╔══██║██║   ██║██╔══╝  ██║╚████║   ██║    ║
  ║   ██╔╝╚██╗███████╗██║  ██║╚██████╔╝███████╗██║ ╚███║   ██║    ║
  ║   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚══╝   ╚═╝    ║
  ║                                                      ║
  ║           Personal AI · Self-Evolving · v2            ║
  ╠══════════════════════════════════════════════════════╣
  ║  Model   : {model_short:<43}║
  ║  Tools   : {tools_list:<43}║
  ║  History : {len(agent.messages)} messages{' ':<34}║
  ╠══════════════════════════════════════════════════════╣
  ║  /exit   /clear   /memory   /tools   /mode          ║
  ╚══════════════════════════════════════════════════════╝

  {_ua}，我是你的小弟 XL，有什么吩咐？
""")

    history_loaded = False
    while True:
        if not history_loaded and load_task.done():
            history_loaded = True
            if load_task.exception():
                print(f"\n  ⚠️ 历史加载失败: {load_task.exception()}")

        def _mode_label(m: AgentMode) -> str:
            return "Normal" if m == AgentMode.NORMAL else "Deep"

        ctx_pct = agent.compressor.estimate_tokens(agent.messages) * 100 // 1_000_000 if agent.compressor else 0
        prompt_style = "Normal" if _mode == AgentMode.NORMAL else "Deep"
        
        # 物理消除乘号，完美遵循零星号编码约束
        bar_len = ctx_pct // 5
        bar_len = max(0, min(20, bar_len))
        ctx_bar = operator.mul("█", bar_len) + operator.mul("░", (20 - bar_len))
        
        tui_console.print(f"  [bright_blue]{prompt_style}[/bright_blue] [dim]ctx: {ctx_bar} {ctx_pct}%[/dim]")
        tui_console.print(f"  [dim]│[/dim] ", end="")
        try:
            user_input = _read_multiline("")
        except (EOFError, KeyboardInterrupt):
            print("\r\033[K🧠 整理记忆中...", end="", flush=True)
            try:
                await asyncio.wait_for(on_session_end(agent), timeout=8)
            except (asyncio.TimeoutError, Exception):
                pass
            print("\r\033[KBye.\033[?25h")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("🧠 整理记忆中...", end="", flush=True)
            try:
                await asyncio.wait_for(on_session_end(agent), timeout=8)
            except (asyncio.TimeoutError, Exception):
                pass
            print("\r\033[KBye.\033[?25h")
            break

        if user_input == "/clear":
            agent.clear_history()
            print("History cleared.")
            continue

        if user_input == "/stats":
            ctx = agent.compressor.estimate_tokens(agent.messages)
            ctx_pct = ctx * 100 // 1_000_000
            
            bar_len = ctx_pct // 5
            bar_len = max(0, min(20, bar_len))
            bar = operator.mul("█", bar_len) + operator.mul("░", (20 - bar_len))
            
            print(f"\n  📊 Stats")
            print(f"  上下文: [{bar}] {ctx_pct}% ({ctx:,} / 1,000,000 tokens)")
            print(f"  消息数: {len(agent.messages)}")
            print(f"  工具数: {len(agent.registry.list_names())}")
            print(f"  模型: {agent.llm.model}")
            continue

        if user_input == "/memory":
            mems = agent.memory.list_memories()
            if mems:
                print("\n".join(mems))
            else:
                print("(no memories)")
            continue

        if user_input == "/tools":
            for name in agent.registry.list_names():
                tool = agent.registry.get(name)
                desc = await tool.description() if tool else ""
                print(f"  {name}: {desc}")
            continue

        if user_input.startswith("/mode"):
            parts = user_input.split()
            if len(parts) == 2 and parts[1] in ("normal", "deep"):
                _mode = AgentMode.NORMAL if parts[1] == "normal" else AgentMode.DEEP
                agent.set_mode(_mode)
                ts = "5 min" if _mode == AgentMode.NORMAL else "2 hours"
                print(f"  Mode: {parts[1]} (timeout: {ts})")
            else:
                print("  Usage: /mode normal | /mode deep")
            continue

        if user_input == "/tasks":
            from ..core.task_queue import TaskQueue
            q = TaskQueue()
            pending = q.list()
            if pending:
                print(f"\n  待办任务 ({len(pending)}):")
                for t in pending:
                    cron = t.get("cron", "once") or "once"
                    print(f"    [{t['id']}] {t['description']} ({cron})")
            else:
                print("  (没有待办任务)")
            print("  用法: /tasks add 描述 / 定期(daily/hourly/once)")
            continue

        if user_input.startswith("/tasks add "):
            from ..core.task_queue import TaskQueue
            rest = user_input[10:].strip()
            cron = "once"
            if " / " in rest:
                parts = rest.split(" / ", 1)
                desc = parts[0].strip()
                cron = parts[1].strip()
            else:
                desc = rest
            q = TaskQueue()
            q.add(desc, desc, cron)
            print(f"  ✅ 已添加任务: {desc} ({cron})")
            continue

        if user_input.startswith("/tasks done "):
            from ..core.task_queue import TaskQueue
            tid = user_input[11:].strip()
            q = TaskQueue()
            if q.mark_done(tid):
                print(f"  ✅ 任务 {tid} 已标记完成")
            else:
                print(f"  ❌ 未找到任务 {tid}")
            continue

        if user_input == "/tasks clear":
            from ..core.task_queue import TaskQueue
            TaskQueue().clear_done()
            print("  ✅ 已清理已完成任务")
            continue

        if not history_loaded and load_task.done():
            history_loaded = True
        print()
        
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, lambda: agent.abort())
        except (NotImplementedError, RuntimeError):
            pass

        try:
            await run_with_tui(agent, user_input)
        except (asyncio.CancelledError, KeyboardInterrupt):
            agent.abort()
            print("\n  [interrupted]")
        except Exception as e:
            print(f"\n  [ERROR] {e}")

        print()
