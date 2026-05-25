import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agent.core.llm import LLMClient
from agent.core.agent import Agent
from agent.tools.registry import ToolRegistry
from agent.core.react_loop import run_loop, llm_chat, llm_stream

# ── 用例 1: 校验 llm.py 在非流式 chat() 下对嵌套缓存 cached_tokens 的抓取 ──
@pytest.mark.asyncio
async def test_llm_chat_metrics_extraction():
    client = LLMClient(model="openai/gpt-4o")

    # 1. 模拟包含 openai/deepseek 风格的嵌套 prompt_tokens_details 结构
    mock_prompt_details = MagicMock()
    mock_prompt_details.cached_tokens = 4096

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 8192
    mock_usage.completion_tokens = 512
    mock_usage.total_tokens = 8704
    mock_usage.prompt_tokens_details = mock_prompt_details

    mock_choice = MagicMock()
    mock_choice.message.content = "我是一只小萤"
    mock_choice.message.tool_calls = None
    mock_choice.message.reasoning_content = "思考中..."

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    # mock acompletion
    with patch("agent.core.llm.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion:
        res = await client.chat(messages=[{"role": "user", "content": "你好"}])
        
        assert res["tokens_used"] == 8704
        assert res["metrics"]["prompt_tokens"] == 8192
        assert res["metrics"]["completion_tokens"] == 512
        assert res["metrics"]["cached_tokens"] == 4096
        assert res["metrics"]["total_tokens"] == 8704


# ── 用例 2: 校验 llm.py 在流式 chat_stream() 下对空 choices / usage chunk 的白盒防御拦截 ──
@pytest.mark.asyncio
async def test_llm_stream_empty_choices_usage_interception():
    client = LLMClient(model="openai/gpt-4o")

    # 模拟流式 chunks 迭代
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta.content = "第一句话"
    chunk1.choices[0].delta.tool_calls = None
    chunk1.choices[0].delta.reasoning_content = None

    # chunk2 模拟末尾 choices 为空、仅含 usage 属性的特殊 usage chunk
    mock_prompt_details = MagicMock()
    mock_prompt_details.cached_tokens = 1024

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 2048
    mock_usage.completion_tokens = 256
    mock_usage.total_tokens = 2304
    mock_usage.prompt_tokens_details = mock_prompt_details

    chunk2 = MagicMock()
    del chunk2.choices  # 模拟没有 choices 属性或为空列表
    chunk2.usage = mock_usage

    # 模拟 async generator
    class AsyncIter:
        def __init__(self, items):
            self.items = items
            self.idx = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self.idx >= len(self.items):
                raise StopAsyncIteration
            item = self.items[self.idx]
            self.idx += 1
            return item

    mock_stream_response = AsyncIter([chunk1, chunk2])

    with patch("agent.core.llm.acompletion", AsyncMock(return_value=mock_stream_response)):
        events = []
        async for ev in client.chat_stream(messages=[{"role": "user", "content": "你好"}], model_override="openai/gpt-4o"):
            events.append(ev)
        
        # 验证正常文本得到了 yield
        assert any(e["type"] == "text_delta" and e["content"] == "第一句话" for e in events)
        
        # 验证空 choices chunk 的 usage 得到了精准截获，并没有触发 IndexError
        usage_events = [e for e in events if e["type"] == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["data"]["prompt_tokens"] == 2048
        assert usage_events[0]["data"]["cached_tokens"] == 1024
        assert usage_events[0]["data"]["completion_tokens"] == 256


# ── 用例 3: 校验 React 思考循环与 Agent 端 Token 统计指标累加 ──
@pytest.mark.asyncio
async def test_react_loop_token_accumulation():
    # 模拟 Registry 与 Agent
    registry = ToolRegistry()
    
    class MockLLMClient:
        def __init__(self):
            self.model = "deepseek/deepseek-chat"
        async def chat(self, messages, tools=None):
            return {
                "content": "我解析完毕了",
                "tool_calls": [],
                "reasoning_content": "白盒模拟思考",
                "tokens_used": 500,
                "metrics": {
                    "prompt_tokens": 400,
                    "completion_tokens": 100,
                    "total_tokens": 500,
                    "cached_tokens": 200,
                }
            }

    agent = Agent(llm=MockLLMClient(), registry=registry)
    agent.messages = [{"role": "user", "content": "测试 Token 累加"}]

    # 执行非流式 chat 注入与计算
    content, reasoning, tool_calls = await agent._llm_chat(agent.messages, [])
    
    assert agent._prompt_tokens == 400
    assert agent._completion_tokens == 100
    assert agent._cached_tokens == 200
    assert agent._total_tokens == 500

    # 计算命中率
    hit_rate = (agent._cached_tokens / agent._prompt_tokens * 100) if agent._prompt_tokens > 0 else 0.0
    assert hit_rate == 50.0


# ── 用例 4: 校验 ReAct 绝对前缀纯净化（在 turn > 0 时, 历史消息保持 100% 静态文字） ──
@pytest.mark.asyncio
async def test_react_prefix_purification():
    registry = ToolRegistry()
    
    # 记录发送给 LLM 接口的所有最终 messages 列表副本，以核验尾部追加临时 System 消息的真实轨迹
    llm_calls_history = []
    
    class MockLLMClientForTrace:
        def __init__(self):
            self.model = "openai/gpt-4o"
            self.api_key = "mock"
            self.api_base = "mock"
            self.deepseek_api_key = ""
            self.temperature = 1.0
            self.max_tokens = 1000
            self.model_pro = "openai/gpt-4o"
        async def chat(self, messages, tools=None, abort_event=None, model_override=""):
            llm_calls_history.append(list(messages))
            return {
                "content": "执行完毕",
                "tool_calls": [],
                "tokens_used": 100,
                "metrics": {"prompt_tokens": 80, "completion_tokens": 20, "cached_tokens": 0, "total_tokens": 100}
            }

    agent = Agent(llm=MockLLMClientForTrace(), registry=registry)
    # 物理初始化任务开始时间，防止被 run_loop 内部的超时熔断器拦截
    agent._task_start_time = asyncio.get_event_loop().time()
    
    # 模拟执行 run_loop
    # 注意: 我们向 agent.messages 存入纯净的用户消息
    user_input = "开始执行净化测试"
    agent.messages.append({"role": "user", "content": user_input})
    
    # 物理调用 run_loop 迭代，这会触发 llm_chat，并完成 messages 的最终拼装
    async for event in run_loop(agent, user_input, turn=0, stream=False):
        pass
    
    # 验证 trace 的 messages
    assert len(llm_calls_history) == 1
    sent_msgs = llm_calls_history[0]
    
    # 黄金断言 1: 静态核心 Prompt 和动态环境上下文被分拆成两条独立的 System 消息以彻底阻断前缀抖动
    assert sent_msgs[0]["role"] == "system"
    assert sent_msgs[1]["role"] == "system"
    assert "当前环境上下文" in sent_msgs[1]["content"]
    
    # 黄金断言 2: 最尾端的消息就是用户最新发送的消息，它不再受到尾部临时消息的抖动干扰
    assert sent_msgs[-1]["role"] == "user"
    assert sent_msgs[-1]["content"] == "开始执行净化测试"
    
    # 黄金断言 3: agent.messages 在被 llm_chat 处理后，其 User 消息依然保持纯净，没有被物理重写
    assert agent.messages[0]["content"] == "开始执行净化测试"


# ── 用例 5: 校验 llm.py 在非标准流式下对非空 choices 数据帧所携带 usage 的精准提取与去重 ──
@pytest.mark.asyncio
async def test_llm_stream_data_chunk_usage_capture():
    client = LLMClient(model="openai/gpt-4o")

    # chunk1 模拟携带数据且携带 usage 属性的非标数据帧
    mock_prompt_details1 = MagicMock()
    mock_prompt_details1.cached_tokens = 500

    mock_usage1 = MagicMock()
    mock_usage1.prompt_tokens = 1000
    mock_usage1.completion_tokens = 100
    mock_usage1.total_tokens = 1100
    mock_usage1.prompt_tokens_details = mock_prompt_details1

    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta.content = "第一句话"
    chunk1.choices[0].delta.tool_calls = None
    chunk1.choices[0].delta.reasoning_content = None
    chunk1.usage = mock_usage1

    # chunk2 模拟下一个普通数据帧，虽然也可能带有 usage（例如某些每一帧都带 usage 的渠道），但应该被我们的 usage_yielded 过滤掉
    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta.content = "第二句话"
    chunk2.choices[0].delta.tool_calls = None
    chunk2.choices[0].delta.reasoning_content = None
    chunk2.usage = mock_usage1

    # 模拟 async generator
    class AsyncIter:
        def __init__(self, items):
            self.items = items
            self.idx = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self.idx >= len(self.items):
                raise StopAsyncIteration
            item = self.items[self.idx]
            self.idx += 1
            return item

    mock_stream_response = AsyncIter([chunk1, chunk2])

    with patch("agent.core.llm.acompletion", AsyncMock(return_value=mock_stream_response)):
        events = []
        async for ev in client.chat_stream(messages=[{"role": "user", "content": "你好"}], model_override="openai/gpt-4o"):
            events.append(ev)
        
        # 验证文本正常 yield
        assert any(e["type"] == "text_delta" and e["content"] == "第一句话" for e in events)
        assert any(e["type"] == "text_delta" and e["content"] == "第二句话" for e in events)
        
        # 验证虽然两个 chunk 都携带了 usage 属性，但是由于 usage_yielded 去重机制，只 yield 了一次 usage 事件
        usage_events = [e for e in events if e["type"] == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["data"]["prompt_tokens"] == 1000
        assert usage_events[0]["data"]["cached_tokens"] == 500
        assert usage_events[0]["data"]["completion_tokens"] == 100
        assert usage_events[0]["data"]["total_tokens"] == 1100


# ── 用例 6: 校验思考流/正文中 Markdown 代码块包裹的 tool_calls 抢救性提取与【尾部最新优先】排重 ──
def test_scavenge_thinking_leakage_tool_calls():
    from agent.core.history_repair import scavenge_tool_calls
    
    # 模拟大模型输出的文本流（其中含有思维溢出的 markdown 代码块，且对 write_file 进行了两次调用演示，第二轮是修正值）
    leak_text = (
        "我刚才想了一下，我需要对 core/llm.py 写入内容。\n"
        "第一轮草稿是：\n"
        "```json\n"
        "{\n"
        "  \"name\": \"write_file\",\n"
        "  \"arguments\": {\"file_path\": \"wrong_path.py\", \"content\": \"print(1)\"}\n"
        "}\n"
        "```\n"
        "不对，我需要修正为：\n"
        "```json\n"
        "{\n"
        "  \"name\": \"write_file\",\n"
        "  \"arguments\": {\"file_path\": \"agent/core/llm.py\", \"content\": \"print(2)\"}\n"
        "}\n"
        "```\n"
        "这句不是代码块 `{'name': 'edit_file'}` 不需要扫描。"
    )
    
    allowed = ["write_file", "read_file", "edit_file"]
    scavenged = scavenge_tool_calls(leak_text, allowed)
    
    # 验证同名工具只提取到了最后一个 (尾部最新优先)
    assert len(scavenged) == 1
    call = scavenged[0]
    assert call["function"]["name"] == "write_file"
    
    # 验证提取出的 arguments 是第二个（最新的）修正值
    import json
    args = json.loads(call["function"]["arguments"])
    assert args["file_path"] == "agent/core/llm.py"
    assert args["content"] == "print(2)"


# ── 用例 7: 校验 JSON 截断修复的【安全分流与高危修改熔断】 ──
@pytest.mark.asyncio
async def test_json_repair_safety_sandboxing_分流():
    from agent.core.history_repair import repair_truncated_json
    from agent.core.agent import AgentMode, PermissionCategory
    
    # 1. 验证 repair_truncated_json 的闭合修护算法本身
    broken_args = '{"file_path": "agent/core/llm.py", "lines_count": 800'
    repaired = repair_truncated_json(broken_args)
    assert repaired == '{"file_path": "agent/core/llm.py", "lines_count": 800}'
    
    # 2. 模拟在 ReAct 思考循环中，调用只读与写入工具遇到截断时的安全分流
    class DummyAgent:
        def __init__(self):
            self.registry = MagicMock()
            self.registry.list_names = MagicMock(return_value=["read_file", "write_file"])
            self.registry.get = MagicMock(return_value=None)
            self.registry.dispatch = AsyncMock(return_value="{}")
            self.messages = []
            self._mode = AgentMode.NORMAL
            self.max_turns = 1
            self._abort = asyncio.Event()
            self._task_start_time = asyncio.get_event_loop().time()
            self._turn_count = 0
            self.llm = MagicMock()
            self.session = None
            self.compressor = MagicMock()
            self.compressor.estimate_tokens = MagicMock(return_value=0)
            self.compressor.should_compress = MagicMock(return_value=False)
            
        def _classify_permission(self, name, args):
            return PermissionCategory.SAFE
            
        async def _build_system_prompt(self):
            return "system"
        async def _build_memory_block(self, input, turn):
            return "memory"
        def _quick_transition(self, input):
            return None
        async def _repair_history(self):
            pass
        async def _apply_sliding_window_and_scratchpad(self):
            pass
        def _create_tracked_task(self, coro):
            pass
            
    # 用例 2a: 只读工具 read_file 发生 arguments 截断 ➔ 应当自愈成功
    read_tc = [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": '{"file_path": "agent/core/llm.py", "lines": 800' # 截断
        }
    }]
    
    agent = DummyAgent()
    agent.llm.chat_stream = MagicMock()
    # 模拟 llm.chat_stream yield tool_call 事件，其 data 就是 read_tc[0]
    async def mock_chat_stream(*args, **kwargs):
        yield {"type": "tool_call", "data": read_tc[0]}
    agent.llm.chat_stream.side_effect = mock_chat_stream
    
    # 执行 run_loop 并核验只读工具自愈正常
    events = []
    async for ev in run_loop(agent, "test", turn=0, stream=True):
        events.append(ev)
        
    # 成功捕获到了 tool_call 事件，且参数已被自愈修复
    tc_events = [e for e in events if e.get("type") == "tool_call" and "args" in e]
    assert len(tc_events) == 1
    assert tc_events[0]["args"]["file_path"] == "agent/core/llm.py"
    assert tc_events[0]["args"]["lines"] == 800
    
    # 用例 2b: 写入工具 write_file 发生 arguments 截断 ➔ 应当熔断报错
    write_tc = [{
        "id": "call_2",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"file_path": "agent/core/llm.py", "content": "class" ' # 截断
        }
    }]
    
    agent_write = DummyAgent()
    agent_write.llm.chat_stream = MagicMock()
    async def mock_chat_stream_write(*args, **kwargs):
        yield {"type": "tool_call", "data": write_tc[0]}
    agent_write.llm.chat_stream.side_effect = mock_chat_stream_write
    
    # 应当触发 raise json.JSONDecodeError 并捕获为崩溃退出，绝不执行写入
    with pytest.raises(Exception):
        async for _ in run_loop(agent_write, "test", turn=0, stream=True):
            pass


