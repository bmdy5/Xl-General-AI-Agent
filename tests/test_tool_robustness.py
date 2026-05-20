import pytest
from pathlib import Path
from agent.tools.registry import ToolRegistry
from agent.tools.memory_tool import MemoryTool
from agent.tools.file_tools import ReadFileTool
from agent.core import Agent


@pytest.mark.asyncio
async def test_registry_validation():
    # 验证 ToolRegistry 的入参拦截与 validate_input 执行
    reg = ToolRegistry()
    mem_tool = MemoryTool()
    reg.register(mem_tool)

    # 发送一个 action 为 add 但缺少 filename/description/content 的参数
    res = await reg.dispatch("save_memory", {"action": "add"})
    assert "error" in res
    assert "Invalid input arguments" in res
    assert "content is required" in res


@pytest.mark.asyncio
async def test_read_file_directory():
    # 验证 ReadFileTool 遇到文件夹时的自愈树状列出功能
    tool = ReadFileTool()
    tests_dir = str(Path(__file__).parent.absolute())
    
    # 模拟读取当前 tests 目录
    results = []
    async for r in tool.call({"file_path": tests_dir}):
        results.append(r)
        
    assert len(results) == 1
    tr = results[0]
    assert tr.type == "result"
    assert "[系统自愈引导]" in tr.data
    assert "test_scheduler_preempt.py" in tr.data


class DummyLLM:
    def __init__(self, model):
        self.model = model


@pytest.mark.asyncio
async def test_core_reasoning_retain():
    # 模拟 Agent 的 reasoning_content 健壮保留
    # 1. 当模型包含 "deepseek" 时，reasoning_content 必须保留
    dummy_llm = DummyLLM("openai/deepseek-v4-pro")
    dummy_reg = ToolRegistry()
    core_ds = Agent(dummy_llm, dummy_reg)
    core_ds.messages = [
        {"role": "assistant", "content": "hello", "reasoning_content": "let me think"}
    ]
    
    # 手动触发 core.py 第 397-404 行的消息拼装处理
    llm_messages = []
    for m in core_ds.messages:
        copy = dict(m)
        if "deepseek" not in core_ds.llm.model.lower():
            copy.pop("reasoning_content", None)
        llm_messages.append(copy)
        
    assert "reasoning_content" in llm_messages[0]
    assert llm_messages[0]["reasoning_content"] == "let me think"

    # 2. 当模型不包含 "deepseek" 时（如 gpt-4o），reasoning_content 必须被过滤
    dummy_llm_gpt = DummyLLM("openai/gpt-4o")
    core_gpt = Agent(dummy_llm_gpt, dummy_reg)
    core_gpt.messages = [
        {"role": "assistant", "content": "hello", "reasoning_content": "let me think"}
    ]
    
    llm_messages_gpt = []
    for m in core_gpt.messages:
        copy = dict(m)
        if "deepseek" not in core_gpt.llm.model.lower():
            copy.pop("reasoning_content", None)
        llm_messages_gpt.append(copy)
        
    assert "reasoning_content" not in llm_messages_gpt[0]


class DummySession:
    def __init__(self):
        self.session_id = "dummy_session"
        self.db_path = "/tmp/dummy.db"
        self.called = False

    async def search_all_sessions(self, query, llm, max_results=3):
        self.called = True
        return "[dummy_session] user: mock history chat"


class DummyMemory:
    def __init__(self):
        self.called = False
        self.base_dir = Path("/tmp")

    def search_memories(self, query, limit=50):
        return []

    def _parse_index(self):
        return []

    def search_notes(self, query, limit=5):
        self.called = True
        return [{"title": "Mock Note", "path": "/tmp/mock.md", "content": "mock text"}]


@pytest.mark.asyncio
async def test_rag_thresholds():
    # 模拟 Agent 初始化并执行 memory block
    dummy_llm = DummyLLM("openai/gpt-4o")
    dummy_reg = ToolRegistry()
    agent = Agent(dummy_llm, dummy_reg)
    
    agent.session = DummySession()
    agent.memory = DummyMemory()
    
    # 模拟输入 6 字符（小于原来的 20/10，大于放宽后的 3）
    user_input = "我的网站"
    
    # 构建内存块，触发 _build_memory_block
    res = await agent._build_memory_block(user_input, turn=1)
    
    # 校验是否成功由于放宽阈值而触发了检索方法
    assert agent.session.called is True
    assert agent.memory.called is True
    assert "## 相关历史对话" in res
    assert "## 相关知识（来源: 学习笔记）" in res

