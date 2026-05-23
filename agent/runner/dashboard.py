import os
import sys
import asyncio
from ..core.bootstrap import build_agent
from ..ui.dashboard import DashboardServer
from ..learn.auto_learn import AutoLearner

async def run_dashboard_learn():
    """Dashboard + 自主学习."""
    agent = build_agent()
    dash = DashboardServer(port=8765)
    await dash.start()

    learn_model = os.environ.get("MYAGENT_LEARN_MODEL", "")
    learner = AutoLearner(agent, max_duration_minutes=5, learn_model=learn_model, dashboard=dash)

    await dash.send({"agent": "xl", "event": "auto_learn_start", "name": "XL"})
    result = await learner.run()

    await dash.send({
        "agent": "xl", 
        "event": "learn_done", 
        "name": "XL",
        "action": f"{result['articles_read']} articles, {result['skills_created']} skills"
    })
    print(f"\n  完成: {result['articles_read']}篇, {result['skills_created']}技能")
    await asyncio.sleep(5)

async def run_dashboard():
    """Dashboard 模式：启动 HTTP + SSE。有 stdin 则交互，无 stdin 则纯服务."""
    agent = build_agent()
    dash = DashboardServer(port=8765)
    await dash.start()

    if not sys.stdin.isatty():
        print("  📡 纯服务模式 (无交互)")
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    if agent.session:
        agent.messages = await agent.session.initialize()

    original_run = agent.run
    async def hooked_run(user_input):
        await dash.send({"agent": "xl", "event": "user_input", "name": "XL"})
        async for event in original_run(user_input):
            etype = event.get("type", "")
            if etype == "tool_call":
                await dash.send({
                    "agent": "xl", 
                    "event": etype, 
                    "name": event.get("name", ""), 
                    "action": str(event.get("args", {}))[:80]
                })
            elif etype == "tool_result":
                await dash.send({"agent": "xl", "event": etype, "name": event.get("name", "")})
            elif etype == "compacted":
                await dash.send({"agent": "xl", "event": "compacting"})
            elif etype == "nudge":
                await dash.send({"agent": "xl", "event": "periodic_nudge"})
            yield event
    agent.run = hooked_run  # type: ignore

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            from ..evolution import on_session_end
            try:
                await asyncio.wait_for(on_session_end(agent), timeout=8)
            except Exception:
                pass
            break
        if not user_input:
            continue
        if user_input == "/exit":
            from ..evolution import on_session_end
            try:
                await asyncio.wait_for(on_session_end(agent), timeout=8)
            except Exception:
                pass
            break
        if user_input == "/clear":
            agent.clear_history()
            continue
        if user_input == "/memory":
            for m in agent.memory.list_memories():
                print(f"  {m}")
            continue
        print()
        try:
            async for event in agent.run(user_input):
                etype = event.get("type", "")
                if etype == "compacted":
                    print(f"\n  [上下文压缩]")
                elif etype == "text_delta":
                    print(event["content"], end="", flush=True)
                elif etype == "tool_call":
                    print(f"\n  [TOOL] {event['name']}")
                elif etype == "tool_result":
                    print(f"  → {str(event['result'])[:200]}")
                elif etype == "nudge":
                    print(f"\n  [💡 Nudge]")
                elif etype == "aborted":
                    print("\n  [aborted]")
                elif etype == "error":
                    print(f"\n  [ERROR] {event['content']}")
        except Exception as e:
            print(f"\n  [ERROR] {e}")
        print()
