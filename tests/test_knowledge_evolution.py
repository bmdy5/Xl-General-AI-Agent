import pytest
import sqlite3
import json
import math
import shutil
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from agent.memory.manager import MemoryManager
from agent.evolution.dream import process_dream_ki, trigger_deep_dream_evolution


class MockLLM:
    def __init__(self):
        self.model = "mock-model"
        self.chat = AsyncMock()


@pytest.fixture
def temp_workspace(tmp_path):
    """临时沙箱工作区，用于规避对真实记忆库的污染."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    # 模拟 settings.yaml 和 model，强行将 base_dir 和 backup_dir 锁死在绝对沙箱 workspace 内！
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
    
    # 强制在沙箱目录进行隔离
    db_dir = temp_workspace / "memory_sandbox"
    db_dir.mkdir()
    
    manager = MemoryManager(base_dir=db_dir)
    manager.backup_dir = db_dir / "backup"
    agent.memory = manager
    
    # Mock embedding，返回固定维度的向量，用于精准测试
    manager._get_embedding = AsyncMock(return_value=[0.1] * 768)
    
    agent.messages = []
    return agent


def test_db_schema_auto_upgrade(temp_workspace):
    """1. 验证 _get_db 对缺失字段时物理快照自愈备份、事务执行及热升级的正确性."""
    db_dir = temp_workspace / "upgrade_sandbox"
    db_dir.mkdir()
    
    db_path = db_dir / "memories.db"
    
    # A. 物理创建一个不带 version 和 revision_history 的老旧 knowledge_items 表
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE knowledge_items (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            keywords TEXT NOT NULL,
            summary TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_hit_at TEXT NOT NULL,
            visit_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    
    # B. 用 MemoryManager 加载它，自动触发热升级自愈
    manager = MemoryManager(base_dir=db_dir)
    manager.backup_dir = db_dir / "backup"
    db = manager._get_db()
    
    # 校验升级后的字段
    cur = db.execute("PRAGMA table_info(knowledge_items)")
    columns = [row[1] for row in cur.fetchall()]
    
    assert "version" in columns
    assert "revision_history" in columns


@pytest.mark.asyncio
async def test_damping_gate_judgment(mock_agent):
    """2. 验证阻尼带 [0.75, 0.90) 内相似度触发大模型终审裁决的分流正确性."""
    manager = mock_agent.memory
    
    # A. 存入一个已有 KI，将它的 embedding mock 出来
    old_ki = {
        "id": "ki_old_damping",
        "title": "LiteLLM API 鉴权失效问题",
        "category": "xl_debugging",
        "keywords": ["LiteLLM", "API"],
        "summary": "这是旧记录",
        "content": "旧内容说明"
    }
    manager.save_ki(old_ki)
    
    # Mock 已有 KI 的 embedding，向量设定为 [0.1, 0.1, ...]
    manager._get_embedding = AsyncMock(side_effect=lambda text: [0.1] * 768)
    
    # 写入它的 embedding 到 db
    embedding_str = json.dumps([0.1] * 768)
    db = manager._get_db()
    with db:
        db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_old_damping", embedding_str))
    
    # B. 新碎片的相似度恰好在阻尼带（这里通过 mock _get_embedding 返回微调向量，使 cos_sim 刚好落入 0.75 - 0.89 之间）
    # 为了简化计算，我们直接在 process_dream_ki 调用的 embedding 查重时控制 cos_sim 
    # 我们将 mock _get_embedding mock 为返回一个稍微不同的向量使得相似度为 0.80
    mock_vec = [0.1] * 768
    # 微微修改后几位
    for i in range(150):
        mock_vec[i] = -0.1 # 反向，大幅拉低相似度
    manager._get_embedding.return_value = mock_vec
    
    # Mock LLM 终审裁决返回 "is_same_subject": true
    mock_agent.llm.chat.return_value = {
        "content": '{"is_same_subject": true}'
    }
    
    # 终审裁决同意后，会接着触发 LLM merge。我们也 Mock merge 的返回 (使用精细 side_effect 防并发干扰)
    async def mock_chat(messages, **kwargs):
        content = messages[0]["content"]
        if "DAMPING_JUDGE_PROMPT" in content:
            return {"content": '{"is_same_subject": true}'}
        return {
            "content": '{"title": "LiteLLM API 终极鉴权解决方案", "category": "xl_debugging", "keywords": ["LiteLLM", "API"], "summary": "合并摘要", "content": "合并融合后内容\\n* v2 (2026-05-24): 亮哥纠正了", "revision_reason": "亮哥在对话中强力纠偏了鉴权基址"}'
        }
    
    mock_agent.llm.chat.side_effect = mock_chat
    
    new_fact = {
        "title": "LiteLLM API 新鉴权路径报错",
        "category": "xl_debugging",
        "keywords": ["LiteLLM", "API"],
        "summary": "新碎片",
        "content": "新事实内容"
    }
    
    # 运行，应该会吞噬合并到已有的 ki_old_damping
    result_id = await process_dream_ki(mock_agent, new_fact)
    
    assert result_id == "ki_old_damping"
    
    # 验证 version 和修订历史
    merged_ki = manager.get_ki("ki_old_damping")
    assert merged_ki["version"] == 2
    assert merged_ki["revision_history"] is not None
    assert len(merged_ki["revision_history"]) == 1
    assert merged_ki["revision_history"][0]["reason"] == "亮哥在对话中强力纠偏了鉴权基址"


@pytest.mark.asyncio
async def test_immediate_fact_revision(mock_agent):
    """3. 验证相似度 >= 0.90 直接触发合并，且新事实冲突覆写、正文尾端历史追加、以及修订字段落盘."""
    manager = mock_agent.memory
    
    old_ki = {
        "id": "ki_fact_override",
        "title": "GPT-SoVITS 依赖库列表",
        "category": "xl_tool_guide",
        "keywords": ["SoVITS", "TTS"],
        "summary": "老版本依赖说明",
        "content": "GPT-SoVITS 默认不需要 wordsegment 依赖。"
    }
    manager.save_ki(old_ki)
    
    # 精准控制相似度为 1.0 (返回一模一样的 embedding)
    manager._get_embedding.return_value = [0.2] * 768
    db = manager._get_db()
    with db:
        db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_fact_override", json.dumps([0.2] * 768)))
        
    # Mock 合并 LLM 返回：以新事实为准覆写冲突
    mock_agent.llm.chat.return_value = {
        "content": '{"title": "GPT-SoVITS 精准依赖列表", "category": "xl_tool_guide", "keywords": ["SoVITS", "wordsegment"], "summary": "纠正后依赖", "content": "GPT-SoVITS 在最新高可用模式下，必须安装 wordsegment 依赖！\\n\\n* v2 (2026-05-24): 亮哥纠正了关于依赖的说法", "revision_reason": "纠正了 wordsegment 缺失导致 API 400 挂起的问题"}'
    }
    
    new_fact = {
        "title": "GPT-SoVITS 必须有 wordsegment 依赖",
        "category": "xl_tool_guide",
        "keywords": ["wordsegment"],
        "summary": "新碎片纠错",
        "content": "高频合成语音必须安装 wordsegment 依赖"
    }
    
    result_id = await process_dream_ki(mock_agent, new_fact)
    assert result_id == "ki_fact_override"
    
    merged_ki = manager.get_ki("ki_fact_override")
    assert merged_ki["version"] == 2
    assert "wordsegment" in merged_ki["content"].lower()
    assert "* v2 (2026-05-24)" in merged_ki["content"]
    assert merged_ki["revision_history"][0]["reason"] == "纠正了 wordsegment 缺失导致 API 400 挂起的问题"


@pytest.mark.asyncio
async def test_deep_dream_clustering_fusion(mock_agent):
    """4. 验证深夜长眠闲置期 0-Token 粗聚类及多碎片 LLM 熔炼合成 Master KI 与旧碎片物理清退."""
    manager = mock_agent.memory
    
    # 模拟存入 3 个相关的碎片 KI (过去 24 小时内)
    # keywords 有重叠，实现 0-Token 聚类到同一个桶中
    ki_1 = {
        "id": "ki_frag_1",
        "title": "SQLite并发锁问题",
        "category": "xl_debugging",
        "keywords": ["SQLite", "并发", "锁"],
        "summary": "碎片1",
        "content": "高并发下 SQLite 容易触发 WAL 锁死。"
    }
    ki_2 = {
        "id": "ki_frag_2",
        "title": "SQLite多开物理隔离",
        "category": "xl_debugging",
        "keywords": ["SQLite", "多开", "隔离"],
        "summary": "碎片2",
        "content": "我们应该采用 admin_id 作为哈希子目录实现多开物理隔离。"
    }
    
    manager.save_ki(ki_1)
    manager.save_ki(ki_2)
    
    # 模拟写入它们的 embeddings，保持活性
    db = manager._get_db()
    with db:
        db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_frag_1", json.dumps([0.1] * 768)))
        db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_frag_2", json.dumps([0.1] * 768)))
        
    k1 = manager.get_ki("ki_frag_1")
    k2 = manager.get_ki("ki_frag_2")
    assert k1["version"] == 1, f"ki_frag_1 version is {k1['version']}"
    assert k2["version"] == 1, f"ki_frag_2 version is {k2['version']}"
    
    # 物理强清退一切逆向自愈还原引入的真实老条目，确保单元测试聚类桶的 100% 绝对纯净无干扰！
    db.execute("DELETE FROM knowledge_items WHERE id NOT IN ('ki_frag_1', 'ki_frag_2')")
    db.execute("DELETE FROM ki_embeddings WHERE ki_id NOT IN ('ki_frag_1', 'ki_frag_2')")
    db.commit()
    
    cur = db.execute("SELECT id, title, version FROM knowledge_items")
    print("ALL ITEMS IN DB BEFORE EVOLUTION (CLEANED):", cur.fetchall())
    # 针对不同 Prompt 进行精细 Mock，避免通用 return_value 导致前序阶段误触发
    async def mock_chat(messages, **kwargs):
        content = messages[0]["content"]
        if "DEEP_DREAM_KI_PROMPT" in content or "DEEP_DREAM_SKILL_PROMPT" in content:
            return {"content": '{"has_learnings": false, "skill_detected": false}'}
        return {
            "content": '{"title": "SQLite 高并发多开物理隔离终极架构", "category": "xl_debugging", "keywords": ["SQLite", "并发锁", "哈希隔离"], "summary": "熔炼合成摘要", "content": "熔炼后的整体干货内容：包含高并发锁和物理隔离说明\\n\\n* v2 (2026-05-24): 深夜全局熔炼"}'
        }
    
    mock_agent.llm.chat.side_effect = mock_chat
    
    # 模拟 agent.messages 包含历史，确保做梦不取消
    mock_agent.messages = [{"role": "user", "content": "SQLite并发锁怎么解决"}]
    
    # 执行自进化
    await trigger_deep_dream_evolution(mock_agent)
    
    # 验证：旧的碎片应该被物理清退 (从 knowledge_items, ki_embeddings, kis_fts 中物理删除)
    cur = db.execute("SELECT id FROM knowledge_items WHERE id IN ('ki_frag_1', 'ki_frag_2')")
    assert len(cur.fetchall()) == 0
    
    cur_emb = db.execute("SELECT ki_id FROM ki_embeddings WHERE ki_id IN ('ki_frag_1', 'ki_frag_2')")
    assert len(cur_emb.fetchall()) == 0
    
    # 验证：新 Master 级知识已成功入库
    cur_new = db.execute("SELECT id, title, version, revision_history FROM knowledge_items WHERE id LIKE 'ki_fused_%'")
    fused_rows = cur_new.fetchall()
    assert len(fused_rows) == 1
    assert fused_rows[0][1] == "SQLite 高并发多开物理隔离终极架构"
    assert fused_rows[0][2] == 2, f"Expected 2 but got {fused_rows[0][2]}, row details: {fused_rows[0]}"
    
    rev_hist = json.loads(fused_rows[0][3])
    assert "深夜做梦全局知识熔炼合成" in rev_hist[-1]["reason"]


def test_rag_version_multiplier(temp_workspace):
    """5. 验证 RAG 检索版本加权对高版本高精度事实的优先召回."""
    db_dir = temp_workspace / "rag_sandbox"
    db_dir.mkdir()
    
    manager = MemoryManager(base_dir=db_dir)
    manager.backup_dir = db_dir / "backup"
    db = manager._get_db()
    
    # 插入两条除版本号之外其他极其相近的记录
    # KI 1：版本 1
    manager.save_ki({
        "id": "ki_version_1",
        "title": "LiteLLM 鉴权指南",
        "category": "xl_debugging",
        "keywords": ["LiteLLM", "API"],
        "summary": "版本1",
        "content": "LiteLLM 的基础说明"
    })
    
    # KI 2：版本 10 (模拟历经 10 次迭代熔炼的高精纯条目)
    manager.save_ki({
        "id": "ki_version_10",
        "title": "LiteLLM 鉴权指南",
        "category": "xl_debugging",
        "keywords": ["LiteLLM", "API"],
        "summary": "版本10",
        "content": "LiteLLM 的基础说明"
    })
    
    # 手动强行修改 version
    db.execute("UPDATE knowledge_items SET version = 1 WHERE id = 'ki_version_1'")
    db.execute("UPDATE knowledge_items SET version = 10 WHERE id = 'ki_version_10'")
    db.commit()
    
    # 计算数学期望加权
    # version_1_mul = 1.0 + 0.05 * log(1) = 1.0
    # version_10_mul = 1.0 + 0.05 * log(10) = 1.0 + 0.05 * 2.3025 = 1.115
    # 高版本的分数会明显被乘大
    
    # 为两条记录提取和保存完全一致的向量，使其向量得分完全一致
    embedding_str = json.dumps([0.1] * 768)
    db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_version_1", embedding_str))
    db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_version_10", embedding_str))
    db.commit()
    
    # Mock RAG embedding query
    manager._get_embedding = AsyncMock(return_value=[0.1] * 768)
    
    # 触发检索
    res = manager.search_memories("LiteLLM 鉴权指南", limit=5)
    
    # 结果第一名必须是高版本的 ki_version_10
    assert len(res) >= 2
    assert res[0]["filename"] == "ki_ki_version_10.md"
    assert res[1]["filename"] == "ki_ki_version_1.md"
