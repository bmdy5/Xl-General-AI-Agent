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
import os
import readline  # 启用退格键和行编辑
import select
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

from agent.llm import LLMClient
from agent.core import Agent
from agent.memory.manager import MemoryManager
from agent.session.handler import SessionHandler
from agent.tools.bash_tool import BashTool
from agent.tools.file_tools import ReadFileTool, WriteFileTool
from agent.tools.image2_tool import Image2GenerateTool
from agent.tools.memory_tool import MemoryTool
from agent.tools.read_image_tool import ReadImageTool
from agent.tools.registry import registry
from agent.tools.spawn_agent_tool import SpawnAgentTool
from agent.tools.web_fetch_tool import WebFetchTool
from agent.tools.web_search_tool import WebSearchTool


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
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, fl)
    except (ImportError, AttributeError, OSError):
        pass
    return first


def _print_highlighted(text: str):
    """流式输出 agent 回复，暖色区分."""
    print(f"\033[38;5;252m{text}\033[0m", end="", flush=True)

def _flush_highlighted():
    pass  # 当前无需 flush，保留接口


def build_agent(session_id: str = "default") -> Agent:
    """组装 Agent：LLM + Tools + Memory + Session."""
    model = os.getenv("MYAGENT_MODEL", "openai/gpt-4o")
    api_key = os.getenv("MYAGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    api_base = os.getenv("MYAGENT_API_BASE") or os.getenv("OPENAI_API_BASE")

    llm = LLMClient(model=model, api_key=api_key, api_base=api_base)

    # 注册工具
    if not registry.list_names():
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(BashTool(work_dir=os.getcwd()))
        registry.register(WebSearchTool())
        registry.register(WebFetchTool())
        registry.register(ReadImageTool())
        registry.register(Image2GenerateTool())
        registry.register(SpawnAgentTool())
        registry.register(MemoryTool())

    memory = MemoryManager()
    session = SessionHandler(session_id)

    return Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        session=session,
        max_turns=int(os.getenv("MYAGENT_MAX_TURNS", "200")),
    )


