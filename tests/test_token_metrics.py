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
    
    # 黄金断言 1: 首条消息是包含了当前环境上下文的 System 消息 (物理合并注入以彻底阻断前缀抖动)
    assert sent_msgs[0]["role"] == "system"
    assert "当前环境上下文" in sent_msgs[0]["content"]
    
    # 黄金断言 2: 最尾端的消息就是用户最新发送的消息，它不再受到尾部临时消息的抖动干扰
    assert sent_msgs[-1]["role"] == "user"
    assert sent_msgs[-1]["content"] == "开始执行净化测试"
    
    # 黄金断言 3: agent.messages 在被 llm_chat 处理后，其 User 消息依然保持纯净，没有被物理重写
    assert agent.messages[0]["content"] == "开始执行净化测试"
