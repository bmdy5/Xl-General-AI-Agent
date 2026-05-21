import asyncio
import os
import sys
import aiohttp

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from agent.gateway import QQGateway

class MockAgentFactory:
    pass

async def send_stage2_test():
    print("=== 🚀 开始让 Agent 真实向亮哥发送【第二阶段：傲娇 & 撒娇】语音测试 ===")
    gateway = QQGateway(MockAgentFactory)
    
    async with aiohttp.ClientSession() as session:
        gateway._http = session
        
        # 1. 发送第二十阶段导语
        intro = "亮哥，傲娇标点符号净化盲听测试已就绪！以下为三组不同停顿/断句方案的音频，请您盲听确认哪一个最自然、吐字最清晰："
        await gateway._send("private", "1705919142", "", intro, skip_delay=True)
        await asyncio.sleep(1.0)

        # 2. 方案 A：纯字无符号
        text_a = "都都说了才不是因为想你才和你说话的！亮哥最差劲了！"
        await gateway._send("private", "1705919142", "", "【方案 A - 纯字无任何标点停顿】", skip_delay=True)
        await gateway._send_voice("private", "1705919142", "", text_a, "傲娇", is_test=True)
        await asyncio.sleep(4.5)

        # 3. 方案 B：顿号口吃 + 句号物理收口
        text_b = "都、都说了才不是因为想你才和你说话的。亮哥最差劲了！"
        await gateway._send("private", "1705919142", "", "【方案 B - 顿号轻停顿 + 句号物理换气收口】", skip_delay=True)
        await gateway._send_voice("private", "1705919142", "", text_b, "傲娇", is_test=True)
        await asyncio.sleep(4.5)

        # 4. 方案 C：英文点口吃 + 句号物理收口
        text_c = "都.都说了才不是因为想你才和你说话的。亮哥最差劲了！"
        await gateway._send("private", "1705919142", "", "【方案 C - 英文小点停顿 + 句号物理换气收口】", skip_delay=True)
        await gateway._send_voice("private", "1705919142", "", text_c, "傲娇", is_test=True)
        await asyncio.sleep(3.0)
        
        print("✅ 第二阶段测试语音发送完成！")

if __name__ == "__main__":
    os.environ["GPT_SOVITS_API_URL"] = "http://127.0.0.1:9880"
    os.environ["NAPCAT_HTTP_URL"] = "http://127.0.0.1:3020"
    asyncio.run(send_stage2_test())
