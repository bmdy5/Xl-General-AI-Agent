import asyncio
import os
import sys
import aiohttp
import logging
import traceback

# 初始化 logging 到标准输出
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from agent.gateway import QQGateway

class MockAgentFactory:
    pass

async def send_stage2_test():
    print("=== 🚀 开始让 Agent 真实向亮哥发送【第二十一阶段：撒娇省略号净化测试】语音 ===")
    gateway = QQGateway(MockAgentFactory)
    
    async with aiohttp.ClientSession() as session:
        gateway._http = session
        
        # 发送第二十一阶段导语
        intro = "亮哥，第二十一阶段的【撒娇情绪黄金语速 1.02 + 普适省略号自动过滤】盲听测试已就绪！下面我将发送一段带有省略号的甜美撒娇文本合成的音频，请您盲听确认字句是否饱满完整、有无吞字奇怪停顿："
        try:
            await gateway._send("private", "1705919142", "", intro, skip_delay=True)
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"❌ 发送导语失败: {e}")
            traceback.print_exc()

        # 撒娇测试文本（带有中英文省略号）
        text = "亮哥对我最好了……小萤最喜欢亮哥了，要一直一直陪着我哦！..."
        try:
            await gateway._send("private", "1705919142", "", f"【撒娇（1.02语速 + 省略号自动过滤版）】\n发送文本: {text}", skip_delay=True)
            print("开始合成并发送语音...")
            await gateway._send_voice("private", "1705919142", "", text, "撒娇", is_test=True)
            await asyncio.sleep(5.0)
        except Exception as e:
            print(f"❌ 语音发送过程中抛出异常: {e}")
            traceback.print_exc()
        
        print("✅ 第二十一阶段测试语音发送完成！")

if __name__ == "__main__":
    os.environ["GPT_SOVITS_API_URL"] = "http://127.0.0.1:9880"
    os.environ["NAPCAT_HTTP_URL"] = "http://127.0.0.1:3020"
    asyncio.run(send_stage2_test())
