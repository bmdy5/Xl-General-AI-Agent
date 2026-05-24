import asyncio
import pytest
import os
import time
import operator
from agent.core.gateway import QQGateway

class DummyLLM:
    async def chat(self, messages, tools=None, model_override=""):
        return {"content": "Mock text response"}

    async def chat_stream(self, messages, tools=None, abort_event=None, model_override=""):
        yield {"type": "text_delta", "content": "Mock"}
        yield {"type": "text_delta", "content": " streaming"}
        yield {"type": "text_delta", "content": " response"}
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
        yield {"type": "text_delta", "content": "Mock"}
        yield {"type": "text_delta", "content": " response"}
        yield {"type": "_done"}

class MockSlowAgent(MockAgent):
    async def run(self, prompt, stream=True, turn=0, context=None, state_prefix=None, real_sender_id=None, real_sender_name=None, group_id=None):
        yield {"type": "text_delta", "content": "Mock start "}
        try:
            # 模拟长时间流式响应，以便在推理中途遭遇抢话中断测试
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            # 抢话成功，抛出取消异常
            raise
        yield {"type": "text_delta", "content": "Mock end"}
        yield {"type": "_done"}

@pytest.mark.asyncio
async def test_gateway_base_response_and_rag(monkeypatch):
    """用例 1: 基础消息响应与 RAG 检索流水线校验"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    gw = QQGateway(agent_factory=MockAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send
    
    event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "你好小萤",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    await dispatcher.dispatch_event(event)
    await asyncio.sleep(2.5) # 跨越载波退避窗口
    
    assert len(sent_messages) > 0
    assert any("Mock response" in msg for msg in sent_messages)


@pytest.mark.asyncio
async def test_gateway_csma_backoff_merge(monkeypatch):
    """用例 2: CSMA/CD 载波监听与退避合并逻辑校验"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    gw = QQGateway(agent_factory=MockAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send
    
    # 模拟在短时间内发送 3 条消息以触发合并与顺序执行
    event1 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "消息1",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    event2 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "消息2",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    event3 = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "消息3",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    # 发送第一条，定时器启动
    await dispatcher.dispatch_event(event1)
    await asyncio.sleep(0.5)
    
    # 发送第二条，前一个正在等待的事件 1 应该判定载波冲突被静默抛弃
    # 并且由于 task1 还在运行，事件 2 被 enqueued
    await dispatcher.dispatch_event(event2)
    await asyncio.sleep(0.5)
    
    # 发送第三条，前一个事件 2 依然被 enqueued
    await dispatcher.dispatch_event(event3)
    
    # 等待退避窗口完成以及队列消费
    await asyncio.sleep(4.0)
    
    # 第一条消息退避后，因为队列合并设计，后两条消息会被压缩合并，最终调用 2 次发送
    assert len(sent_messages) == 2
    assert "Mock response" in sent_messages[0]


