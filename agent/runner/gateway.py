import os
from ..core.bootstrap import build_agent
from ..core.gateway import QQGateway

async def run_gateway():
    """QQ Gateway 模式：连接 NapCat 收发 QQ 消息."""
    print("\n  MyAgent — QQ Gateway 模式")
    print(f"  WebSocket: {os.environ.get('NAPCAT_WS_URL', 'ws://localhost:3001')}")
    print(f"  HTTP API:  {os.environ.get('NAPCAT_HTTP_URL', 'http://localhost:3020')}")
    print()

    gw = QQGateway(build_agent)
    try:
        await gw.run()
    except KeyboardInterrupt:
        print("\n  Gateway stopped.")

async def run_douyin_gateway():
    """Douyin 独立网关已移除，保留入口用于兼容旧脚本."""
    print("\n  Douyin Gateway 已移除，请使用新版视觉引擎 (browser_agent/desktop_agent)")
    import asyncio
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n  Douyin Gateway stopped.")
