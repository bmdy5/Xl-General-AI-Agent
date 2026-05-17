#!/usr/bin/env python3
"""MyAgent — 通用 AI Agent CLI.

用法：
    python main.py                          # 交互模式
    python main.py "请帮我读一下 README.md"   # 单次模式

配置：
    复制 .env.example 为 .env，填入 API key。
    支持所有 LiteLLM 兼容的 provider（OpenAI、Anthropic、DeepSeek 等）。
"""

import asyncio
import json
import os
import readline  # 启用退格键和行编辑
import signal
import sys

from dotenv import load_dotenv

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
load_dotenv()

from agent.llm import LLMClient
from agent.core import Agent, AgentMode
from agent.memory.manager import MemoryManager
from agent.session.handler import SessionHandler
from agent.tools.bash_tool import BashTool
from agent.tools.edit_file_tool import EditFileTool
from agent.tools.file_tools import ReadFileTool, WriteFileTool
from agent.tools.swarm_tool import SwarmTool
from agent.tools.run_sequence_tool import RunSequenceTool
from agent.tools.manage_tool_tool import ManageToolTool
from agent.tools.image2_tool import Image2GenerateTool
from agent.tools.memory_tool import MemoryTool
from agent.tools.read_image_tool import ReadImageTool
from agent.tools.mcp_client_tool import MCPClientTool
from agent.tools.registry import registry
from agent.tools.spawn_agent_tool import SpawnAgentTool
from agent.tools.stitch_tool import StitchTool
from agent.tools.web_fetch_tool import WebFetchTool
from agent.tools.web_search_tool import WebSearchTool
from agent.tools.organize_notes_tool import OrganizeNotesTool


# ── 终端辅助 ───────────────────────────────────────────────

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
    # Unix: 用 fcntl 检测 stdin buffer 中是否有更多数据（粘贴）
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
            # 非阻塞模式下 codec decode 偶尔失败，忽略多行检测
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
    pass  # 当前无需 flush，保留接口


def build_agent(session_id: str = "default") -> Agent:
    """组装 Agent：LLM + Tools + Memory + Session."""
    # 主力模型（Mimo，用于视觉识别）
    model_vision = os.getenv("MYAGENT_MODEL", "openai/gpt-4o")
    api_key = os.getenv("MYAGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    api_base = os.getenv("MYAGENT_API_BASE") or os.getenv("OPENAI_API_BASE")

    # 日常/深度模型（DeepSeek）
    model_flash = os.getenv("MYAGENT_MODEL_FLASH", "deepseek/deepseek-chat")
    model_pro = os.getenv("MYAGENT_MODEL_PRO", "deepseek/deepseek-chat")

    max_tokens = int(os.getenv("MYAGENT_MAX_TOKENS", "16384"))
    llm = LLMClient(
        model=model_flash,            # 默认用 DeepSeek Flash（省钱）
        api_key=api_key,              # Mimo API key（仅 Mimo 模型会用）
        api_base=api_base,
        max_tokens=max_tokens,
        model_vision=model_vision,    # 视觉模型
        model_pro=model_pro,          # 深度推理模型
    )

    # 注册工具
    if not registry.list_names():
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(EditFileTool())
        registry.register(SwarmTool())
        registry.register(RunSequenceTool())
        registry.register(ManageToolTool())
        registry.register(BashTool(work_dir=os.getcwd()))
        registry.register(WebSearchTool())
        registry.register(WebFetchTool())
        registry.register(ReadImageTool())
        registry.register(Image2GenerateTool())
        registry.register(SpawnAgentTool())
        registry.register(StitchTool())
        registry.register(MCPClientTool())
        registry.register(MemoryTool())
        registry.register(OrganizeNotesTool())

    memory = MemoryManager()
    session = SessionHandler(session_id)

    return Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        session=session,
        max_turns=int(os.getenv("MYAGENT_MAX_TURNS", "200")),
    )


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
                # 合并：如果用户已开始聊天，历史在前，新消息在后
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

  亮哥，我是你的小弟 XL，有什么吩咐？
