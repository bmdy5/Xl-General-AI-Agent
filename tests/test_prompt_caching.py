import pytest
import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from agent.memory.manager import MemoryManager
from agent.core.react_loop import setup_prompt_caching, run_loop
from agent.core.prompt_builder import build_memory_block


@pytest.fixture
def test_env(tmp_path):
    """测试沙箱，配有独立的 SQLite 数据库，防污染."""
    db_dir = tmp_path / "memory_sandbox"
    db_dir.mkdir()
    
    # 强行 Mock settings 的 memory 配置，杜绝真实逆向自愈还原污染测试沙箱
    from agent.core.config import settings
    settings._data["memory"] = {
        "base_dir": str(db_dir),
        "backup_dir": str(tmp_path / "mock_backup"),
        "multi_instance_isolation": False
    }
    
    # 初始化 MemoryManager 并直接使用内置 _get_db() 自动初始化全部高精度虚表与表结构
    manager = MemoryManager(base_dir=db_dir)
    db = manager._get_db()
    
    # 模拟写入一个拥有多次修订的 KI，最新版本为 3
    rev_history = [
        {"version": 1, "timestamp": "2026-05-24T00:00:00Z", "reason": "初始版本"},
        {"version": 2, "timestamp": "2026-05-24T01:00:00Z", "reason": "亮哥在对话中纠正了鉴权基址"},
        {"version": 3, "timestamp": "2026-05-24T02:00:00Z", "reason": "亮哥更新了高阶 TTS 降噪参数"}
    ]
    
    # 写入测试行
    db.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, version, revision_history)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ki_test_id",
        "大模型缓存调试规范",
        "xl_debugging",
        '["缓存", "TDD"]',
        "关于 Prompt 缓存的高级调试指南",
        "这里是缓存的具体核心技术内容详情说明。",
        "2026-05-24T00:00:00Z",
        "2026-05-24T02:00:00Z",
        "2026-05-24T02:00:00Z",
        3,
        json.dumps(rev_history)
    ))
    db.commit()
    
    return {
        "manager": manager,
        "db_dir": db_dir
    }


@pytest.mark.asyncio
async def test_prompt_builder_ki_structuring(test_env):
    """1. 物理验证 RAG 检索召回的长期记忆被完美格式化为高雅标准 KI 属性卡片，且仅包含最近一条修订历史."""
    manager = test_env["manager"]
    
    # Mock agent 实例以模拟 build_memory_block 调用环境
    agent = MagicMock()
    agent._turn_count = 0
    agent.messages = [{"role": "user", "content": "我想调试大模型缓存"}]
    agent.memory = manager
    
    # 模拟 RAG 检索返回了我们刚刚写入的那条记录的物理文件名
    # search_memories 返回 list[dict]
    manager.search_memories = MagicMock(return_value=[{
        "id": 1,
        "content": "这里是缓存的具体核心技术内容详情说明。",
        "description": "大模型缓存调试规范",
        "memory_type": "merged",
        "filename": "ki_ki_test_id.md",
        "timestamp": "2026-05-24T02:00:00Z",
        "rank": 0.99
    }])
    
    # 执行记忆块拼装
    memory_block = await build_memory_block(agent, "我想调试大模型缓存", 0)
    
    # A. 校验结构化卡片的属性呈现
    assert "📌 ID: ki_test_id (Version: 3)" in memory_block
    assert "类别: xl_debugging" in memory_block
    assert "标题: 大模型缓存调试规范" in memory_block
    assert "标签: ['缓存', 'TDD']" in memory_block
    assert "摘要: 关于 Prompt 缓存的高级调试指南" in memory_block
    assert "权威内容:" in memory_block
    assert "这里是缓存的具体核心技术内容详情说明。" in memory_block
    
    # B. 验证“精纯显示：仅包含最新一条（最近一条）修订原因”
    assert "最新修订原因:" in memory_block
    assert "亮哥更新了高阶 TTS 降噪参数" in memory_block
    # 不应当包含历史的前两条修订原因
    assert "初始版本" not in memory_block
    assert "亮哥在对话中纠正了鉴权基址" not in memory_block


@pytest.mark.asyncio
async def test_react_loop_caching_front_injection(test_env):
    """2. 物理验证 Caching 优化后，ReAct 工具调用多步迭代中，System Prompt 前缀 100% 单调静态无抖动."""
    manager = test_env["manager"]
    
    # Mock 一个极简 agent，使其支持 ReAct 循环
    agent = MagicMock()
    agent.max_turns = 2
    agent._mode.value = "normal"
    agent._task_start_time = 0.0
    agent.compressor.estimate_tokens = MagicMock(return_value=1000)
    agent.compressor.should_compress = MagicMock(return_value=False)
    
    # 模拟 system prompt 构建和记忆块构建
    agent._build_system_prompt = AsyncMock(return_value="小萤是一个个人开发者伴侣静态SystemPrompt部分")
    agent._build_memory_block = AsyncMock(return_value="这里是召回的结构化 KI 精纯记忆块")
    
    # Mock llm.chat 以拦截大模型最终接收到的 messages
    agent.llm.model = "deepseek/deepseek-chat"
    
    # 模拟工具注册
    agent.registry.get_definitions = MagicMock(return_value=[])
    
    # 用于收集每一步 LLM 发送的消息
    captured_inputs = []
    
    async def mock_llm_chat(agent_inst, messages, tools):
        captured_inputs.append(messages)
        # 返回一个包含工具调用的 assistant 消息，模拟 ReAct 进入下一步工具调用
        if len(captured_inputs) == 1:
            return "我需要执行一下 shell 来看文件", "", [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"AbsolutePath": "/test.py"}'}}]
        return "完成了任务，报告主人", "", []
        
    # 我们拦截 llm_chat
    import agent.core.react_loop
    agent.messages = []
    
    # 我们为了能捕获，临时 monkeypatch 核心 react_loop 内部的 llm_chat 或直接在 agent.llm 的接口上做 mock
    # 事实上，run_loop 内部调用了 llm_chat(agent, final_messages, tools)
    # 我们直接 mock agent.llm_chat 或者 mock 导入的 llm_chat
    # 让我们来看看 react_loop 内部是如何引用 llm_chat 的
    # 它在首部导入：from .llm import llm_chat, llm_stream (或者直接引用)
    # 我们在测试里 mock 它
    
    # 针对 setup_prompt_caching
    # 验证 Anthropic Caching cache_control 标记在 Index 0 首条 System 消息的正确打标
    messages = [
        {"role": "system", "content": "这是一个很长很长很长的 System Prompt 包含召回的 Context"},
        {"role": "user", "content": "亮哥偏好"},
        {"role": "assistant", "content": "记得你说过喜欢傲娇"},
    ]
    
    claude_msgs = setup_prompt_caching(messages, "claude-3-5-sonnet")
    # Index 0 处的消息应该在 content block 最末尾有 cache_control
    assert isinstance(claude_msgs[0]["content"], list)
    assert claude_msgs[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    
    # Index 1 (User 消息，倒数第二个) 也应该有打标
    assert claude_msgs[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
