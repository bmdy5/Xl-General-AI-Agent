import pytest
import sqlite3
import json
import shutil
import os
from pathlib import Path
from agent.memory.manager import MemoryManager


@pytest.fixture
def migration_sandbox(tmp_path):
    """构建独立的老库与新库隔离测试沙箱环境."""
    sandbox = tmp_path / "migration_sandbox"
    sandbox.mkdir()
    
    # 1. 建立老物理根目录和新哈希隔离物理目录
    old_base = sandbox / "old_memory_root"
    old_base.mkdir()
    
    new_base = sandbox / "new_memory_root" / "1705919142"
    new_base.mkdir(parents=True, exist_ok=True)
    
    # 2. 物理创建一个不带 version 和 revision_history 字段的老旧 memories.db
    old_db_path = old_base / "memories.db"
    conn = sqlite3.connect(str(old_db_path))
    
    # 长期大脑表 (旧表结构，无 version, revision_history)
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
    
    # 模拟插入两条老主条目
    conn.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ki_old_1", 
        "老亮哥爱心偏好", 
        "user", 
        '["亮哥", "偏好"]', 
        "老的偏好描述", 
        "亮哥非常喜欢小萤傲娇的样子！", 
        "2026-05-23T12:00:00Z", 
        "2026-05-23T12:00:00Z", 
        "2026-05-23T12:00:00Z"
    ))
    conn.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ki_old_2", 
        "老沟通技巧", 
        "communication", 
        '["沟通"]', 
        "老的沟通记录", 
        "对话中多用撒娇情绪词汇。", 
        "2026-05-23T12:10:00Z", 
        "2026-05-23T12:10:00Z", 
        "2026-05-23T12:10:00Z"
    ))
    
    # 向量表
    conn.execute("""
        CREATE TABLE ki_embeddings (
            ki_id TEXT PRIMARY KEY,
            embedding TEXT NOT NULL
        )
    """)
    conn.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_old_1", json.dumps([0.1]*768)))
    conn.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_old_2", json.dumps([0.2]*768)))
    
    # 全文检索表 memories_fts
    conn.execute("""
        CREATE VIRTUAL TABLE memories_fts
        USING fts5(content, description, memory_type, filename, timestamp)
    """)
    conn.execute("""
        INSERT INTO memories_fts (content, description, memory_type, filename, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (" 亮 哥 喜 欢 傲 娇 ", "亮哥偏好描述", "user", "reflect_legacy_pref.md", "2026-05-23T12:00:00Z"))
    
    conn.commit()
    conn.close()
    
    # 3. 物理构建老的 Markdown 文件碎片及老的 MEMORY.md
    old_index = old_base / "MEMORY.md"
    old_index.write_text("""# Memory Index

- [亮哥偏好描述](reflect_legacy_pref.md) `2026-05-23T12:00:00Z`
""", encoding="utf-8")
    
    old_pref_file = old_base / "reflect_legacy_pref.md"
    old_pref_file.write_text("亮哥非常喜欢小萤傲娇的样子！", encoding="utf-8")
    
    # 物理构建老的核心文件 user_profile.md (有独特的 ### 段落)
    old_core_file = old_base / "user_profile.md"
    old_core_file.write_text("""# User Profile

---
### 亮哥日常习惯
亮哥习惯在深夜进行代码调试。

---
### 沟通人设
小萤要对亮哥保持温柔！
""", encoding="utf-8")
    
    # 4. 在新库中创建自己的 user_profile.md 核心文件，包含一部分重合的，一部分独特的段落
    new_core_file = new_base / "user_profile.md"
    new_core_file.write_text("""# User Profile

---
### 亮哥日常习惯
亮哥习惯在深夜进行代码调试。

---
### 最新模型偏好
亮哥推荐使用 deepseek 极速模型。
""", encoding="utf-8")
    
    # 新库自己已有的新 memories.db (含有 1 条新纪录，证明多实例隔离成功)
    new_db_path = new_base / "memories.db"
    new_conn = sqlite3.connect(str(new_db_path))
    new_conn.execute("""
        CREATE TABLE knowledge_items (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, 
            keywords TEXT NOT NULL, summary TEXT NOT NULL, content TEXT NOT NULL, 
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_hit_at TEXT NOT NULL, 
            visit_count INTEGER DEFAULT 0, version INTEGER DEFAULT 1, revision_history TEXT
        )
    """)
    new_conn.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "ki_new_1", "新隔离条目", "general", '["新"]', "最新摘要", 
        "这是新哈希隔离库里的灵魂记忆。", "2026-05-24T00:00:00Z", "2026-05-24T00:00:00Z", "2026-05-24T00:00:00Z"
    ))
    
    new_conn.execute("""
        CREATE TABLE ki_embeddings (
            ki_id TEXT PRIMARY KEY,
            embedding TEXT NOT NULL
        )
    """)
    new_conn.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", ("ki_new_1", json.dumps([0.9]*768)))
    
    new_conn.execute("""
        CREATE VIRTUAL TABLE memories_fts
        USING fts5(content, description, memory_type, filename, timestamp)
    """)
    new_conn.commit()
    new_conn.close()
    
    return {
        "sandbox": sandbox,
        "old_base": old_base,
        "new_base": new_base,
        "old_db_path": old_db_path,
        "new_db_path": new_db_path
    }


def test_memory_seamless_migration(migration_sandbox):
    """物理验证老旧无隔离数据库及物理 Markdown 文件至新隔离库的 100% 无损热迁移熔接."""
    old_base = migration_sandbox["old_base"]
    new_base = migration_sandbox["new_base"]
    
    # 1. 物理 Mock settings 的 memory 配置，使其执行时锁定我们的沙箱
    from agent.core.config import settings
    settings._data["memory"] = {
        "base_dir": str(old_base), # 使没有隔离的老旧温床定位在此
        "backup_dir": str(old_base.parent / "backup"),
        "multi_instance_isolation": True
    }
    
    # 2. 用 Mock 的 settings 初始化 MemoryManager
    # 因为 MemoryManager 初始化中会在 base_dir 后追加 admin_id (默认 1705919142)
    # 使得 manager.base_dir 会变为 old_base / "1705919142"
    # 我们为了能让迁移引擎准确识别“外部无隔离的老库 memories.db”，需要在 manager 中能够自适应计算出老库的主路径
    manager = MemoryManager(base_dir=new_base)
    
    # 手动强行修改 manager 中的老旧无隔离 base 路径，使 TDD 能准确识别
    # 实际上在 manager.py 的实现中，我们会自动向上解析 parent 或通过 settings 重新计算老根路径
    # 我们可以通过 mock 或者是真实代码的路径提取方式。
    # 这里我们断言迁移是否成功触发
    
    # 在 manager.__init__ 时会默认触发迁移检测。由于我们在 manager.py 还没写，我们现在在此处手动执行我们即将编写的方法
    # 这就是完美的 TDD
    if hasattr(manager, "_run_hot_migration_if_needed"):
        manager._run_hot_migration_if_needed(old_base_dir_override=old_base)
    else:
        # 如果还没实现，在此直接让测试通过或跑出失败以证明 TDD
        # 我们现在去修改 manager.py 并提供该方法，在此我们先断言
        pass

    # A. 校验数据库合并
    db = manager._get_db()
    
    # 1. 验证新库和老库的数据完美熔接
    cur = db.execute("SELECT id, title, version FROM knowledge_items ORDER BY id")
    rows = cur.fetchall()
    
    # 应当有 3 条记录: ki_new_1, ki_old_1, ki_old_2
    assert len(rows) == 3
    assert rows[0][0] == "ki_new_1"
    assert rows[1][0] == "ki_old_1"
    assert rows[2][0] == "ki_old_2"
    
    # 2. 验证合并后的记录已经对齐了表结构，且老数据 version 自动为 1
    assert rows[1][2] == 1
    
    # 3. 验证 ki_embeddings 完美合并
    cur_emb = db.execute("SELECT ki_id FROM ki_embeddings ORDER BY ki_id")
    emb_rows = cur_emb.fetchall()
    assert len(emb_rows) == 3
    
    # 4. 验证 memories_fts 全文检索去重导入
    cur_fts = db.execute("SELECT filename FROM memories_fts")
    fts_rows = cur_fts.fetchall()
    # 应该包含老库的 reflect_legacy_pref.md
    filenames = [r[0] for r in fts_rows]
    assert "reflect_legacy_pref.md" in filenames

    # B. 校验物理 Markdown 碎片的拷贝与 core 文件的合并去重
    
    # 1. 普通碎片文件应该被成功拷贝到新目录
    new_pref_file = new_base / "reflect_legacy_pref.md"
    assert new_pref_file.exists()
    assert new_pref_file.read_text(encoding="utf-8") == "亮哥非常喜欢小萤傲娇的样子！"
    
    # 2. 核心文件 user_profile.md 应该实现微米级的分段去重原子合并
    new_core_file = new_base / "user_profile.md"
    assert new_core_file.exists()
    core_content = new_core_file.read_text(encoding="utf-8")
    
    # 包含老 core 文件中独有的：
    assert "沟通人设" in core_content
    assert "小萤要对亮哥保持温柔！" in core_content
    
    # 包含新 core 文件中独有的：
    assert "最新模型偏好" in core_content
    assert "亮哥推荐使用 deepseek 极速模型。" in core_content
    
    # 并且，只出现了一次亮哥日常习惯（去重）：
    assert core_content.count("亮哥日常习惯") == 1
    assert core_content.count("亮哥习惯在深夜进行代码调试") == 1

    # 3. 索引 MEMORY.md 完美合并去重
    new_index_file = new_base / "MEMORY.md"
    assert new_index_file.exists()
    index_content = new_index_file.read_text(encoding="utf-8")
    assert "reflect_legacy_pref.md" in index_content

    # C. 校验物理归档重命名，消灭二次搬家
    
    old_db_migrated = old_base / "memories.db.migrated"
    assert old_db_migrated.exists()
    assert not (old_base / "memories.db").exists()
    
    # 老的 md 文件也被标记为了 migrated
    assert (old_base / "reflect_legacy_pref.md.migrated").exists()
    assert not (old_base / "reflect_legacy_pref.md").exists()
    
    # 老的 MEMORY.md 也被重命名为 migrated
    assert (old_base / "MEMORY.md.migrated").exists()
    assert not (old_base / "MEMORY.md").exists()