async def run_interactive(plan_mode: bool = False):
    """交互模式：持续对话直到用户输入 /exit."""
    agent = build_agent()

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
  ║  /exit   /clear   /memory   /tools                  ║
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

        try:
            user_input = _read_multiline()
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

        # 历史在后台加载，不阻塞首条消息
        if not history_loaded and load_task.done():
            history_loaded = True
        print()
        _req_start = asyncio.get_event_loop().time()
        _spinning = False
        _spin_task = None
        _tool_spinning = False
        _tool_spin_task = None

        # SIGINT → abort agent (not exit)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, lambda: agent.abort())
        except (NotImplementedError, RuntimeError):
            pass  # Windows 不支持

        def _start_spin(label):
            nonlocal _spinning, _spin_task
            _spinning = True
            _spin_start = asyncio.get_event_loop().time()
            async def spin():
                frames = "⠋⠙⠙⠘⠜⠴⠦⠧⠇⠏"
                i = 0
                while _spinning:
                    e = asyncio.get_event_loop().time() - _spin_start
                    print(f"\r\033[K\033[90m{frames[i%len(frames)]} {label} ({e:.1f}s)\033[0m", end="", flush=True)
                    i += 1; await asyncio.sleep(0.15)
            _spin_task = asyncio.create_task(spin())

        def _stop_spin():
            nonlocal _spinning
            _spinning = False
            try: _spin_task.cancel()
            except Exception: pass

        def _start_tool_spin(name):
            nonlocal _tool_spinning, _tool_spin_task
            _tool_spinning = True
            _tool_start = asyncio.get_event_loop().time()
            async def tool_spin():
                frames = "⠋⠙⠙⠘⠜⠴⠦⠧⠇⠏"
                i = 0
                while _tool_spinning:
                    e = asyncio.get_event_loop().time() - _tool_start
                    print(f"\r\033[K\033[90m{frames[i%len(frames)]} {name} ({e:.1f}s)\033[0m", end="", flush=True)
                    i += 1; await asyncio.sleep(0.15)
            _tool_spin_task = asyncio.create_task(tool_spin())

        def _stop_tool_spin():
            nonlocal _tool_spinning
            _tool_spinning = False
            try: _tool_spin_task.cancel()
            except Exception: pass

        try:
            async for event in agent.run(user_input, stream=True, plan_mode=plan_mode):
                etype = event["type"]

                if etype == "compacted":
                    print(f"\n  [上下文已压缩: {event.get('message_count', '?')} 条消息]")

                elif etype == "exploring_start":
                    _start_spin("思考中")

                elif etype == "exploring_done":
                    _stop_spin()
                    print(f"\r\033[K", end="")

                elif etype == "completed":
                    _stop_spin()
                    _stop_tool_spin()
                    _elapsed = asyncio.get_event_loop().time() - _req_start
                    _tokens = sum(len(str(m.get("content","")))//4 for m in agent.messages[-10:])
                    _ctx_pct = agent.compressor.estimate_tokens(agent.messages) * 100 // 1_000_000
                    print(f"\033[90m({_elapsed:.1f}s · ~{_tokens}t · {_ctx_pct}% ctx)\033[0m")
                    _req_start = asyncio.get_event_loop().time()

                elif etype == "reasoning":
                    print(f"\033[90m{event['content']}\033[0m", end="", flush=True)

                elif etype == "text_delta":
                    _print_highlighted(event["content"])

                elif etype in ("tool_call", "tool_exec"):
                    name = event.get("name") or event.get("data", {}).get("function", {}).get("name", "?")
                    _stop_spin()
                    _start_tool_spin(name)

                elif etype == "tool_result":
                    _stop_tool_spin()
                    short = str(event.get("result", ""))[:200].replace("\n", " ")
                    icon = "\033[32m✓\033[0m" if "error" not in str(event.get("result", "")).lower()[:50] else "\033[31m✗\033[0m"
                    print(f"\r\033[K  {icon} {short}")

                elif etype == "plan_ready":
                    _stop_spin()
                    plan = event.get("content", "")[:500]
                    tools = event.get("tools", [])
                    print(f"\r\033[K\033[1;36m--- 计划 ---\033[0m\n{plan}")
                    print(f"\033[33m工具: {', '.join(tools)}\033[0m")
                    print("\033[1;36m执行? [Y/n]\033[0m ", end="", flush=True)
                    try:
                        ans = await asyncio.get_event_loop().run_in_executor(None, input)
                    except (EOFError, KeyboardInterrupt):
                        ans = "n"
                    if ans.strip().lower() in ("", "y", "yes"):
                        agent.approve_plan()
                        print("  → 执行中...")
                    else:
                        agent.abort()
                        print("  → 已取消")

                elif etype == "ctx_warning":
                    print(f"\n  ⚠️ 上下文已用 {event.get('pct',90)}%，建议 /clear 或等压缩")

                elif etype == "nudge":
                    print(f"\n  [💡 Periodic Nudge: 检查是否有值得保存的记忆]")

                elif etype == "max_turns":
                    print("\n  [max turns reached]")

                elif etype == "error":
                    _stop_spin()
                    _stop_tool_spin()
                    print(f"\n  \033[31m[ERROR]\033[0m {event['content']}")

                elif etype == "aborted":
                    _stop_spin()
                    _stop_tool_spin()
                    print("\n  [aborted]")

        except (asyncio.CancelledError, KeyboardInterrupt):
            agent.abort()
            _stop_spin()
            _stop_tool_spin()
            print("\n  [interrupted]")
        except Exception as e:
            _stop_spin()
            _stop_tool_spin()
            print(f"\n  \033[31m[ERROR]\033[0m {e}")

        print()


async def run_single(query: str):
    """单次模式：执行一次查询后退出."""
    agent = build_agent()

    async for event in agent.run(query):
        etype = event["type"]
        if etype == "compacted":
            print(f"\n[上下文压缩: {event.get('message_count', '?')} 条消息]")
        elif etype == "reasoning":
            print(f"\033[90m{event['content']}\033[0m", end="", flush=True)
        elif etype == "text_delta":
            print(event["content"], end="", flush=True)
        elif etype == "tool_call":
            print(f"\n[TOOL] {event.get('name', '?')}")
        elif etype == "tool_result":
            short = event["result"][:300].replace("\n", " ")
            print(f"  → {short}")
        elif etype == "error":
            print(f"\n[ERROR] {event['content']}")

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

    print(f"\n  👑 XL Agent — Dashboard 学习模式\n")
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

    print("\n  👑 XL Agent — 交互模式 (Dashboard: http://localhost:8765)\n")

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
    plan_mode = "--plan" in sys.argv
    argv = [a for a in sys.argv if a != "--plan"]

    if len(argv) > 1:
        if argv[1] == "--gateway":
            _run_safe(run_gateway())
        elif argv[1] == "--auto-learn":
            _run_safe(run_auto_learn())
        elif argv[1] == "--cleanup":
            from agent.cleanup import run_cleanup
            agent = build_agent()
            _run_safe(run_cleanup(agent))
        elif argv[1] == "--dashboard":
            _run_safe(run_dashboard())
        elif argv[1] == "--dashboard-learn":
            _run_safe(run_dashboard_learn())
        else:
            _run_safe(run_single(" ".join(argv[1:])))
    else:
        _run_safe(run_interactive(plan_mode=plan_mode))