""")

    # 确保历史加载完成
    history_loaded = False
    while True:
        if not history_loaded and load_task.done():
            history_loaded = True
            if load_task.exception():
                print(f"\n  ⚠️ 历史加载失败: {load_task.exception()}")

        def _mode_label(m: AgentMode) -> str:
            return "Normal" if m == AgentMode.NORMAL else "Deep"

        # 输入区—简洁横线
        ctx_pct = agent.compressor.estimate_tokens(agent.messages) * 100 // 1_000_000 if agent.compressor else 0
        prompt_style = "Normal" if _mode == AgentMode.NORMAL else "Deep"
        ctx_bar = "█" * (ctx_pct // 5) + "░" * (20 - ctx_pct // 5)
        from agent.tui import console as tui_console
        tui_console.print(f"  [bright_blue]{prompt_style}[/bright_blue] [dim]ctx: {ctx_bar} {ctx_pct}%[/dim]")
        tui_console.print(f"  [dim]│[/dim] ", end="")
        try:
            user_input = _read_multiline("")
        except (EOFError, KeyboardInterrupt):
            print("\r\033[K🧠 整理记忆中...", end="", flush=True)
            try:
                from agent.evolution import on_session_end
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
                from agent.evolution import on_session_end
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
            bar = "█" * (ctx_pct // 5) + "░" * (20 - ctx_pct // 5)
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
            from agent.task_queue import TaskQueue
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
            from agent.task_queue import TaskQueue
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
            from agent.task_queue import TaskQueue
            tid = user_input[11:].strip()
            q = TaskQueue()
            if q.mark_done(tid):
                print(f"  ✅ 任务 {tid} 已标记完成")
            else:
                print(f"  ❌ 未找到任务 {tid}")
            continue

        if user_input == "/tasks clear":
            from agent.task_queue import TaskQueue
            TaskQueue().clear_done()
            print("  ✅ 已清理已完成任务")
            continue

        # 历史在后台加载，不阻塞首条消息
        if not history_loaded and load_task.done():
            history_loaded = True
        print()
        # SIGINT → abort agent (not exit)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, lambda: agent.abort())
        except (NotImplementedError, RuntimeError):
            pass

        # 使用 TUI 渲染
        from agent.tui_events import run_with_tui
        try:
            await run_with_tui(agent, user_input)
        except (asyncio.CancelledError, KeyboardInterrupt):
            agent.abort()
            print("\n  [interrupted]")
        except Exception as e:
            print(f"\n  [ERROR] {e}")

        print()


async def run_single(query: str):
    """单次模式：执行一次查询后退出."""
    agent = build_agent()

    from agent.tui_events import run_with_tui
    try:
        await run_with_tui(agent, query)
    except Exception as e:
        print(f"\n[ERROR] {e}")
    print()


async def run_dashboard_learn():
    """Dashboard + 自主学习."""
    from agent.dashboard import DashboardServer
    from agent.auto_learn import AutoLearner

    agent = build_agent()
    dash = DashboardServer(port=8765)
    await dash.start()

    learn_model = os.getenv("MYAGENT_LEARN_MODEL", "")
    learner = AutoLearner(agent, max_duration_minutes=5, learn_model=learn_model, dashboard=dash)

    await dash.send({"agent": "xl", "event": "auto_learn_start", "name": "XL"})
    result = await learner.run()

    await dash.send({"agent": "xl", "event": "learn_done", "name": "XL",
                     "action": f"{result['articles_read']} articles, {result['skills_created']} skills"})
    print(f"\n  完成: {result['articles_read']}篇, {result['skills_created']}技能")
    await asyncio.sleep(5)  # 让 dashboard 看到最终状态


async def run_auto_learn():
    """自主学习模式：agent 自动浏览网页、学习知识、创建技能."""
    from agent.auto_learn import AutoLearner

    agent = build_agent()
    learn_model = os.getenv("MYAGENT_LEARN_MODEL", "")
    dash = getattr(agent, '_dash', None)
    learner = AutoLearner(agent, max_duration_minutes=5, learn_model=learn_model, dashboard=dash)

    print("\n  MyAgent — 自主学习模式")
    print(f"  Model: {agent.llm.model}")
    print(f"  Duration: 5 minutes")
    print(f"  Knowledge base: {learner.kb}")
    print()

    try:
        result = await learner.run()
    except KeyboardInterrupt:
        result = {"articles_read": 0, "skills_created": 0, "topics": [], "summary": "用户中断", "errors": ["KeyboardInterrupt"]}

    print(f"\n  ===== 学习完成 =====")
    print(f"  阅读文章: {result['articles_read']} 篇")
    print(f"  创建技能: {result['skills_created']} 个")
    if result["errors"]:
        print(f"  错误: {len(result['errors'])} 个")
    print(f"\n{result['summary']}")


async def run_gateway():
    """QQ Gateway 模式：连接 NapCat 收发 QQ 消息."""
    from agent.gateway import QQGateway

    print("\n  MyAgent — QQ Gateway 模式")
    print(f"  WebSocket: {os.getenv('NAPCAT_WS_URL', 'ws://localhost:3001')}")
    print(f"  HTTP API:  {os.getenv('NAPCAT_HTTP_URL', 'http://localhost:3000')}")
    print()

    gw = QQGateway(build_agent)
    try:
        await gw.run()
    except KeyboardInterrupt:
        print("\n  Gateway stopped.")


def _cleanup_terminal():
    """恢复终端状态（抄 openclaw）."""
    import sys
    try:
        sys.stdout.write("\x1b[0m\x1b[?25h")  # 重置颜色 + 显示光标
        sys.stdout.flush()
    except Exception:
        pass

import atexit
atexit.register(_cleanup_terminal)

async def run_dashboard():
    """Dashboard 模式：启动 HTTP + SSE。有 stdin 则交互，无 stdin 则纯服务."""
    import sys
    from agent.dashboard import DashboardServer

    agent = build_agent()
    dash = DashboardServer(port=8765)
    await dash.start()

    # 无 stdin (nohup/后台) → 纯服务模式
    if not sys.stdin.isatty():
        print("  📡 纯服务模式 (无交互)")
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    # 恢复历史
    if agent.session:
        agent.messages = await agent.session.initialize()

    # Hook: agent 事件同时推送到 dashboard
    original_run = agent.run
    async def hooked_run(user_input):
        await dash.send({"agent": "xl", "event": "user_input", "name": "XL"})
        async for event in original_run(user_input):
            etype = event.get("type", "")
            if etype == "tool_call":
                await dash.send({"agent": "xl", "event": etype, "name": event.get("name", ""), "action": str(event.get("args", {}))[:80]})
            elif etype == "tool_result":
                await dash.send({"agent": "xl", "event": etype, "name": event.get("name", "")})
            elif etype == "compacted":
                await dash.send({"agent": "xl", "event": "compacting"})
            elif etype == "nudge":
                await dash.send({"agent": "xl", "event": "periodic_nudge"})
            yield event
    agent.run = hooked_run  # type: ignore

    # Startup banner is printed above via the banner list

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            from agent.evolution import on_session_end
            try: await asyncio.wait_for(on_session_end(agent), timeout=8)
            except: pass
            break
        if not user_input: continue
        if user_input == "/exit":
            from agent.evolution import on_session_end
            try: await asyncio.wait_for(on_session_end(agent), timeout=8)
            except: pass
            break
        if user_input == "/clear": agent.clear_history(); continue
        if user_input == "/memory":
            for m in agent.memory.list_memories(): print(f"  {m}")
            continue
        print()
        try:
            async for event in agent.run(user_input):
                etype = event.get("type", "")
                if etype == "compacted": print(f"\n  [上下文压缩]")
                elif etype == "text_delta": print(event["content"], end="", flush=True)
                elif etype == "tool_call": print(f"\n  [TOOL] {event['name']}")
                elif etype == "tool_result": print(f"  → {str(event['result'])[:200]}")
                elif etype == "nudge": print(f"\n  [💡 Nudge]")
                elif etype == "aborted": print("\n  [aborted]")
                elif etype == "error": print(f"\n  [ERROR] {event['content']}")
        except Exception as e: print(f"\n  [ERROR] {e}")
        print()


def _run_safe(coro):
    try: asyncio.run(coro)
    except (KeyboardInterrupt, asyncio.CancelledError): pass

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--gateway":
            _run_safe(run_gateway())
        elif sys.argv[1] == "--auto-learn":
            _run_safe(run_auto_learn())
        elif sys.argv[1] == "--cleanup":
            from agent.cleanup import run_cleanup
            agent = build_agent()
            _run_safe(run_cleanup(agent))
        elif sys.argv[1] == "--dashboard":
            _run_safe(run_dashboard())
        elif sys.argv[1] == "--dashboard-learn":
            _run_safe(run_dashboard_learn())
        elif sys.argv[1] in ("--duoagent", "-d"):
            from agent.duoagent.server import start
            start()
        else:
            _run_safe(run_single(" ".join(sys.argv[1:])))
    else:
        _run_safe(run_interactive())
