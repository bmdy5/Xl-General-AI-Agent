import asyncio
import pytest
import os
import time
from agent.core.gateway import QQGateway

class DummyLLM:
    async def chat(self, messages, tools=None, model_override=""):
        return {"content": "Mock text response"}

    async def chat_stream(self, messages, tools=None, abort_event=None, model_override=""):
        yield {"type": "text_delta", "content": "Mock response"}
        yield {"type": "_done"}

class DummyMemory:
    async def search_memories(self, query, limit=5):
        return []
    async def save_memory(self, content, category="xl_debugging", keywords=None):
        pass

class DummySession:
    async def replace_all(self, messages):
        pass

class DummyCompressor:
    def estimate_tokens(self, messages):
        return 100
    async def compress(self, messages, memory=None):
        return messages, False

class MockAgent:
    def __init__(self, session_key=None, messages=None, memory=None, llm=None, session=None, compressor=None):
        self.session_key = session_key
        self.llm = DummyLLM()
        self.memory = DummyMemory()
        self.messages = messages if messages is not None else []
        self.session = DummySession()
        self.compressor = DummyCompressor()
        self.current_user_id = "1705919142"
        self.role = "admin"

    async def run(self, prompt, stream=True, turn=0, context=None, state_prefix=None, real_sender_id=None, real_sender_name=None, group_id=None):
        # 吐出接收到的 prompt 内容，方便断言高情商提示词的注入结果
        yield {"type": "text_delta", "content": f"Received: {prompt}"}
        yield {"type": "_done"}

class MockSlowAgent(MockAgent):
    async def run(self, prompt, stream=True, turn=0, context=None, state_prefix=None, real_sender_id=None, real_sender_name=None, group_id=None):
        yield {"type": "text_delta", "content": f"Slow Received: {prompt}"}
        # 模拟稍长的流式响应以使后面的并发消息能够入队
        await asyncio.sleep(2.0)
        yield {"type": "_done"}

@pytest.mark.asyncio
async def test_admin_only_merge(monkeypatch):
    """验证全是亮哥（管理员）连发时的消息全量出队与专属 Prompt 渲染及重新派发的正确性"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    gw = QQGateway(agent_factory=MockSlowAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send

    # 1. 发送消息1触发初始任务，使用 MockSlowAgent 使其进入 2.0s 运行
    event1 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "亮哥消息1",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(event1)
    # 跨越载波监听退避窗口 (1.2s + 缓冲) 确保开始执行
    await asyncio.sleep(1.5)
    
    # 2. 在任务运行中途发送消息2 and 消息3，它们将被压入消息队列
    event2 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "亮哥消息2",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    event3 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "亮哥消息3",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    await dispatcher.dispatch_event(event2)
    await dispatcher.dispatch_event(event3)
    
    # 3. 等待足够时间，让消息1执行完，并拉起第二个任务彻底执行完毕（3.2s + 1.2s退避 + 2s运行 + 缓冲）
    await asyncio.sleep(6.5)

    # 拼接所有发送的历史块，用于在流式发包模式下进行白盒 Prompt 匹配
    merged_responses = "".join(sent_messages)
    
    # 检查专属合并提示词和拼接的消息
    assert "亮哥在刚才小萤思考期间连发了 2 条消息" in merged_responses
    assert "亮哥消息2" in merged_responses
    assert "亮哥消息3" in merged_responses


@pytest.mark.asyncio
async def test_mixed_users_merge(monkeypatch):
    """验证包含其他发言人时（群聊混杂）的高情商混杂 Prompt 渲染及每行姓名标注的正确性"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    # 将用户 12345678 加入安全白名单，以便模拟他人发言通过安全检查并避开默认的 1911828529 机器人ID
    monkeypatch.setenv("MY_AGENT_WHITE_LIST", "12345678")
    
    gw = QQGateway(agent_factory=MockSlowAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send

    # 1. 亮哥在群聊中发言1触发初始任务
    event1 = {
        "message_type": "group",
        "user_id": "1705919142",
        "group_id": "123456",
        "raw_message": "[CQ:at,qq=999999] 亮哥起头",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(event1)
    await asyncio.sleep(1.5)
    
    # 2. 中途收到亮哥消息2和同事A消息（用户 12345678）
    event2 = {
        "message_type": "group",
        "user_id": "1705919142",
        "group_id": "123456",
        "raw_message": "[CQ:at,qq=999999] 亮哥追问",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    event3 = {
        "message_type": "group",
        "user_id": "12345678",
        "group_id": "123456",
        "raw_message": "[CQ:at,qq=999999] 同事说一句",
        "self_id": "999999",
        "sender": {"nickname": "同事A"}
    }
    
    await dispatcher.dispatch_event(event2)
    await dispatcher.dispatch_event(event3)
    
    # 3. 等待消费完毕
    await asyncio.sleep(6.5)

    merged_responses = "".join(sent_messages)
    
    # 检查合并响应的 Prompt 格式与多人高情商标注
    assert "系统检测到在此期间有多人发言（含亮哥与他人）" in merged_responses
    assert "亮哥" in merged_responses
    assert "同事A" in merged_responses
    assert "同事说一句" in merged_responses


@pytest.mark.asyncio
async def test_carrier_sense_seamless_pass(monkeypatch):
    """验证重新派发合并后的消息时，高可用 Carrier Sense 无缝通行与自愈"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    gw = QQGateway(agent_factory=MockSlowAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send

    # 1. 亮哥消息1触发
    event1 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "消息1",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(event1)
    await asyncio.sleep(1.5)
    
    # 2. 中途连发多条
    event2 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "连发2",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    event3 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "连发3",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(event2)
    await dispatcher.dispatch_event(event3)
    
    # 3. 等待消费完毕，确保合并后重新派发的事件被无缝执行
    await asyncio.sleep(6.5)

    merged_responses = "".join(sent_messages)
    assert "连发2" in merged_responses
    assert "连发3" in merged_responses
