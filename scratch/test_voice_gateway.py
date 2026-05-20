import asyncio
import sys
import os

# 将项目根目录加入到 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import build_agent
from agent.gateway import QQGateway

async def test():
    print("💡 [测试启动] 正在初始化 QQGateway...")
    gw = QQGateway(build_agent)
    
    # 模拟 aiohttp.ClientSession，使 _send 能通过 HTTP 接口真实发包
    import aiohttp
    session = aiohttp.ClientSession()
    gw._http = session
    
    event = {
        "message_type": "private",
        "user_id": 1705919142,
        "raw_message": "小萤语音测试：喜 太好了，今天又能跟亮哥一起聊天啦！",
        "post_type": "message"
    }
    
    print("🎙️ [测试事件] 正在注入事件模拟: '小萤语音测试：喜 太好了，今天又能跟亮哥一起聊天啦！'")
    await gw._handle(event)
    
    # 等待异步发送任务与推理运行完毕
    await asyncio.sleep(6)
    await session.close()
    print("✅ [测试完成] 注入消息流程执行完毕，会话已关闭。")

if __name__ == "__main__":
    asyncio.run(test())
