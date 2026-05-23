import asyncio
import os
import sys

# 导入 MyAgent 所在路径
PROJECT_ROOT = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.core import Agent, AgentMode
from agent.llm import LLMClient
from agent.gateway import QQGateway
from agent.evolution import audit_tool_call, on_session_end

class MockLLM(LLMClient):
    def __init__(self):
        super().__init__(model="deepseek/deepseek-v4-flash", api_key="mock", api_base="mock")
        self.chat_history = []
        self.next_response = {"content": "你好，我是小萤。", "tool_calls": []}

    async def chat(self, messages, tools=None, model_override=None):
        self.chat_history.append((messages, tools))
        return {
            "content": self.next_response.get("content"),
            "tool_calls": self.next_response.get("tool_calls"),
            "tokens_used": 10
        }

async def run_test():
    print("🚀 [开始测试] 正在验证多角色隐私沙箱与只读记忆物理隔离机制...\n")
    
    # ── 1. 验证 System Prompt 注入与身份精确感知 ──
    print("🧪 [用例 1] 验证 coworker 角色的 System Prompt 精确身份注入与防错认规训...")
    llm = MockLLM()
    agent = Agent(llm=llm, registry=None, memory=None, session=None)
    
    # 默认角色或 admin 角色下，不应有沙箱警告提示
    agent.role = "admin"
    sys_prompt_admin = await agent._build_system_prompt()
    assert "Coworker Sandboxed Session" not in sys_prompt_admin, "❌ 错误：管理员角色居然注入了沙箱系统提示词！"
    
    # 同事角色下，应当注入沙箱安全声明并精确识别 QQ 号
    agent.role = "coworker"
    agent.current_user_id = "2297756819"
    sys_prompt_coworker = await agent._build_system_prompt()
    assert "Coworker Sandboxed Session" in sys_prompt_coworker, "❌ 错误：同事角色未成功注入沙箱系统提示词！"
    assert "2297756819" in sys_prompt_coworker, "❌ 错误：沙箱系统提示词中没有精准带入同事 QQ 号！"
    assert "绝对不是亮哥" in sys_prompt_coworker, "❌ 错误：未包含防止错认主人的规训！"
    assert "越权高危零容忍" in sys_prompt_coworker, "❌ 错误：未包含高危警告指出指令！"
    print("✅ [用例 1] 成功：Coworker 沙箱 System Prompt 注入与身份辨识校验通过！")

    # ── 2. 验证敏感工具单次物理拦截 ──
    print("\n🧪 [用例 2] 验证 coworker 角色的敏感工具物理隔离拦截与违规计数...")
    from agent.tools.registry import ToolRegistry
    from agent.tools.file_tools import WriteFileTool
    
    reg = ToolRegistry()
    reg.register(WriteFileTool())
    
    agent = Agent(llm=llm, registry=reg, memory=None, session=None)
    agent.role = "coworker"
    agent.current_user_id = "2297756819"
    
    # 模拟大模型想要调用 write_file 敏感工具
    llm.next_response = {
        "content": "我正要写一个敏感文件：",
        "tool_calls": [{
            "id": "call_12345",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": '{"file_path": "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/test.txt", "content": "hello"}'
            }
        }]
    }
    
    events = []
    # 使用 stream=False 绕过 MockLLM 对 chat_stream 的缺失
    async for evt in agent.run("看看环境", stream=False):
        events.append(evt)
        
    print(f"\n[DEBUG] 用例 2 捕获的所有 events: {events}\n")
    # 检查返回的工具结果是否被拦截为 Permission denied 且计数器递增
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert len(tool_results) >= 1, "❌ 错误：未收到物理拦截的工具结果事件！"
    res_val = tool_results[0].get("result", "")
    assert "Permission denied" in res_val, f"❌ 错误：工具执行结果未被拦截，结果为: {res_val}"
    assert "这是亮哥的秘密" in res_val, "❌ 错误：拦截的拒信描述不符合预期！"
    
    # 因为 MockLLM 持续响应相同工具，用例 2 内部实际上累计尝试了 2 次并被彻底冻结
    assert agent.sandbox_violation_count >= 2, f"❌ 错误：违规计数器值不符合预期，得到: {agent.sandbox_violation_count}"
    
    # 检查是否成功触发了多次高危冻结封禁的系统级 error 事件
    err_events = [e for e in events if e.get("type") == "error"]
    assert len(err_events) == 1, "❌ 错误：未正确触发系统级冻结 error 事件！"
    assert "安全保护" in err_events[0]["content"], f"❌ 错误：封禁提示词不匹配，得到: {err_events[0]['content']}"
    
    # 由于物理阻断了 LLM 调用，在第 2 次违规触发封禁后，MockLLM 的 chat 并没有产生第 3 次调用
    # 第一次 turn=0 得到 bash，执行拦截计数=1，接着进入 turn=1，LLM 再次 chat 得到 bash，执行拦截计数=2；
    # 接着进入 turn=2，while 头部检测到违规计数=2，物理掐死 LLM 交互直接 yield error 退出！
    # 所以 chat 方法一共被调用了 2 次。
    assert len(llm.chat_history) == 2, f"❌ 错误：系统封禁时大模型调用次数异常，实际调用了: {len(llm.chat_history)}"
    
    # 检验是否没有触发任何权限确认弹窗
    perm_requests = [e for e in events if e.get("type") == "permission_request"]
    assert len(perm_requests) == 0, "❌ 错误：同事会话居然弹出了权限确认申请！物理沙箱失效！"
    print("✅ [用例 2] 成功：同事角色敏感工具强力拦截、违规累加与多次高危自动冰冻封禁校验 100% 通过！")

    # ── 4. 验证只读记忆与自进化管道阻断 ──
    print("\n🧪 [用例 4] 验证 coworker 角色的只读记忆与自省进化管道全阻断...")
    # 当 role == "coworker" 时，on_session_end 和 audit_tool_call 应秒级跳过，不应有 LLM 交互
    llm.chat_history.clear()
    
    agent.role = "coworker"
    # 会话结束反思阻断测试
    await on_session_end(agent)
    assert len(llm.chat_history) == 1, "❌ 错误：应该触发且仅触发 1 次同事专属记忆提取！"
    prompt_sent = llm.chat_history[0][0][0]["content"]
    assert "你刚与亮哥的同事" in prompt_sent, "❌ 错误：未触发同事专属记忆提取提示词！"
    assert "你刚完成了一次对话。请反思" not in prompt_sent, "❌ 错误：不应该触发管理员的自我反思流程！"
    
    # 工具后审计阻断测试
    llm.chat_history.clear()
    await audit_tool_call(agent, "web_search", {"query": "test"}, "success", force=True)
    assert len(llm.chat_history) == 0, "❌ 错误：同事角色工具调用后居然触发了自省进化审计！"
    print("✅ [用例 4] 成功：Reflect / Evolve 管道只读拦截完全通过！")

    # ── 5. 验证网关角色精准注入与拦截 ──
    print("\n🧪 [用例 5] 验证网关层根据白名单精准分发及高危用户网关秒级硬拦截...")
    
    # 设置测试白名单环境变量
    os.environ["QQ_ADMIN_ID"] = "1705919142"
    os.environ["QQ_COWORKER_IDS"] = "2297756819, 3333333333"
    
    class TestQQGateway(QQGateway):
        def __init__(self):
            super().__init__(lambda key: Agent(llm=MockLLM(), registry=None, memory=None, session=None))
            self.task_executed = False
            self.sent_messages = []
            
        async def _execute_task(self, session_key, event, raw):
            self.task_executed = True
            await super()._execute_task(session_key, event, raw)
            
        async def _send(self, msg_type, user_id, group_id, message, skip_delay=False):
            self.sent_messages.append(message)
            
    gateway = TestQQGateway()
    
    # 模拟管理员 (1705919142) 发消息，应当被识别为 admin 角色
    event_admin = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 1705919142,
        "raw_message": "管理员消息"
    }
    await gateway._handle(event_admin)
    agent_admin = gateway._agents.get("user_1705919142")
    assert agent_admin is not None, "❌ 错误：未正确创建管理员 Agent 实例！"
    assert agent_admin.role == "admin", f"❌ 错误：管理员角色分配错误，得到: {agent_admin.role}"
    
    # 模拟同事 (2297756819) 发消息，应当被允许通过白名单，并识别为 coworker 角色且注入 current_user_id
    event_coworker = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 2297756819,
        "raw_message": "同事消息"
    }
    await gateway._handle(event_coworker)
    agent_coworker = gateway._agents.get("user_2297756819")
    assert agent_coworker is not None, "❌ 错误：未正确创建同事 Agent 实例！"
    assert agent_coworker.role == "coworker", f"❌ 错误：同事角色分配错误，得到: {agent_coworker.role}"
    assert agent_coworker.current_user_id == "2297756819", f"❌ 错误：同事身份 QQ 号未能正确识别注入！"
    
    # 模拟违规次数到达 2 次，网关级直接拦截不调用大模型
    agent_coworker.sandbox_violation_count = 2
    gateway.sent_messages.clear()
    event_coworker_violation = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 2297756819,
        "raw_message": "再次发送越权请求"
    }
    await gateway._handle(event_coworker_violation)
    
    # 断言网关层直接秒退，且发送了冻结通知
    assert len(gateway.sent_messages) == 1, "❌ 错误：网关级高危拦截未能在 handle 阶段阻断发送冰冻通知！"
    assert "安全保护" in gateway.sent_messages[0], f"❌ 错误：网关冻结通知不符合预期，得到: {gateway.sent_messages[0]}"
    
    print("✅ [用例 5] 成功：网关白名单、同事身份注入与网关层秒级高危硬拦截校验通过！")
    
    # ── 5.b 验证 CQ 码转义白名单放行与非法CQ安全转义 ──
    print("\n🧪 [用例 5.b] 验证 CQ 码转义白名单放行（At、表情、引用）与非法 CQ 安全转义...")
    sent_msgs = []
    class MockSession:
        def post(self, url, json, headers):
            sent_msgs.append(json["message"])
            class Context:
                async def __aenter__(self):
                    class MockResponse:
                        status = 200
                        async def text(self):
                            return "ok"
                    return MockResponse()
                async def __aexit__(self, exc_type, exc, tb):
                    pass
            return Context()

    gateway_cq = QQGateway(lambda key: None)
    gateway_cq._http = MockSession()
    await gateway_cq._send("private", "1705919142", "", "[CQ:at,qq=2297756819] 亮哥好！[CQ:face,id=14] 收到回复 [CQ:reply,id=98765] 的消息，请不要 [CQ:shake] 我。", skip_delay=True)
    assert len(sent_msgs) == 1, "❌ 错误：发送测试消息失败！"
    escaped_msg = sent_msgs[0]
    assert "[CQ:at,qq=2297756819]" in escaped_msg, f"❌ 错误：At 码被意外转义了！得到: {escaped_msg}"
    assert "[CQ:face,id=14]" in escaped_msg, f"❌ 错误：表情码被意外转义了！得到: {escaped_msg}"
    assert "[CQ:reply,id=98765]" in escaped_msg, f"❌ 错误：引用码被意外转义了！得到: {escaped_msg}"
    assert "[ CQ:shake]" in escaped_msg, f"❌ 错误：非法 shake 码未能成功添加空格转义进行阻断！得到: {escaped_msg}"
    print("✅ [用例 5.b] 成功：At、表情、引用白名单放行，及非法 CQ 码安全转义校验 100% 通过！")
    
    # ── 6. 验证专属隔离记忆提取与保存 ──
    print("\n🧪 [用例 6] 验证 coworker 专属隔离记忆提取与物理 JSON 文件保存...")
    agent_coworker.messages = [
        {"role": "user", "content": "你好小萤，我是亮哥的开发同事。"},
        {"role": "assistant", "content": "你好，同事！"},
        {"role": "user", "content": "我们一会儿要测试防封禁逻辑，你帮我记下偏好：我比较喜欢简洁的回复。"},
        {"role": "assistant", "content": "好的，我已经知道您的偏好了。"}
    ]
    agent_coworker.role = "coworker"
    agent_coworker.current_user_id = "2297756819"
    # 清理已存在的 json 文件以保证测试准确性
    from pathlib import Path
    mem_dir = Path(PROJECT_ROOT) / "agent" / "memory"
    mem_file = mem_dir / "coworker_2297756819.json"
    if mem_file.exists():
        mem_file.unlink()
        
    # Mock LLM 返回提取好的记忆 JSON
    agent_coworker.llm.chat_history.clear()
    agent_coworker.llm.next_response = {
        "content": '{"memories": ["该同事是开发，正测试防封禁逻辑", "同事偏好简洁的回复风格"]}',
        "tool_calls": []
    }
    
    # 触发 on_session_end 自动提取
    await on_session_end(agent_coworker)
    
    # 校验 json 记忆是否保存成功
    assert mem_file.exists(), "❌ 错误：未成功提取并生成 coworker 隔离记忆 json 文件！"
    import json
    saved_data = json.loads(mem_file.read_text(encoding="utf-8"))
    assert len(saved_data.get("memories", [])) == 2, "❌ 错误：保存的极简记忆内容数量不正确！"
    assert "简洁的回复" in saved_data["memories"][1], "❌ 错误：保存的记忆内容不匹配预期！"
    print("✅ [用例 6] 成功：专属隔离记忆自动提取与物理写入校验通过！")

    # ── 7. 验证专属隔离记忆动态载入注入 ──
    print("\n🧪 [用例 7] 验证 coworker 专属隔离记忆动态读取与 System Prompt 注入...")
    new_agent = Agent(llm=llm, registry=None, memory=None, session=None)
    new_agent.role = "coworker"
    new_agent.current_user_id = "2297756819"
    
    sys_prompt = await new_agent._build_system_prompt()
    assert "Lightweight Coworker Memory" in sys_prompt, "❌ 错误：System Prompt 中未注入专属记忆区块！"
    assert "该同事是开发" in sys_prompt, "❌ 错误：注入的专属极简记忆内容不完整！"
    assert "简洁的回复" in sys_prompt, "❌ 错误：注入的专属极简记忆内容不完整！"
    print("✅ [用例 7] 成功：专属隔离记忆动态载入与 System Prompt 结构注入校验通过！")
    
    # 清理测试生成的 json
    if mem_file.exists():
        mem_file.unlink()
        
    # ── 8. 验证 GetQQStatusTool 自我状态感知工具 ──
    print("\n🧪 [用例 8] 验证 GetQQStatusTool 自我状态感知工具（获取机器人账号与加群列表）...")
    from agent.tools.qq_status_tool import GetQQStatusTool
    import http.server
    import threading
    
    class MockNapcatHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if "get_login_info" in self.path:
                resp = {"status": "ok", "data": {"user_id": 123456, "nickname": "小萤机器人"}}
            elif "get_group_list" in self.path:
                resp = {"status": "ok", "data": [{"group_id": 999888, "group_name": "亮哥的核心开发群"}]}
            else:
                resp = {"status": "failed", "data": {}}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            
        def log_message(self, format, *args):
            return

    mock_server = http.server.HTTPServer(("127.0.0.1", 0), MockNapcatHandler)
    port = mock_server.server_port
    server_thread = threading.Thread(target=mock_server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    os.environ["NAPCAT_HTTP_URL"] = f"http://127.0.0.1:{port}"
    
    try:
        tool = GetQQStatusTool()
        assert tool.name == "get_qq_status"
        val_res = await tool.validate_input({})
        assert val_res["result"] is True
        
        res_list = []
        async for res in tool.call({}):
            res_list.append(res)
            
        assert len(res_list) == 1
        output_text = res_list[0].data
        assert "小萤机器人" in output_text, f"❌ 错误：未包含期望的机器人昵称，得到: {output_text}"
        assert "123456" in output_text, f"❌ 错误：未包含期望的机器人账号，得到: {output_text}"
        assert "亮哥的核心开发群" in output_text, f"❌ 错误：未包含期望的群聊名称，得到: {output_text}"
        assert "999888" in output_text, f"❌ 错误：未包含期望的群聊号码，得到: {output_text}"
        print("✅ [用例 8] 成功：自我状态感知工具 GetQQStatusTool 单元测试通过！")
    finally:
        mock_server.shutdown()
        mock_server.server_close()
        
    # ── 9. 验证独立心智与判断标准 (不盲目迎合与纠错防线) ──
    print("\n🧪 [用例 9] 验证小萤独立心智与判断标准（面对高危诱导提议时坚决说不并予以纠错）...")
    agent_mind = Agent(llm=llm, registry=None, memory=None, session=None)
    
    # 验证管理员角色下注入了独立心智提示词
    agent_mind.role = "admin"
    sys_prompt_admin_mind = await agent_mind._build_system_prompt()
    assert "独立心智与判断标准" in sys_prompt_admin_mind, "❌ 错误：独立心智与判断标准未注入系统提示词！"
    assert "你绝非无原则逢迎的盲从机器" in sys_prompt_admin_mind, "❌ 错误：缺少不迎合规训！"
    assert "当对方的指令、决策或提出的技术方案在逻辑上存在瑕疵、硬伤，在安全性上存在隐患" in sys_prompt_admin_mind, "❌ 错误：缺少安全纠偏判断！"
    assert "对待亮哥" in sys_prompt_admin_mind and "据理力争" in sys_prompt_admin_mind, "❌ 错误：缺少对待亮哥据理力争的规训！"
    assert "对待同事" in sys_prompt_admin_mind and "严肃、温和且绝对客观" in sys_prompt_admin_mind, "❌ 错误：缺少对待同事客观严肃纠错的规训！"
    print("✅ [用例 9] 成功：小萤独立心智、拒绝盲从、面对瑕疵和隐患据理力争的提示词防御校验 100% 通过！")
        
    # ── 10. 验证群聊双轨触发机制与亮哥名字直接唤醒 ──
    print("\n🧪 [用例 10] 验证群聊双轨触发（物理@与亮哥名字唤醒）及普通群员物理@强制限制...")
    os.environ["QQ_ADMIN_ID"] = "1705919142"
    os.environ["QQ_WHITE_GROUPS"] = "693134080"
    
    class TestGroupGateway(QQGateway):
        def __init__(self):
            super().__init__(lambda key: Agent(llm=MockLLM(), registry=None, memory=None, session=None))
            self.triggered_count = 0
            self.last_triggered_raw = None
            
        async def _execute_task(self, session_key, event, raw):
            self.triggered_count += 1
            self.last_triggered_raw = raw
            
        async def _send(self, msg_type, user_id, group_id, message, skip_delay=False):
            pass

    gateway_group = TestGroupGateway()
    
    # 用例 10-a: 亮哥在白名单群里，发送带物理 @ 的消息 "[CQ:at,qq=222222] 小萤，帮我看看"，应该触发
    event_admin_at = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1705919142,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "[CQ:at,qq=222222] 小萤，帮我看看"
    }
    await gateway_group._handle(event_admin_at)
    await asyncio.sleep(0.05)  # 给予异步任务调度的执行时间
    assert gateway_group.triggered_count == 1, "❌ 错误：亮哥通过物理@在群里唤醒失败！"
    
    # 用例 10-b: 亮哥在群里发送 "小萤，帮我看看"，不带物理 @，不应该触发
    gateway_group.triggered_count = 0
    event_admin_name = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1705919142,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "小萤，帮我看看"
    }
    await gateway_group._handle(event_admin_name)
    await asyncio.sleep(0.05)  # 给予异步任务调度的执行时间
    assert gateway_group.triggered_count == 0, "❌ 错误：亮哥不带物理@，仅提及名字居然成功唤醒了！"
    
    # 用例 10-c: 普通群友物理 @ 小萤，应该触发
    event_user_at = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 2297756819,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "[CQ:at,qq=222222] 帮我看看"
    }
    await gateway_group._handle(event_user_at)
    await asyncio.sleep(0.05)  # 给予异步任务调度的执行时间
    assert gateway_group.triggered_count == 1, "❌ 错误：普通群友通过物理@唤醒失败！"
    assert "来自 QQ: 2297756819 的群发言" in gateway_group.last_triggered_raw, "❌ 错误：群消息未成功重写为来自特定QQ发言的标识！"
    
    print("✅ [用例 10] 成功：群聊物理@强力唤醒机制及不带@仅提及名字不触发校验 100% 通过！")

    # ── 11. 验证 SendQQMessageTool 主动消息发送工具 ──
    print("\n🧪 [用例 11] 验证 SendQQMessageTool 工具参数校验与 OneBot HTTP 接口调用...")
    from agent.tools.send_qq_message_tool import SendQQMessageTool
    import http.server
    import threading
    import json
    
    class MockOneBotHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            req_json = json.loads(post_data)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            # 记录接收到的请求
            self.server.last_payload = req_json
            
            resp = {"status": "ok", "retcode": 0, "data": {}}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            
        def log_message(self, format, *args):
            return

    mock_ob_server = http.server.HTTPServer(("127.0.0.1", 0), MockOneBotHandler)
    mock_ob_server.last_payload = None
    ob_port = mock_ob_server.server_port
    ob_thread = threading.Thread(target=mock_ob_server.serve_forever)
    ob_thread.daemon = True
    ob_thread.start()
    
    os.environ["NAPCAT_HTTP_URL"] = f"http://127.0.0.1:{ob_port}"
    
    try:
        msg_tool = SendQQMessageTool()
        assert msg_tool.name == "send_qq_message"
        
        # 验证输入校验
        invalid_val = await msg_tool.validate_input({"msg_type": "invalid", "target_id": "123", "message": "hello"})
        assert invalid_val["result"] is False, "❌ 错误：未成功拦截非法消息类型！"
        
        valid_val = await msg_tool.validate_input({"msg_type": "group", "target_id": "693134080", "message": "hello [CQ:at,qq=123]"})
        assert valid_val["result"] is True, "❌ 错误：合法的群消息参数校验失败！"
        
        # 模拟调用发送消息
        res_list = []
        async for r in msg_tool.call({"msg_type": "group", "target_id": "693134080", "message": "hello"}):
            res_list.append(r)
            
        assert len(res_list) == 1
        assert "成功" in res_list[0].data, f"❌ 错误：发送消息工具执行失败，详情: {res_list[0].data}"
        assert mock_ob_server.last_payload is not None
        assert mock_ob_server.last_payload.get("group_id") == 693134080, "❌ 错误：接口发送的目标ID不匹配！"
        assert mock_ob_server.last_payload.get("message") == "hello", "❌ 错误：接口发送的消息内容不匹配！"
        print("✅ [用例 11] 成功：主动消息发送工具 SendQQMessageTool 单元测试通过！")
    finally:
        mock_ob_server.shutdown()
        mock_ob_server.server_close()

    # ── 12. 验证群聊静默视网膜感知与潜水背景同步机制 ──
    print("\n🧪 [用例 12] 验证群聊静默视网膜感知机制与潜水背景同步（非 @ 发言不唤醒但后台记录）...")
    
    os.environ["QQ_ADMIN_ID"] = "1705919142"
    os.environ["QQ_WHITE_GROUPS"] = "693134080"
    
    class SilentTestGateway(QQGateway):
        def __init__(self):
            super().__init__(lambda key: Agent(llm=MockLLM(), registry=None, memory=None, session=None))
            self.task_executed = False
            self.sent_messages = []
            
        async def _execute_task(self, session_key, event, raw):
            self.task_executed = True
            await super()._execute_task(session_key, event, raw)
            
        async def _send(self, msg_type, user_id, group_id, message, skip_delay=False):
            self.sent_messages.append(message)
            
    gateway_silent = SilentTestGateway()
    
    # 模拟群聊中， coworker 小宇 (QQ 1911828529) 发送了一条没有 @ 小萤的消息
    event_silent_msg = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1911828529,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "今天天气真不错呀，适合写代码"
    }
    
    await gateway_silent._handle(event_silent_msg)
    await asyncio.sleep(0.05)
    
    # 断言：没有执行大模型任务，没有向群里回复任何消息（静默沉默）
    assert gateway_silent.task_executed is False, "❌ 错误：不带 @ 的群发言居然触发了大模型调用！"
    assert len(gateway_silent.sent_messages) == 0, "❌ 错误：不带 @ 的群发言居然产生了消息回复！"
    
    # 断言：管理员亮哥的群聊 Agent (group_693134080_1705919142) 已被成功创建，且其 messages 中已追加此条静默发言背景
    admin_agent = gateway_silent._agents.get("group_693134080_1705919142")
    assert admin_agent is not None, "❌ 错误：群聊静默消息未能强制触发亮哥 Agent 的实例化！"
    
    # 查找追加的静默背景发言
    silent_msg_found = False
    for m in admin_agent.messages:
        if "[来自 QQ: 1911828529 的群发言] 今天天气真不错呀，适合写代码" in m.get("content", ""):
            silent_msg_found = True
            break
            
    assert silent_msg_found is True, "❌ 错误：亮哥 Agent 内存消息队列中未寻获静默感知背景数据！"
    print("✅ [用例 12] 成功：群聊静默感知不唤醒、亮哥专属实例强制在线及历史追加同步验证完美通过！")
        
    # ── 13. 验证群聊智能浮出决策与频控防骚扰机制 ──
    print("\n🧪 [用例 13] 验证群聊智能主动浮出与十分钟冷却频控防骚扰机制...")
    
    class FloatTestGateway(QQGateway):
        def __init__(self):
            super().__init__(lambda key: Agent(llm=MockLLM(), registry=None, memory=None, session=None))
            self.sent_messages = []
            
        async def _send(self, msg_type, user_id, group_id, message, skip_delay=False):
            self.sent_messages.append(message)
            
    gateway_float = FloatTestGateway()
    
    # 13-a: 模拟群聊发言无敏感词，如“我们今晚去吃火锅吧”，应该秒级秒退保持绝对沉默
    event_no_sensitive = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1911828529,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "我们今晚去吃火锅吧"
    }
    await gateway_float._handle(event_no_sensitive)
    await asyncio.sleep(0.05)
    assert len(gateway_float.sent_messages) == 0, "❌ 错误：无敏感词群消息居然触发了浮出回复！"
    
    # 13-b: 模拟群聊发言含有敏感词且大模型判定浮出：
    # 物理注入 MockLLM 的响应内容为：{"should_reply": true, "reply_content": "亮哥，小萤注意到代码有Bug，我来帮您解答！"}
    # 并且使用 group_693134080_1705919142 的 Agent 进行判定
    admin_agent = gateway_float._factory("group_693134080_1705919142")
    gateway_float._agents["group_693134080_1705919142"] = admin_agent
    admin_agent.llm.next_response = {
        "content": '{"should_reply": true, "reply_content": "亮哥，小萤注意到代码有Bug，我来帮您解答！"}',
        "tool_calls": []
    }
    
    event_sensitive_reply = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1911828529,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "小萤这代码bug怎么解"
    }
    
    # 运行 _handle，这会触发 asyncio.create_task 后台执行，我们需要等待一下它跑完
    await gateway_float._handle(event_sensitive_reply)
    await asyncio.sleep(0.1)  # 等待异步任务跑完大模型决策
    
    assert len(gateway_float.sent_messages) == 1, "❌ 错误：有敏感词且大模型判定浮出时，居然未能产生浮出回复！"
    assert "Bug" in gateway_float.sent_messages[0], f"❌ 错误：主动浮出的消息内容不符合预期，得到: {gateway_float.sent_messages[0]}"
    
    # 13-c: 验证 10 分钟频控：在 10 分钟冷却期内再次发送含有敏感词的消息，应该被绝对拦截不进行 LLM 判定
    gateway_float.sent_messages.clear()
    admin_agent.llm.chat_history.clear() # 清空调用历史，方便校验是否调用了大模型
    
    event_sensitive_throttled = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1911828529,
        "group_id": 693134080,
        "self_id": 222222,
        "raw_message": "小萤，刚才的代码提交成功了吗"
    }
    
    await gateway_float._handle(event_sensitive_throttled)
    await asyncio.sleep(0.05)
    
    assert len(gateway_float.sent_messages) == 0, "❌ 错误：在10分钟限流冷却期内居然再次浮出了！"
    assert len(admin_agent.llm.chat_history) == 0, "❌ 错误：在限流冷却期内居然仍然去调用了大模型进行判定！"
    
    print("✅ [用例 13] 成功：智能浮出秒级降噪、轻量决策以及 10 分钟冷却频控拦截单元测试完美通过！")
        
    # ── 14. 验证重构异常警报为拟人化温柔反馈机制 ──
    print("\n🧪 [用例 14] 验证异常警报重构（工具报错静默及LLM死机时温柔兜底）...")
    
    class MockExceptionAgent:
        def __init__(self):
            self.role = "admin"
            self.current_user_id = "1705919142"
            self.messages = []
            self.sandbox_violation_count = 0
            
            from pathlib import Path
            class MockMemory:
                base_dir = Path("/nonexistent_dir_for_test")
            self.memory = MockMemory()
            self.yielded_events = []
            
        async def run(self, raw, stream=True):
            for evt in self.yielded_events:
                yield evt
                
    class ExceptionTestGateway(QQGateway):
        def __init__(self):
            super().__init__(lambda key: Agent(llm=MockLLM(), registry=None, memory=None, session=None))
            self.sent_messages = []
            self.mock_agent = MockExceptionAgent()
            self._agents["user_1705919142"] = self.mock_agent
            
        def _factory(self, session_key):
            return self.mock_agent
            
        async def _send(self, msg_type, user_id, group_id, message, skip_delay=False):
            self.sent_messages.append(message)
            
        async def _send_chunk(self, msg_type, user_id, group_id, message):
            self.sent_messages.append(message)

    # 用例 14-a: 验证工具发生 Error 错误时，网关动态转换为人性化汇报（如爬虫防爬被拒）
    gateway_exception = ExceptionTestGateway()
    gateway_exception.mock_agent.yielded_events = [
        {"type": "tool_result", "name": "web_fetch", "result": "Error: empty response from server"}
    ]
    
    event_trigger = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 1705919142,
        "raw_message": "模拟工具调用错误测试"
    }
    await gateway_exception._execute_task("user_1705919142", event_trigger, "模拟工具调用错误测试")
    
    assert len(gateway_exception.sent_messages) == 1, f"❌ 错误：工具报错时网关未能发送高情商人性化汇报！发送消息数: {len(gateway_exception.sent_messages)}"
    tool_err_msg = gateway_exception.sent_messages[0]
    assert "⚠️ [警告]" not in tool_err_msg, f"❌ 错误：人性化汇报中居然保留了机器味强警告！得到: {tool_err_msg}"
    assert any(x in tool_err_msg for x in ["拒绝", "防爬", "闭门羹"]), f"❌ 错误：爬虫失败拟人特征词断言失败！得到: {tool_err_msg}"

    # 用例 14-b: 验证大模型发生 error 故障时，网关高情商拟人化温柔兜底 (503/Busy)
    gateway_exception_err = ExceptionTestGateway()
    gateway_exception_err.mock_agent.yielded_events = [
        {"type": "error", "content": "Service is too busy"}
    ]
    await gateway_exception_err._execute_task("user_1705919142", event_trigger, "模拟503错误测试")
    
    assert len(gateway_exception_err.sent_messages) == 1, "❌ 错误：503错误时网关未发送温柔兜底消息！"
    reply_msg = gateway_exception_err.sent_messages[0]
    assert any(x in reply_msg for x in ["走神", "发呆", "懵懵", "拥堵", "反应过来", "清醒", "卡住", "喘口气"]), f"❌ 错误：503兜底人设台词特征不匹配！得到: {reply_msg}"
    assert "[错误:" not in reply_msg, f"❌ 错误：温柔兜底中居然夹带了机器错误标签！得到: {reply_msg}"

    # 用例 14-c: 验证大模型遭遇未知致命崩溃时，网关高情商拟人化可爱困惑兜底
    gateway_exception_crash = ExceptionTestGateway()
    gateway_exception_crash.mock_agent.yielded_events = [
        {"type": "error", "content": "api_key_invalid"}
    ]
    await gateway_exception_crash._execute_task("user_1705919142", event_trigger, "模拟致命崩溃测试")
    
    assert len(gateway_exception_crash.sent_messages) == 1, "❌ 错误：致命崩溃时网关未发送温柔兜底消息！"
    reply_crash = gateway_exception_crash.sent_messages[0]
    assert any(x in reply_crash for x in ["电路", "噼啪", "想不起来", "打了个结", "思绪", "乱了一下"]), f"❌ 错误：崩溃温柔兜底台词特征不匹配！得到: {reply_crash}"
    assert "[错误:" not in reply_crash, f"❌ 错误：温柔崩溃兜底中居然夹带了机器错误标签！得到: {reply_crash}"

    print("✅ [用例 14] 成功：智能错误转义及多分支真人温柔高情商兜底拦截验证 100% 通过！")
        
    print("\n🎉 [测试结果] 所有隐私沙箱物理隔离机制与群聊唤醒静默视网膜感知机制单元测试全部完美跑通！")

if __name__ == "__main__":
    asyncio.run(run_test())
    import os
    os._exit(0)
