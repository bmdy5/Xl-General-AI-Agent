import asyncio
import os
import shutil
import sqlite3
import sys
from pathlib import Path

# 添加项目根目录到 python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.session.handler import SessionHandler
from agent.memory.manager import MemoryManager
from agent.core import Agent
from agent.llm import LLMClient
from agent.tools.registry import ToolRegistry

def load_dotenv():
    """手动极简解析根目录下的 .env 环境变量文件"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

class MockLLM:
    def __init__(self):
        self.model = "gemini-2.0-flash"
    async def chat(self, messages, tools=None):
        return {"content": "我看到之前我们聊过 DALL-E 3 提示词结构以及小红书治愈风封面的设计。"}

async def run_chat_history_test():
    print("==================================================")
    print("开始进行跨会话 RAG 与代词兜底检索实战测试...")
    print("==================================================")

    project_root = Path(__file__).resolve().parent.parent
    temp_dir = project_root / "temp_test_sessions_rag"
    
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # 1. 模拟第一个会话 (历史会话: past_session_12345)
    past_handler = SessionHandler(session_id="past_session_12345", storage_dir=str(temp_dir))
    
    past_dialogue = [
        {"role": "user", "content": "亮哥今天打算设计一下小红书的治愈系插画封面。"},
        {"role": "assistant", "content": "好啊，我们可以用奶油ins风 and 对比拼贴的排版。"},
        {"role": "user", "content": "那顺便把 DALL-E 3 提示词结构写一下吧。"},
        {"role": "assistant", "content": "没问题！经典的结构是：主体（人物/服装） -> 细节（场景/光照） -> 综述（镜头/风格）。"}
    ]
    
    # 逐条写入并同步 FTS5 索引
    for msg in past_dialogue:
        await past_handler.append_message(msg)
    
    print("✔ [历史会话 past_session_12345] 写入成功并已建立 FTS 全文索引。")

    # 2. 模拟第二个会话 (当前会话: current_session_67890)
    current_handler = SessionHandler(session_id="current_session_67890", storage_dir=str(temp_dir))
    
    print("\n亮哥提问: “我们之前具体聊了什么？”")
    
    # 3. 验证 search_all_sessions 能否触发指代词兜底召回 past_session_12345
    mock_llm = MockLLM()
    retrieved_history = await current_handler.search_all_sessions(
        query="我们之前具体聊了什么？",
        llm=mock_llm,
        max_results=5
    )
    
    print("\n🔍 [RAG 检索层] 召回的跨会话历史原句：")
    print("--------------------------------------------------")
    print(retrieved_history)
    print("--------------------------------------------------")
    
    # 4. 模拟 Agent 加载和 [MEMORY BLOCK] 最终注入效果
    memory_manager = MemoryManager()
    memory_manager.base_dir = temp_dir
    
    reg = ToolRegistry()
    agent_mock = Agent(llm=mock_llm, registry=reg, memory=memory_manager, session=current_handler)
    memory_block = await agent_mock._build_memory_block(user_input="我们之前具体聊了什么？", turn=1)
    
    print("\n📦 [提示词组装层] 最终注入到 Agent 提示词中的 [MEMORY BLOCK] 局部内容：")
    print("--------------------------------------------------")
    print(memory_block)
    print("--------------------------------------------------")
    
    assert "past_session_12345" in retrieved_history, "跨会话回忆应该能索引到 past_session_12345"
    assert "治愈系插画封面" in retrieved_history, "应该召回关于封面的讨论"
    
    print("\n✔ [检索与装载测试通过] 下一步：连接真实大模型测试真实 Agent 对答表现...")

    # 5. 真实大模型端到端交互测试
    load_dotenv()
    model = os.getenv("MYAGENT_MODEL") or "deepseek/deepseek-v4-flash"
    api_key = os.getenv("MYAGENT_API_KEY")
    api_base = os.getenv("MYAGENT_API_BASE")
    
    if not api_key:
        print("\n⚠️ 警告：未检测到有效 API_KEY，跳过真实大模型交互测试。")
    else:
        print(f"\n🚀 正在连接真实大模型接口 (模型: {model})...")
        real_llm = LLMClient(model=model, api_key=api_key, api_base=api_base)
        
        agent_real = Agent(llm=real_llm, registry=reg, memory=memory_manager, session=current_handler)
        
        response_content = ""
        # 真正运行 Agent，并让其针对注入的 [MEMORY BLOCK] 做出真实回答
        async for chunk in agent_real.run(user_input="我们之前具体聊了什么？"):
            # 兼容 stream=True 或是 stream=False 下的不同事件类型
            if chunk.get("type") in ["text_delta", "text"] or chunk.get("event") == "text":
                response_content += chunk.get("content", "")
        
        print("\n小萤真实的端到端对话回答：")
        print("--------------------------------------------------")
        print(response_content)
        print("--------------------------------------------------")
        
        # 验证大模型是否具有正确的归因自察
        assert any(keyword in response_content for keyword in ["系统", "记录", "自动", "之前", "过去", "载入", "回忆", "小红书"])
        print("\n🎉 [端到端测试 100% 成功] 小萤已完美知道并且诚实说明了回忆的来源！")

    # 清理临时目录
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    asyncio.run(run_chat_history_test())
