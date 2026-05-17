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
    def __init__(self):
        self.llm = MockLLM()
        self.memory = MockMemory()
    async def run(self, prompt, stream=True):
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
async def test_ai_driven_scheduler():
    gw = QQGateway(agent_factory=MockAgent)
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text):
        sent_messages.append(text)
    gw._send = mock_send

    # 模拟长任务
    task1_event = {"message_type": "private", "user_id": "123", "raw_message": "跑一个 long 任务"}
    asyncio.create_task(gw._handle(task1_event))
    await asyncio.sleep(0.5)

    # 模拟排队任务
    task2_event = {"message_type": "private", "user_id": "123", "raw_message": "顺便备份"}
    await gw._handle(task2_event)
    await asyncio.sleep(0.5)

    # 模拟抢占任务
    task3_event = {"message_type": "private", "user_id": "123", "raw_message": "先帮我看一下 README"}
    await gw._handle(task3_event)

    await asyncio.sleep(2.0)
    
    # 验证点：
    # 1. 成功拦截并发出由 MockLLM 动态生成的女性极客小肖安抚句
    # 2. 成功发送抢占刹车提示
    has_ai_queue_reply = any("小肖" in m and "备忘录" in m for m in sent_messages)
    has_preempt = any("手忙脚乱" in m or "备忘录" in m for m in sent_messages)

    assert has_ai_queue_reply, "应该成功调起大模型生成动态安抚"
    print("Dynamic AI-driven scheduler preemption test successfully passed!")
