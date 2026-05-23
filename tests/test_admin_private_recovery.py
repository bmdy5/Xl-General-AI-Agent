import asyncio
import pytest
import os
from agent.core.gateway import QQGateway

class MockLLM:
    async def chat(self, messages, tools=None, model_override=""):
        return {"content": "Mock Response"}

class MockMemory:
    def search_memories(self, query, limit=5):
        return []

class MockAgent:
    def __init__(self, session_key=None, *args, **kwargs):
        self.llm = MockLLM()
        self.memory = MockMemory()
        self.messages = []
        self.session = None

    async def run(self, prompt, stream=True, **kwargs):
        yield {"type": "text_delta", "content": f"Mock reply to: {prompt}"}

@pytest.mark.asyncio
async def test_admin_private_wakeup_and_bypass(monkeypatch):
    """测试管理员私聊特权白名单判定，以及开口一瞬间物理阻断、秒级清退全局后台打盹任务的自愈逻辑"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    # 实例化网关
    gw = QQGateway(agent_factory=MockAgent)
    dispatcher = gw.dispatcher
    
    # 1. 模拟小宇（非管理员）由于频繁交互累积脑力疲劳，处于打盹做梦状态中
    session_key_xiaoyu = "user_1911828529"
    dispatcher._sleep_modes[session_key_xiaoyu] = True
    dispatcher._fatigue_levels[session_key_xiaoyu] = 100.0
    
    # 模拟为小宇关联一个正在异步跑的深度梦境 GC 净化任务
    async def mock_sleep_process():
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            # 物理取消成功
            raise

    sleep_task = asyncio.create_task(mock_sleep_process())
    dispatcher._active_sleep_tasks[session_key_xiaoyu] = sleep_task
    
    # 2. 验证当前小宇确实被卡死在打盹净化状态中
    assert dispatcher._sleep_modes.get(session_key_xiaoyu) is True
    assert dispatcher._active_sleep_tasks.get(session_key_xiaoyu) is sleep_task
    
    # 3. 模拟亮哥（管理员 1705919142）发来私聊命令
    admin_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "小红书简介设计得怎么样了？",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, *args, **kwargs):
        sent_messages.append(text)
    
    gw._send = mock_send
    
    # 触发事件分发
    await dispatcher.dispatch_event(admin_event)
    await asyncio.sleep(0.5)
    
    # 4. 验证管理员特权唤醒与强抢自愈指标：
    # A. 打盹标记（_sleep_modes）应当被全局清空
    assert not dispatcher._sleep_modes, "管理员一开口，应当物理清空全部打盹标记实现全局苏醒"
    
    # B. 脑力疲劳度（_fatigue_levels）应当被清空
    assert not dispatcher._fatigue_levels, "管理员消息应当物理清空疲劳度缓存"
    
    # C. 正在运行的后台打盹异步协程任务应当被强行 cancel 强占秒级清退，且 active tasks 被 pop 移除
    assert sleep_task.done(), "后台做梦打盹协程应当被物理 cancel 强行结束"
    assert not dispatcher._active_sleep_tasks, "活跃打盹任务缓存表应当被清空"
    
    print("🎉 Pytest Admin Private Wakeup & Bypass verification successfully passed!")


@pytest.mark.asyncio
async def test_admin_permission_request_flow(monkeypatch):
    """测试管理员私聊中的敏感指令权限申请与授权释放流程"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    class MockApprovedAgent(MockAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.approved = False

        def approve_permission(self):
            self.approved = True

        def deny_permission(self):
            self.approved = False

        async def run(self, prompt, stream=True, **kwargs):
            yield {
                "type": "permission_request",
                "category": "write",
                "tool_name": "write_file",
                "message": "写入 README.md"
            }
            yield {"type": "text_delta", "content": f"Result is approved: {self.approved}"}
            yield {"type": "_done"}

    # 实例化网关
    gw = QQGateway(agent_factory=MockApprovedAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, *args, **kwargs):
        sent_messages.append(text)
    
    gw._send = mock_send
    
    # 2. 模拟亮哥（管理员）触发需要授权的敏感操作
    admin_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "修改README文件",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    # 启动处理
    await dispatcher.dispatch_event(admin_event)
    
    session_key = "user_1705919142"
    active_task = gw._current_tasks.get(session_key)
    assert active_task is not None, "后台处理任务应当已被成功创建"
    
    await asyncio.sleep(1.5)  # 等待其运行并且跨越 1.2 秒的载波退避窗口
    print(f"DEBUG: active_task.done() = {active_task.done()}")
    print(f"DEBUG: sent_messages = {sent_messages}")
    if active_task.done() and active_task.exception():
        raise active_task.exception()
    
    # 验证是否成功挂起
    assert session_key in dispatcher._pending_perms, f"事件应当已被挂起且处于等待审批状态. Sent messages: {sent_messages}"
    
    # 3. 模拟亮哥发送了「允许」放行指令
    approve_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "y",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    # 触发允许事件，释放挂起
    await dispatcher.dispatch_event(approve_event)
    await asyncio.sleep(0.5)
    
    # 4. 验证整个审批链条
    assert session_key not in dispatcher._pending_perms, "允许指令被响应后，审批应该被移出队列"
    assert any("Result is approved: True" in m for m in sent_messages), "大模型应能接收到 approved 为 True 的状态并继续运行"
    print("🎉 Pytest Admin Permission Request and Physical Release flow passed!")

