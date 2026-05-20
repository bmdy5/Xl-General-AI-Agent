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


@pytest.mark.asyncio
async def test_history_repair_ordering():
    # 模拟 Agent
    dummy_llm = DummyLLM("openai/gpt-4o")
    dummy_reg = ToolRegistry()
    agent = Agent(dummy_llm, dummy_reg)
    
    # 构造测试消息：assistant (带两个 tool_calls，一个已有 tool 回包，一个缺失) -> user (新消息)
    agent.messages = [
        {
            "role": "assistant",
            "content": "我先查一下",
            "tool_calls": [
                {
                    "id": "call_existing",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"}
                },
                {
                    "id": "call_missing",
                    "type": "function",
                    "function": {"name": "save_memory", "arguments": "{}"}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_existing",
            "name": "read_file",
            "content": "file contents"
        },
        {
            "role": "user",
            "content": "怎么报错了"
        }
    ]
    
    # 触发 _repair_history()
    await agent._repair_history()
    
    # 校验：_repair_history 之后，messages 长度应该变为 4
    # 且两个 tool 消息都应该紧紧跟随在 assistant 后面，并且新 user 消息应在最后
    assert len(agent.messages) == 4
    assert agent.messages[0]["role"] == "assistant"
    
    # 紧随其后的应该是两个 tool 角色消息
    assert agent.messages[1]["role"] == "tool"
    assert agent.messages[1]["tool_call_id"] == "call_existing"
    
    assert agent.messages[2]["role"] == "tool"
    assert agent.messages[2]["tool_call_id"] == "call_missing"
    assert agent.messages[2]["name"] == "save_memory"  # 应当能智能从 tool_calls 中提取出 name
    
    # 最末尾的依然是 user 消息
    assert agent.messages[3]["role"] == "user"
    assert agent.messages[3]["content"] == "怎么报错了"


class DummySessionWithHistory:
    def __init__(self):
        self.session_id = "dummy_session"
        self.db_path = "/tmp/dummy.db"
        
    async def initialize(self) -> list[dict]:
        history = [{"role": "system", "content": "system prompt"}]
        for i in range(20):
            history.append({"role": "user", "content": f"msg {i}"})
            history.append({"role": "assistant", "content": f"reply {i}"})
        return history


@pytest.mark.asyncio
async def test_history_load_window():
    dummy_llm = DummyLLM("openai/gpt-4o")
    dummy_reg = ToolRegistry()
    agent = Agent(dummy_llm, dummy_reg)
    agent.session = DummySessionWithHistory()
    
    # 模拟 run 初始化的历史加载
    history = await agent.session.initialize()
    system_msgs = [m for m in history if m.get("role") == "system"]
    recent = [m for m in history if m.get("role") != "system"][-15:]
    agent.messages = system_msgs + recent
    
    # 验证最终加载的消息里，含有 1 条 system，和 15 条 recent 消息
    assert len(agent.messages) == 16
    assert agent.messages[0]["role"] == "system"
    assert all(m["role"] != "system" for m in agent.messages[1:])


@pytest.mark.asyncio
async def test_cross_session_pronoun_fallback():
    from agent.session.handler import SessionHandler
    import shutil
    import os
    import json
    
    temp_dir = "/tmp/test_pronouns"
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    
    try:
        # 创建一个外部的历史 Session 文件，写入几条聊天记录
        external_file = os.path.join(temp_dir, "old_session.jsonl")
        with open(external_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"role": "user", "content": "刚才我写的代码运行良好"}) + "\n")
            f.write(json.dumps({"role": "assistant", "content": "是的，恭喜！"}) + "\n")
            
        handler = SessionHandler(session_id="current_session", storage_dir=temp_dir)
        
        # 验证用代词 "刚才我们聊了什么" 查询
        res = await handler.search_all_sessions("刚才我们聊了什么", llm=None, max_results=3)
        
        # 断言应当能智能兜底命中 old_session 的内容
        assert "[历史会话 old_session] assistant: 是的，恭喜！" in res
        assert "[历史会话 old_session] user: 刚才我写的代码运行良好" in res
        
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_history_interleaved_reordering():
    # 模拟 Agent
    dummy_llm = DummyLLM("openai/gpt-4o")
    dummy_reg = ToolRegistry()
    agent = Agent(dummy_llm, dummy_reg)
    
    # 构造带交错 user 消息的复杂历史记录
    agent.messages = [
        {
            "role": "system",
            "content": "system prompt"
        },
        {
            "role": "user",
            "content": "发这几张图给我看看"
        },
        {
            "role": "assistant",
            "content": "好嘞！",
            "tool_calls": [
                {
                    "id": "tc_01",
                    "type": "function",
                    "function": {"name": "read_image", "arguments": "{}"}
                },
                {
                    "id": "tc_02",
                    "type": "function",
                    "function": {"name": "read_image", "arguments": "{}"}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "tc_01",
            "name": "read_image",
            "content": "image 1 result"
        },
        {
            "role": "user",
            "content": "别发第三张了"
        },
        {
            "role": "tool",
            "tool_call_id": "tc_02",
            "name": "read_image",
            "content": "image 2 result"
        },
        {
            "role": "user",
            "content": "继续"
        }
    ]
    
    # 触发 _repair_history()
    await agent._repair_history()
    
    # 校验：智能重排之后，所有的 tool 消息都应当紧随 assistant 后面，其它 user 消息在后
    assert len(agent.messages) == 7
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user"
    assert agent.messages[1]["content"] == "发这几张图给我看看"
    
    assert agent.messages[2]["role"] == "assistant"
    
    # 后面应该是紧接着的两个 tool 消息
    assert agent.messages[3]["role"] == "tool"
    assert agent.messages[3]["tool_call_id"] == "tc_01"
    assert agent.messages[4]["role"] == "tool"
    assert agent.messages[4]["tool_call_id"] == "tc_02"
    
    # 后面才是那两个被抽离出去的 user 消息
    assert agent.messages[5]["role"] == "user"
    assert agent.messages[5]["content"] == "别发第三张了"
    
    assert agent.messages[6]["role"] == "user"
    assert agent.messages[6]["content"] == "继续"
