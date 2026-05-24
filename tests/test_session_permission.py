import asyncio
import pytest
import os
from agent.core.gateway import QQGateway
from tests.test_admin_private_recovery import MockAgent

@pytest.mark.asyncio
async def test_session_level_one_time_approval(monkeypatch):
    """测试管理员敏感操作会话级单次审批机制：一次会话只需同意一次，后续自动放行，跨会话自动重置"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")

    class MockDoublePermissionAgent(MockAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.approved = False

        def approve_permission(self):
            self.approved = True

        def deny_permission(self):
            self.approved = False

        async def run(self, prompt, stream=True, **kwargs):
            # 1. 触发第一个敏感工具请求
            yield {
                "type": "permission_request",
                "category": "write",
                "tool_name": "edit_file_1",
                "message": "写入核心配置1"
            }
            # 保存此时的授权状态
            state_1 = self.approved
            
            # 2. 触发第二个敏感工具请求 (如果单次审批生效，此操作应自动放行)
            yield {
                "type": "permission_request",
                "category": "write",
                "tool_name": "edit_file_2",
                "message": "写入核心配置2"
            }
            state_2 = self.approved

            yield {"type": "text_delta", "content": f"State1: {state_1} | State2: {state_2}"}
            yield {"type": "_done"}

    # 实例化网关
    gw = QQGateway(agent_factory=MockDoublePermissionAgent)
    dispatcher = gw.dispatcher

    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, *args, **kwargs):
        sent_messages.append(text)

    gw._send = mock_send

    # ──── 步骤 1. 发起第一轮会话，触发两次权限请求 ────
    session_key = "user_1705919142"
    first_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "开启敏感任务",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }

    await dispatcher.dispatch_event(first_event)
    await asyncio.sleep(1.5)  # 跨越载波避碰

    # 验证第一个工具被挂起拦截
    assert session_key in dispatcher._pending_perms, "第一个敏感工具应当被卡住等待授权"
    
    # 模拟亮哥回复 y 允许第一个工具
    approve_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "y",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(approve_event)
    await asyncio.sleep(0.5)

    # ──── 步骤 2. 验证智能放行成效 ────
    # 审批队列应该空了 (因为第二个工具被自动信任直接放行，不再挂起！)
    assert session_key not in dispatcher._pending_perms, "第二个工具应当自动免检通过，队列不应挂起"
    
    # 验证最终大模型收到的状态：第一个在唤醒后为 True，第二个自动放行也是 True！
    print(f"DEBUG SENT: {sent_messages}")
    assert any("State1: True | State2: True" in m for m in sent_messages), "两个敏感操作应当最终都成功获取到了授权"

    # ──── 步骤 3. 验证跨会话物理隔离与安全重置（模拟 30 分钟绿灯超时） ────
    sent_messages.clear()
    
    # 模拟 30 分钟信任绿灯已超时过期
    dispatcher.executor.session_approved_until = 0.0
    
    second_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "第二轮全新任务",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }

    await dispatcher.dispatch_event(second_event)
    await asyncio.sleep(1.5)

    # 跨对话（模拟超时）后信任被彻底销毁，第一个敏感工具必须再次触发卡死拦截！
    assert session_key in dispatcher._pending_perms, "绿灯超时后信任应当被安全销毁，首个工具必须再次拦截！"
    
    print("🎉 Pytest Session-level one-time approval & boundary reset test passed successfully!")

