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
    """Douyin Standalone Gateway 模式：独立拉起抖音私信网关."""
    print("\n  MyAgent — Douyin Standalone Gateway 模式 (已物理分进程隔离)")
    print()

    gw = QQGateway(build_agent)
    try:
        await gw.run(only_douyin=True)
    except KeyboardInterrupt:
        print("\n  Douyin Standalone Gateway stopped.")
