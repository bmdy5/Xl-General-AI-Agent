from ..bootstrap import build_agent
from ..tui_events import run_with_tui

async def run_single(query: str):
    """单次模式：执行一次查询后退出."""
    agent = build_agent()
    try:
        await run_with_tui(agent, query)
    except Exception as e:
        print(f"\n[ERROR] {e}")
    print()