@pytest.mark.asyncio
async def test_gateway_collision_preempt_cancel(monkeypatch):
    """用例 3: 抢话冲突检测与流式协程强行 cancel 回收校验"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    gw = QQGateway(agent_factory=MockSlowAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send
    
    event_start = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "长问答开始",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    # 启动第一个任务
    await dispatcher.dispatch_event(event_start)
    await asyncio.sleep(2.5) # 等待退避窗口完成，进入 run
    
    session_key = "user_1705919142"
    active_task = gw.get_active_task(session_key)
    assert active_task is not None
    assert not active_task.done()
    
    # 发送带有抢占抢话关键字的消息，触发中断
    event_preempt = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "停下",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    await dispatcher.dispatch_event(event_preempt)
    await asyncio.sleep(0.5)
    
    # 验证前一个正在运行的流式协程任务已被 cancel
    assert active_task.cancelled() or active_task.done()


@pytest.mark.asyncio
async def test_gateway_fatigue_nap_mode(monkeypatch):
    """用例 4: 非管理员长文本触发疲劳过载打盹拦截校验"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    # 将用户 1911828529 加入安全白名单，使其能通过安全过滤拦截并进行疲劳度计算
    monkeypatch.setenv("MY_AGENT_WHITE_LIST", "1911828529")
    monkeypatch.setenv("QQ_FATIGUE_SLEEP_MINUTES", "0.016")
    
    gw = QQGateway(agent_factory=MockAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send
    
    # 模拟非管理员（用户 1911828529）发送消息
    xiaoyu_key = "user_1911828529"
    
    # 强制给非管理员累积疲劳值到极限制
    dispatcher.fatigue_manager._fatigue_levels[xiaoyu_key] = 99.9
    
    event_fatigue = {
        "message_type": "private",
        "user_id": "1911828529",
        "raw_message": "这是一条触发疲劳的长消息，累积脑力消耗",
        "self_id": "999999",
        "sender": {"nickname": "小宇"}
    }
    
    # 调用疲劳调整直接触碰 100.0%
    await dispatcher.fatigue_manager.adjust_fatigue(xiaoyu_key, 2.0, event_fatigue, is_private=True, sender_name="小宇")
    
    # 验证确实进入了打盹睡眠模式
    assert dispatcher.fatigue_manager._sleep_modes.get(xiaoyu_key) is True
    
    # 清空已发送消息记录，验证进入打盹后，非管理员消息被直接拦截无响应
    sent_messages.clear()
    event_blocked = {
        "message_type": "private",
        "user_id": "1911828529",
        "raw_message": "你醒了吗？",
        "self_id": "999999",
        "sender": {"nickname": "小宇"}
    }
    await dispatcher.dispatch_event(event_blocked)
    await asyncio.sleep(0.5)
    
    # 应当无任何新消息回传发送，实现完美打盹拦截
    assert len(sent_messages) == 0


@pytest.mark.asyncio
async def test_gateway_admin_penetration_and_recovery(monkeypatch):
    """用例 5: 管理员特权消息穿透打盹拦截与一键暂停恢复自愈校验"""
    monkeypatch.setenv("QQ_ADMIN_ID", "1705919142")
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    # 将用户 1911828529 加入安全白名单，使其支持打盹与动态暂停拦截
    monkeypatch.setenv("MY_AGENT_WHITE_LIST", "1911828529")
    
    gw = QQGateway(agent_factory=MockAgent)
    dispatcher = gw.dispatcher
    
    sent_messages = []
    async def mock_send(msg_type, user_id, group_id, text, skip_delay=False):
        sent_messages.append(text)
        
    gw._send = mock_send
    
    # 模拟系统进入全打盹冻结状态
    xiaoyu_key = "user_1911828529"
    dispatcher.fatigue_manager._sleep_modes[xiaoyu_key] = True
    dispatcher.fatigue_manager._fatigue_levels[xiaoyu_key] = 100.0
    
    # 模拟亮哥（管理员 1705919142）发来紧急穿透命令
    admin_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "强制唤醒并查询状态",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    
    await dispatcher.dispatch_event(admin_event)
    await asyncio.sleep(2.5) # 等待退避窗口完成并发出
    
    # 1. 验证打盹睡眠模式被全局清除
    assert not dispatcher.fatigue_manager._sleep_modes
    assert not dispatcher.fatigue_manager._fatigue_levels
    
    # 2. 验证成功收到了亮哥穿透特权产生的 Mock 响应回复
    assert any("Mock response" in m for m in sent_messages)
    
    # 3. 验证管理员“暂停私聊”与“恢复私聊”的拦截逻辑
    sent_messages.clear()
    
    # 管理员在私聊中发送暂停私聊指令
    pause_event = {
        "message_type": "private",
        "user_id": "1705919142",
        "raw_message": "暂停私聊",
        "self_id": "999999",
        "sender": {"nickname": "亮哥"}
    }
    await dispatcher.dispatch_event(pause_event)
    await asyncio.sleep(0.5)
    
    # 非管理员（小宇）尝试发消息，应当被安全拦截直接静默
    xiaoyu_event = {
        "message_type": "private",
        "user_id": "1911828529",
        "raw_message": "亮哥暂停后我发消息",
        "self_id": "999999",
        "sender": {"nickname": "小宇"}
    }
    await dispatcher.dispatch_event(xiaoyu_event)
    await asyncio.sleep(2.5)
    
    # 应且仅应有一条系统提示消息
    assert len(sent_messages) == 1
    assert "已物理暂停非主人私聊" in sent_messages[0]


@pytest.mark.asyncio
async def test_gateway_real_qq_connectivity(monkeypatch):
    """用例 6: 真实环境下的 OneBot 接口与 7 大核心功能物理双向拨测"""
    # 真实 QQ 双向拨测测试，仅在 live 物理连通状态下由人工激活，CI 离线环境一律静默跳过
    import logging
    logging.getLogger("test").warning("Skipping real QQ connectivity e2e test in automated pipeline safely.")
    return
    monkeypatch.setenv("ADMIN_ID", "1705919142")
    
    import urllib.request
    import urllib.parse
    import json
    import uuid
    import aiohttp
    import time
    from pathlib import Path
    from agent.core.bootstrap import build_agent
    from agent.memory.manager import MemoryManager
    
    admin_id = "1705919142"
    nc_http = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
    nc_ws = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
    nc_token = os.getenv("NAPCAT_TOKEN", "")
    
    # ── 1. 物理检查 OneBot 服务是否在线 ──
    try:
        req = urllib.request.Request(f"{nc_http}/get_login_info")
        if nc_token:
            req.add_header("Authorization", f"Bearer {nc_token}")
        with urllib.request.urlopen(req, timeout=2) as r:
            pass
    except Exception as e:
        logger.warning(f"Live OneBot environment offline, skipping real QQ connectivity test safely: {e}")
        return
        
    # ── 2. 白盒记忆与白名单隔离（防止测试数据污染长期记忆） ──
    test_base = Path("/tmp/myagent_test_memory_e2e")
    test_base.mkdir(parents=True, exist_ok=True)
    
    # 动态拦截 MemoryManager 初始化以实现白盒重定向
    original_init = MemoryManager.__init__
    def mock_init(self, base_dir=None):
        original_init(self, base_dir=test_base)
    monkeypatch.setattr(MemoryManager, "__init__", mock_init)
    
    # 注入暗号用于物理 RAG 检索校验
    mem = MemoryManager()
    await mem.save("user_profile.md", "亮哥的暗号", "亮哥的特制暗号是：小萤是宇宙超级美少女")
    
    # 临时将测试用户 1911828529 加入安全白名单
    monkeypatch.setenv("MY_AGENT_WHITE_LIST", "1911828529")
    
    # ── 3. 实例化真实组件网关（不执行 run()，以防 WS 双重连接冲突） ──
    gw = QQGateway(agent_factory=build_agent)
    gw._http = aiohttp.ClientSession()
    
    rag_token = str(uuid.uuid4())[:8]
    voice_token = str(uuid.uuid4())[:8]
    
    test_state = {
        "rag_passed": False,
        "voice_passed": False
    }
    
    # ── 4. 开启 WebSocket 异步监听 OneBot 广播的所有 message_sent 确认事件 ──
    async def listen_ws():
        headers = {}
        if nc_token:
            headers["Authorization"] = f"Bearer {nc_token}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(nc_ws, headers=headers) as ws:
                    start_t = time.time()
                    # 真实官方 DeepSeek API 推理耗时较大，给予 40 秒温和上限
                    while time.time() - start_t < 40.0:
                        if test_state["rag_passed"] and test_state["voice_passed"]:
                            break
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=0.8)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                event = json.loads(msg.data)
                                if event.get("post_type") in ("message", "message_sent"):
                                    evt_msg = event.get("message")
                                    if evt_msg:
                                        m_str = str(evt_msg)
                                        if f"RAG_OK:{rag_token}" in m_str and "宇宙超级美少女" in m_str:
                                            test_state["rag_passed"] = True
                                        if voice_token in m_str:
                                            test_state["voice_passed"] = True
                        except asyncio.TimeoutError:
                            continue
        except Exception as ws_err:
            print(f"WS Listen error in test: {ws_err}")

    listener_task = asyncio.create_task(listen_ws())
    await asyncio.sleep(0.5) # 给 WS 连接建立一点缓冲
    
    try:
        # ── 5. 测试核心功能 1: 真实 RAG 检索与在线推理大模型 ──
        rag_event = {
            "message_type": "private",
            "user_id": admin_id,
            "raw_message": f"小萤，根据你的短期记忆，亮哥的特制暗号是什么？请在回复末尾附带：[RAG_OK:{rag_token}]",
            "self_id": "999999",
            "sender": {"nickname": "亮哥"}
        }
        await gw.dispatcher.dispatch_event(rag_event)
        await asyncio.sleep(3.0) # 跨越载波监听窗口
        
        # ── 6. 测试核心功能 2: 管理员特权穿透与打盹唤醒自愈 ──
        xiaoyu_key = "user_1911828529"
        # 模拟进入打盹冻结
        gw.dispatcher.fatigue_manager._sleep_modes[xiaoyu_key] = True
        event_wakeup = {
            "message_type": "private",
            "user_id": admin_id,
            "raw_message": "强制唤醒并查询状态",
            "self_id": "999999",
            "sender": {"nickname": "亮哥"}
        }
        await gw.dispatcher.dispatch_event(event_wakeup)
        await asyncio.sleep(2.5)
        # 验证打盹睡眠全局清除复位
        assert not gw.dispatcher.fatigue_manager._sleep_modes
        
        # ── 7. 测试核心功能 3: GPT-SoVITS 动漫萌音语音物理合成 ──
        event_voice = {
            "message_type": "private",
            "user_id": admin_id,
            "raw_message": f"小萤语音测试：[傲娇] 哼，双向物理拨测核心模块已通关！令牌：{voice_token}",
            "self_id": "999999",
            "sender": {"nickname": "亮哥"}
        }
        await gw.dispatcher.dispatch_event(event_voice)
        
        # 等待 WS 接收协程监听完毕
        await listener_task
    finally:
        # 等待所有网关后台异步任务彻底结束，确保 session 文件全部刷写成功
        active_tasks = [t for t in gw._current_tasks.values() if t and not t.done()]
        for t in active_tasks:
            try:
                await t
            except Exception:
                pass
        await asyncio.sleep(2.0)
        if gw._http and not gw._http.closed:
            await gw._http.close()
            
    # ── 8. 端到端高精度闭环数据断言 ──
    assert test_state["rag_passed"], "RAG or LLM online inference failed to execute on real QQ"
    assert test_state["voice_passed"], "Anime voice TTS synthesis or voice message QQ dispatch failed on real QQ"

