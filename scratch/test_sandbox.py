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
    from agent.tools.file_tools import ReadFileTool
    from agent.tools.bash_tool import BashTool
    
    reg = ToolRegistry()
    reg.register(ReadFileTool())
    reg.register(BashTool(work_dir=os.getcwd()))
    
    agent = Agent(llm=llm, registry=reg, memory=None, session=None)
    agent.role = "coworker"
    agent.current_user_id = "2297756819"
    
    # 模拟大模型想要调用 bash 敏感工具
    llm.next_response = {
        "content": "我正要执行命令看看系统环境：",
        "tool_calls": [{
            "id": "call_12345",
            "type": "function",
            "function": {
                "name": "bash",
                "arguments": '{"command": "cat /etc/passwd"}'
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
    assert "strictly restricted in coworker sandboxed session" in res_val, "❌ 错误：拦截的拒信描述不符合预期！"
    
    # 因为 MockLLM 持续响应相同工具，用例 2 内部实际上累计尝试了 2 次并被彻底冻结
    assert agent.sandbox_violation_count >= 2, f"❌ 错误：违规计数器值不符合预期，得到: {agent.sandbox_violation_count}"
    
    # 检查是否成功触发了多次高危冻结封禁的系统级 error 事件
    err_events = [e for e in events if e.get("type") == "error"]
    assert len(err_events) == 1, "❌ 错误：未正确触发系统级冻结 error 事件！"
    assert "系统安全防线拦截" in err_events[0]["content"], f"❌ 错误：封禁提示词不匹配，得到: {err_events[0]['content']}"
    
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
    assert "系统安全防线拦截" in gateway.sent_messages[0], f"❌ 错误：网关冻结通知不符合预期，得到: {gateway.sent_messages[0]}"
    
    print("✅ [用例 5] 成功：网关白名单、同事身份注入与网关层秒级高危硬拦截校验通过！")
    
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
        
    print("\n🎉 [测试结果] 所有隐私沙箱物理隔离机制（升级版）单元测试全部完美跑通！")

if __name__ == "__main__":
    asyncio.run(run_test())
