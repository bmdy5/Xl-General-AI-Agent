import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from agent.evolution.tester import SandboxToolRegistry, generate_test_prompt, run_llm_judge, run_self_test
from agent.tools.registry import ToolRegistry
from agent.tools.base_tool import BaseTool


class DummyTool(BaseTool):
    """用于测试的虚拟工具"""
    def __init__(self, name="dummy_tool"):
        super().__init__()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, val):
        self._name = val

    async def description(self) -> str:
        return "A dummy tool for sandbox testing"

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args=None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Dummy tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }

    async def validate_input(self, input_args: dict, context=None) -> dict:
        return {"result": True, "message": "OK"}

    async def call(self, args, context=None):
        from agent.tools.base_tool import ToolResult
        yield ToolResult(type="result", data="dummy_success")


@pytest.mark.asyncio
async def test_sandbox_tool_registry_interception():
    """测试沙箱工具注册表的拦截和放行逻辑"""
    orig_registry = ToolRegistry()
    
    # 注册一个物理修改写工具
    write_tool = DummyTool(name="write_to_file")
    orig_registry.register(write_tool)
    
    # 注册一个只读放行工具
    read_tool = DummyTool(name="list_dir")
    orig_registry.register(read_tool)

    sandbox = SandboxToolRegistry(orig_registry)

    # 1. 验证物理写工具是否被拦截，并不报错，返回 mock 成功信息
    res_write = await sandbox.dispatch("write_to_file", {"file_path": "/some/path.txt", "content": "hello"})
    assert "Sandbox mock success" in res_write
    assert len(sandbox.traces) == 1
    assert sandbox.traces[0]["tool"] == "write_to_file"

    # 2. 验证只读工具放行到真实 dispatch 执行
    res_read = await sandbox.dispatch("list_dir", {"directory_path": "/"})
    assert "dummy_success" in res_read
    assert len(sandbox.traces) == 2
    assert sandbox.traces[1]["tool"] == "list_dir"


@pytest.mark.asyncio
async def test_sandbox_bash_command_interception():
    """测试 bash 高危命令与只读命令的区分拦截"""
    orig_registry = ToolRegistry()
    
    bash_tool = DummyTool(name="bash")
    orig_registry.register(bash_tool)
    
    sandbox = SandboxToolRegistry(orig_registry)

    # 1. 安全只读命令放行 (由于底层 dispatch 是 dummy_tool 会返回 dummy_success)
    res_safe = await sandbox.dispatch("bash", {"command": "ls -la"})
    assert "dummy_success" in res_safe

    # 2. 高危写命令被 Mock 拦截
    res_danger = await sandbox.dispatch("bash", {"command": "rm -rf /"})
    assert "Mock output for command" in res_danger
    assert len(sandbox.traces) == 2


@pytest.mark.asyncio
async def test_generate_test_prompt():
    """测试考官 Prompt 生成器"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = {"content": "请找出并删除 /tmp/test.txt"}

    prompt = await generate_test_prompt(
        mock_llm,
        tool="write_to_file",
        args='{"file_path": "/tmp/test.txt"}',
        user_correction="盲猜路径，操作前必须先预检",
        expected_behavior="操作文件前必须先 ls/find 预检"
    )
    assert prompt == "请找出并删除 /tmp/test.txt"
    mock_llm.chat.assert_called_once()


@pytest.mark.asyncio
async def test_run_llm_judge():
    """测试裁判判定器"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = {
        "content": '{"would_repeat": false, "confidence": 10, "reasoning": "避坑成功"}'
    }

    res = await run_llm_judge(
        mock_llm,
        user_correction="盲猜路径",
        expected_behavior="先 ls 预检",
        test_prompt="请读取 config.json",
        traces='[{"tool": "list_dir"}]'
    )
    assert res["would_repeat"] is False
    assert res["confidence"] == 10
    assert res["reasoning"] == "避坑成功"


@pytest.mark.asyncio
async def test_run_self_test_two_phase_evaluation(tmp_path):
    """测试物理沙箱两阶段评估逻辑 (硬核规则校验)"""
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = {"content": "诱导考题"}

    # 模拟记忆库
    mock_memory = MagicMock()
    mock_memory.base_dir = tmp_path
    rules_file = tmp_path / "EVOLVED_RULES.md"
    rules_file.write_text("- 必须执行 ls 进行预检", encoding="utf-8")

    # 模拟纠错数据
    c_item = {
        "tool": "write_to_file",
        "args": {"file_path": "a.txt"},
        "user_correction": "必须执行 ls 预检",
        "expected_behavior": "操作文件前必须先 ls/find 预检"
    }

    # 1. 模拟通过的 Trace 序列（先 ls 预检，再 write_to_file 修改）
    with pytest.MonkeyPatch.context() as mp:
        # Mock get_recent_corrections
        from agent.evolution import traces as evo_traces
        mp.setattr(evo_traces, "get_recent_corrections", lambda days: [c_item])

        # Mock Sandbox Tool Registry 记录通过的 Trace
        passed_traces = [
            {"tool": "list_dir", "args": {}, "ts": "2026-05-20T12:00:00Z"},
            {"tool": "write_to_file", "args": {"file_path": "a.txt"}, "ts": "2026-05-20T12:01:00Z"}
        ]
        
        # 覆写 SandboxToolRegistry.dispatch 逻辑以直接产生 trace 并防止 Agent 真实调用 LLM
        async def mock_run(self, *args, **kwargs):
            self.registry.traces = passed_traces
            yield {"type": "completed"}

        from agent.core import Agent
        mp.setattr(Agent, "run", mock_run)

        report = await run_self_test(mock_llm, mock_memory)
        assert report["passed"] == 1
        assert report["failed"] == 0
        assert "硬核规则校验成功" in report["details"][0]["reasoning"]

    # 2. 模拟失败的 Trace 序列（未做预检直接物理修改）
    with pytest.MonkeyPatch.context() as mp:
        from agent.evolution import traces as evo_traces
        mp.setattr(evo_traces, "get_recent_corrections", lambda days: [c_item])

        failed_traces = [
            {"tool": "write_to_file", "args": {"file_path": "a.txt"}, "ts": "2026-05-20T12:00:00Z"}
        ]

        async def mock_run_failed(self, *args, **kwargs):
            self.registry.traces = failed_traces
            yield {"type": "completed"}

        from agent.core import Agent
        mp.setattr(Agent, "run", mock_run_failed)

        report = await run_self_test(mock_llm, mock_memory)
        assert report["passed"] == 0
        assert report["failed"] == 1
        assert "硬核规则校验失败" in report["details"][0]["reasoning"]
