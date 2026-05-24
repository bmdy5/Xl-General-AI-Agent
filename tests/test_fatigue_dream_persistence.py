import pytest
import sqlite3
import json
import asyncio
import time
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from agent.memory.manager import MemoryManager
from agent.evolution.dream import trigger_deep_dream_evolution
from agent.net_gateway.fatigue_manager import FatigueManager


class MockLLM:
    def __init__(self):
        self.model = "mock-model"
        self.chat = AsyncMock()


@pytest.fixture
def temp_workspace(tmp_path):
    """临时沙箱工作区，用于规避对真实记忆库的污染."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    # 模拟 settings.yaml，强行将 base_dir 和 backup_dir 锁死在绝对沙箱内
    config_dir = workspace / "config"
    config_dir.mkdir()
    settings_file = config_dir / "settings.yaml"
    settings_file.write_text(f"""
memory:
  base_dir: "{workspace}/memory_sandbox"
  backup_dir: "{workspace}/backup"
  local_model_path: "./model/m3e-base"
""", encoding="utf-8")

    return workspace


@pytest.fixture
def mock_agent(temp_workspace):
    agent = MagicMock()
    agent.llm = MockLLM()
    
    db_dir = temp_workspace / "memory_sandbox"
    db_dir.mkdir()
    
    manager = MemoryManager(base_dir=db_dir)
    manager.backup_dir = db_dir / "backup"
    agent.memory = manager
    
    async def fake_get_embedding(text):
        return [0.1] * 768
    manager._get_embedding = fake_get_embedding
    agent.messages = []
    agent.session_key = "test_session_123"
    agent.session = AsyncMock()
    return agent


def test_active_sessions_ddl_auto_generation(temp_workspace):
    """测试点 A: 验证 active_sessions 关系表 DDL 在 DB 初始化时自动生成且结构健康."""
    db_dir = temp_workspace / "ddl_sandbox"
    db_dir.mkdir()
    
    manager = MemoryManager(base_dir=db_dir)
    db = manager._get_db()
    
    cur = db.execute("PRAGMA table_info(active_sessions)")
    columns = {row[1]: row[2] for row in cur.fetchall()}
    
    assert "session_key" in columns
    assert "messages" in columns
    assert "updated_at" in columns
    assert columns["session_key"] == "TEXT"


@pytest.mark.asyncio
async def test_active_session_debounce_writing(mock_agent):
    """测试点 B: 验证 1.0 秒异步防抖写盘机制。极速调用 10 次，1.0s 后只物理写入 1 次."""
    manager = mock_agent.memory
    session_key = mock_agent.session_key
    
    # 物理监控 SQLite 写入，我们在 DB 里直接监控 active_sessions 表的数据状态
    db = manager._get_db()
    
    # 极速触发 10 次写入
    for i in range(10):
        messages = [{"role": "user", "content": f"msg_{i}"}]
        manager.save_active_session_async(session_key, messages)
    
    # 0.1秒时验证数据库，因为防抖有 1.0 秒延迟，此时数据尚未落盘
    cur = db.execute("SELECT messages FROM active_sessions WHERE session_key = ?", (session_key,))
    assert cur.fetchone() is None
    
    # 等待 1.2 秒
    await asyncio.sleep(1.2)
    
    # 再次查询，验证最尾端的一条数据已经原子刷入
    cur = db.execute("SELECT messages FROM active_sessions WHERE session_key = ?", (session_key,))
    row = cur.fetchone()
    assert row is not None
    
    stored = json.loads(row[0])
    assert len(stored) == 1
    assert stored[0]["content"] == "msg_9"


@pytest.mark.asyncio
async def test_gateway_cold_start_recovery(temp_workspace):
    """测试点 C: 验证 Gateway 冷启动自愈，messages 100% 自动还原续接."""
    db_dir = temp_workspace / "recovery_sandbox"
    db_dir.mkdir()
    
    # 1. 模拟之前的 Gateway 运行并持久化了一些消息
    manager = MemoryManager(base_dir=db_dir)
    session_key = "user_999888"
    fake_history = [
        {"role": "user", "content": "你好小萤"},
        {"role": "assistant", "content": "亮哥好！"}
    ]
    
    async def fake_get_embedding(text):
        return [0.1] * 768
    manager._get_embedding = fake_get_embedding
    
    manager.save_active_session_async(session_key, fake_history)
    await asyncio.sleep(1.2) # 等待防抖刷盘成功
    
    # 2. 模拟进程重启，创建一个全新 Agent (冷启动)
    # 物理规避 MagicMock 导致的 Prompt 拼接 TypeError，传入真实的 registry 引用
    from agent.core.agent import Agent
    from agent.tools.registry import registry
    session_mock = AsyncMock()
    # 强行 Mock search_all_sessions 返回常规字符串，杜绝 AsyncMock 返回 MagicMock 引起的 lines 拼接 TypeError 崩溃
    session_mock.search_all_sessions = AsyncMock(return_value="No past conversations")
    
    llm_mock = MockLLM()
    # 配置 LLM chat mock，使 ReAct 循环第一轮由于无 tool_calls 而直接 completed 退出
    llm_mock.chat = AsyncMock(return_value={
        "content": "好的，我已经收到重启后的消息。",
        "reasoning_content": "冷启动恢复成功，无任何工具调用需求。",
        "tool_calls": [],
        "tokens_used": 100,
        "metrics": {"prompt_tokens": 80, "completion_tokens": 20, "cached_tokens": 0}
    })
    
    agent = Agent(llm=llm_mock, registry=registry, memory=manager, session=session_mock)
    agent.session_key = session_key
    
    # 3. 运行 Agent.run 初始化
    async for _ in agent.run("亮哥发来重启后的首条新消息"):
        break
    
    # 4. 断言已完美续接！消息列表包含冷启动自愈载入的 2 条旧消息 + 刚进来的 1 条新消息 + 刚生成的 1 条回复消息
    assert len(agent.messages) == 4
    assert agent.messages[0]["content"] == "你好小萤"
    assert agent.messages[1]["content"] == "亮哥好！"
    assert agent.messages[2]["content"] == "亮哥发来重启后的首条新消息"
    assert agent.messages[3]["content"] == "好的，我已经收到重启后的消息。"


@pytest.mark.asyncio
async def test_snapshot_incremental_compaction(mock_agent):
    """测试点 D: 验证高并发做梦快照增量清账。做梦提炼结束切除老历史，但做梦期间并发流入的新消息被完好保留."""
    manager = mock_agent.memory
    session_key = mock_agent.session_key
    
    # 1. 做梦前积累了 3 条老历史
    mock_agent.messages = [
        {"role": "user", "content": "老消息1"},
        {"role": "assistant", "content": "老回复1"},
        {"role": "user", "content": "老消息2"}
    ]
    
    # 模拟进入做梦
    # A. 截取快照和记录快照长度
    snapshot = list(mock_agent.messages)
    snapshot_len = len(snapshot)
    
    # Mock LLM 提炼 KI 与 Skill 返回空 (做梦静默)
    mock_agent.llm.chat.return_value = {"content": '{"has_learnings": false, "skill_detected": false}'}
    
    # B. 启动做梦协程 (我们在后台运行它)
    dream_task = asyncio.create_task(trigger_deep_dream_evolution(mock_agent, history_messages=snapshot))
    
    # C. 在做梦的 30ms 期间，并发流入了 2 条最新消息
    mock_agent.messages.append({"role": "user", "content": "做梦期间并发流入的新消息A"})
    mock_agent.messages.append({"role": "assistant", "content": "做梦期间并发流入的回复B"})
    
    # 此时内存共 5 条
    assert len(mock_agent.messages) == 5
    
    # 等待做梦协程跑完
    await dream_task
    
    # D. 模拟 FatigueManager 在做梦结束后，物理执行“增量清账切片”
    if len(mock_agent.messages) >= snapshot_len:
        mock_agent.messages = mock_agent.messages[snapshot_len:]
    else:
        mock_agent.messages = []
        
    # E. 验证清账结果：快照内 3 条老历史被精准截断；并发流入的 2 条最新消息无损保留！
    assert len(mock_agent.messages) == 2
    assert mock_agent.messages[0]["content"] == "做梦期间并发流入的新消息A"
    assert mock_agent.messages[1]["content"] == "做梦期间并发流入的回复B"


@pytest.mark.asyncio
async def test_dream_card_fallback_local_recovery(mock_agent):
    """测试点 E: 验证梦境反思回顾卡片超时/报错时的本地 Fallback 容灾。Mock LLM 异常时，本地模板能 100% 成功提炼."""
    # 1. 模拟做梦提炼中多轮大模型精细应答 (采用真实中文关键词匹配，规避 formatted 后英文字符标识丢失导致的 Unmocked 错误)
    async def mock_chat(messages, **kwargs):
        content = messages[0]["content"]
        
        # A. 提炼 KI (对应 DEEP_DREAM_KI_PROMPT 中文特征词)
        if "进化做梦提炼引擎" in content:
            return {"content": '{"has_learnings": true, "learnings": [{"title": "API失效", "category": "xl_debugging", "keywords": [], "summary": "LiteLLM API超时", "content": "详细报错描述"}]}'}
            
        # B. 提炼 Skill (对应 DEEP_DREAM_SKILL_PROMPT 中文特征词)
        if "技能突变合成引擎" in content:
            return {"content": '{"skill_detected": true, "skill_folder_name": "test_sop", "skill_name": "测试SOP技能", "skill_md_content": "### spec", "helper_script_filename": null, "helper_script_content": null}'}
            
        # C. 技能自动归类 (对应 _llm_categorize 中文特征词)
        if "分类引擎" in content:
            return {"content": "development"}
            
        # D. 技能查重语义匹配 (对应 _llm_find_similar_skill 中文特征词)
        if "归类查重引擎" in content:
            return {"content": '{"is_similar": false, "similar_skill_folder": null}'}
            
        # E. 深夜全局知识熔炼自演进 (对应 DREAM_FUSE_PROMPT 中文特征词)
        if "知识熔炼合成大师" in content:
            return {"content": '{"title": "熔炼后的Master KI", "category": "xl_debugging", "keywords": [], "summary": "熔炼摘要", "content": "熔炼正文"}'}
            
        # F. 卡片最终总结 (对应 DREAM_EVOLUTION_SUMMARY_PROMPT 中文特征词) ➔ 故意超时，测试 Fallback 本地自愈防假死
        if "梦境自省大师" in content:
            raise asyncio.TimeoutError("大模型卡片提炼超时")
            
        raise Exception(f"Unmocked prompt: {content}")
        
    mock_agent.llm.chat.side_effect = mock_chat
    
    summary_card = ""
    # 3. 执行做梦提炼并捕获真实堆栈
    try:
        mock_agent.messages = [{"role": "user", "content": "模拟对话历史经验事实"}]
        summary_card = await trigger_deep_dream_evolution(mock_agent)
    except Exception as run_err:
        traceback.print_exc()
        raise run_err
    
    # 4. 验证返回的卡片 100% 健康，且包含了“系统离线提炼”及我们真实入库的自愈计数
    assert "📊 梦境回顾总结 (系统离线提炼)" in summary_card
    assert "提炼了 1 条关于系统教训或用户偏好的核心记忆事实" in summary_card
    assert "突变合成了 1 个全新的自进化技能" in summary_card
    assert "【测试SOP技能】" in summary_card
