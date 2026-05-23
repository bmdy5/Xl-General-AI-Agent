import pytest
import json
import asyncio
from typing import Any, AsyncGenerator, Optional
from agent.tools.registry import ToolRegistry
from agent.tools.base_tool import BaseTool, ToolResult
from agent.core import Agent
from agent.core.react_loop import run_loop

class MockRepeatLLM:
    """Mock 大模型：始终返回同名、同参的工具调用。"""
    def __init__(self, tool_name="dummy_tool", arguments='{"param": "value"}'):
        self.model = "openai/gpt-4o"
        self.tool_name = tool_name
        self.arguments = arguments
        self.call_count = 0

    async def chat(self, messages, tools=None):
        self.call_count += 1
        return {
            "content": "我需要调用工具来解决这个问题。",
            "tool_calls": [
                {
                    "id": f"tc_{self.call_count}",
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "arguments": self.arguments
                    }
                }
            ],
            "reasoning_content": "分析中..."
        }

class DummyTool(BaseTool):
    """一个简单的虚拟测试工具，完美实现 BaseTool 接口。"""
    
    @property
    def name(self) -> str:
        return "dummy_tool"

    async def description(self) -> str:
        return "a dummy tool for testing deadlock fuse"

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "a dummy tool for testing",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param": {"type": "string"}
                    },
                    "required": ["param"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        yield ToolResult(type="result", data="Tool execution success")

@pytest.mark.asyncio
async def test_deadlock_fuse_trigger():
    # 1. 注册工具
    reg = ToolRegistry()
    reg.register(DummyTool())
    
    # 2. 初始化 mock LLM（它会连续输出完全相同的 dummy_tool {"param": "value"}）
    mock_llm = MockRepeatLLM()
    
    # 3. 初始化 Agent
    agent = Agent(llm=mock_llm, registry=reg)
    agent.messages = [{"role": "user", "content": "帮我运行这个任务"}]
    
    # 手动重置任务开始时间与总 Token 字段，防止单元测试被判定超时或属性缺失
    import asyncio
    agent._task_start_time = asyncio.get_event_loop().time()
    agent._total_tokens = 0
    agent._turn_count = 0
    
    # 4. 运行 ReAct 核心循环并收集事件
    events = []
    async for event in run_loop(agent, "帮我运行这个任务", turn=0, stream=False):
        events.append(event)
        
        # 防御性退出：一旦 max_turns 或 completed 发生即跳出
        if event.get("type") in ("completed", "max_turns"):
            break
        if len(events) > 30:
            break

    # 5. 断言判定：前 3 次工具正常执行，第 4 次触发物理熔断拦截并注回大模型警告
    tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
    
    assert len(tool_messages) >= 4
    
    # 前 3 次调用成功
    for i in range(3):
        assert "Tool execution success" in tool_messages[i]["content"]
        
    # 第 4 次被熔断器安全拦截，内容包含熔断错误提示
    fuse_message = tool_messages[3]["content"]
    assert "【死循环安全熔断】" in fuse_message
    assert "您已连续 4 次以完全相同的参数调用工具" in fuse_message
