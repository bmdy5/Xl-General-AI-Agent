import asyncio
import pytest
from agent.gateway import QQGateway

class MockLLM:
    async def chat(self, messages, tools=None, model_override=""):
        # 模拟分类器、小肖秒回及自整定手册
        system_content = messages[0]["content"]
        if "抢占式打断" in system_content:
            # 模拟判断亮哥是否发出强占打断
            for msg in messages:
                if "README" in msg.get("content", ""):
                    return {"content": "True"}
            return {"content": "False"}
        elif "安抚" in system_content:
            return {"content": "好啦亮哥，小肖记在备忘录里啦，等我干完马上来！"}
        elif "自相矛盾" in system_content:
            return {"content": '{\n  "name": "小肖",\n  "gender": "女",\n  "user_address": "亮哥",\n  "tone_style": "俏皮且懂事的女生程序员语气",\n  "preferences": ["绝对忠诚"],\n  "avoid_list": ["死板套话"]\n}'}
        return {"content": "Mock completion"}

class MockMemory:
    def __init__(self):
        import tempfile
        from pathlib import Path
        self.base_dir = Path(tempfile.mkdtemp())
    def search_memories(self, query, limit=5):
        return [{"content": "语气要温柔俏皮"}]

class MockAgent:
    def __init__(self, session_key=None, *args, **kwargs):
        self.llm = MockLLM()
        self.memory = MockMemory()
        self.messages = []
        self.session = None
    async def run(self, prompt, stream=True, **kwargs):
        if "long" in prompt:
            yield {"type": "text_delta", "content": "长任务第一阶段"}
            try:
                await asyncio.sleep(5.0)
                yield {"type": "text_delta", "content": "长任务第二阶段"}
            except asyncio.CancelledError:
                # 捕获取消，模拟安全退出
                raise
        else:
            yield {"type": "text_delta", "content": f"普通回复: {prompt}"}

@pytest.mark.asyncio
async def test_ai_driven_scheduler(monkeypatch):
    monkeypatch.setenv("QQ_ADMIN_ID", "123")
    monkeypatch.setenv("ADMIN_ID", "123")
    gw = QQGateway(agent_factory=MockAgent)
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, *args, **kwargs):
        sent_messages.append(text)
    gw._send = mock_send

    # 1. 模拟启动长任务
    task1_event = {"message_type": "private", "user_id": "123", "raw_message": "跑一个 long 任务"}
    asyncio.create_task(gw._handle(task1_event))
    await asyncio.sleep(0.5)

    # 2. 模拟排队任务 (在当前排队机制下，新来的并发消息会暂存入队列排队，防止吞消息)
    task2_event = {"message_type": "private", "user_id": "123", "raw_message": "顺便备份"}
    await gw._handle(task2_event)
    await asyncio.sleep(0.5)
    
    # 校验：消息已被成功排队
    queue = gw._message_queues.get("user_123")
    assert queue is not None
    assert any(raw == "顺便备份" for event, raw in queue), "排队模式下消息应被成功加入暂存队列"
    print("✅ Queue mode verification: '顺便备份' queued successfully")

    # 3. 模拟强行抢占任务 (发送包含抢占关键词 '停下' 的指令)
    task3_event = {"message_type": "private", "user_id": "123", "raw_message": "停下，先帮我看一下 README"}
    await gw._handle(task3_event)

    await asyncio.sleep(2.0)
    
    # 验证点：
    # 1. 长任务 task1 被强行 Cancel，因此不应输出 '长任务第二阶段'
    # 2. 抢占指令触发了拦截，生成系统调度通知并启动了抢占命令的执行
    has_preempt_triggered = any("停下上一个任务" in m for m in sent_messages)
    task1_cancelled_successfully = not any("长任务第二阶段" in m for m in sent_messages)

    assert has_preempt_triggered, "应当成功触发强占中断并启动新命令"
    assert task1_cancelled_successfully, "长任务应当被中止，不生成第二阶段的输出"
    print("🎉 Dynamic keyword-driven scheduler preemption test successfully passed!")