# ── 用例 8: 校验 AgentExecutor 在接收到 completed 事件时成功触发 unified token metrics 审计日志 ──
@pytest.mark.asyncio
async def test_executor_completed_event_token_metrics(monkeypatch):
    from agent.net_gateway.executor import AgentExecutor
    
    # 模拟 context, bus, dispatcher 等网关底层对象
    mock_context = MagicMock()
    mock_context.activity_logger = MagicMock()
    mock_context.send_msg = AsyncMock()
    mock_context._last_voice_time = 0.0
    
    mock_bus = MagicMock()
    mock_bus.wait_for_carrier_sense = AsyncMock(return_value=False)
    mock_bus.is_collision = MagicMock(return_value=False)
    
    mock_dispatcher = MagicMock()
    mock_dispatcher.bot = MagicMock()
    mock_dispatcher.bus = mock_bus
    
    executor = AgentExecutor(
        context=mock_context,
        dispatcher=mock_dispatcher
    )
    
    # 模拟 Agent 和它的 run() 方法 yield "completed" 事件
    mock_agent = MagicMock()
    mock_agent._prompt_tokens = 30000
    mock_agent._cached_tokens = 20000
    mock_agent._completion_tokens = 5000
    mock_agent._total_tokens = 35000
    mock_agent.compressor = None
    
    async def mock_run(*args, **kwargs):
        yield {"type": "completed"}
        
    mock_agent.run = mock_run
    
    # 执行 execute_agent_run
    session_key = "user_12345"
    import time
    await executor.execute_agent_run(
        agent=mock_agent,
        raw="你好",
        session_key=session_key,
        msg_type="private",
        user_id="12345",
        group_id="",
        sender_name="亮哥",
        task_start_time=time.time()
    )
    
    # 验证 log_metrics 确实被调用了，并且传入了正确的 tokens 与命中率参数
    mock_context.activity_logger.log_metrics.assert_called_once_with(
        session_key=session_key,
        prompt_tokens=30000,
        cached_tokens=20000,
        completion_tokens=5000,
        total_tokens=35000,
        is_estimated=False,
        user_id="12345"
    )

