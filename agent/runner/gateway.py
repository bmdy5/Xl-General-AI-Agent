import os
from ..bootstrap import build_agent
from ..gateway import QQGateway

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
