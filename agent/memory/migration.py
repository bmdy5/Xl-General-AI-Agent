import logging
import sqlite3
import shutil
import json
import hashlib
import re
from pathlib import Path
from typing import Optional
from agent.core.config import settings
from .store import CORE_FILES

logger = logging.getLogger("agent.memory.manager")

def _run_hot_migration_if_needed(manager, old_base_dir_override: Optional[Path] = None):
    """老旧无隔离数据库及 Markdown 碎片到新多实例哈希隔离库的 100% 无损热平滑迁移引擎"""
    # 1. 确定老旧无隔离库的物理根路径
    memory_cfg = settings.get("memory") or {}
    
    if old_base_dir_override:
        old_base_dir = Path(old_base_dir_override)
    else:
        base_dir_str = memory_cfg.get("base_dir", "~/.my-agent/memory")
        old_base_dir = manager.resolve_adaptive_path(base_dir_str)
        
    old_db = old_base_dir / "memories.db"
    
    # 如果老旧无隔离目录就是当前隔离 base，或者老库根本不存在，或者老库被重命名为了 migrated，则安全退回
    try:
        if not old_db.exists() or old_db.resolve() == (manager.base_dir / "memories.db").resolve():
            return
    except Exception:
        return
        
    logger.info(f"🚚 [热迁移开始] 检测到未搬家的老旧无隔离记忆库: {old_db}。拉起无损热熔合引擎！")
    
    # A. 第一步：老库 DDL 列结构自愈热对齐
    try:
        conn_old = sqlite3.connect(str(old_db), timeout=5.0)
        cur = conn_old.execute("PRAGMA table_info(knowledge_items)")
        columns = [row[1] for row in cur.fetchall()]
        
        if columns:
            need_commit = False
            if "version" not in columns:
                conn_old.execute("ALTER TABLE knowledge_items ADD COLUMN version INTEGER DEFAULT 1")
                need_commit = True
                logger.info("Successfully added 'version' column to old legacy knowledge_items table.")
            if "revision_history" not in columns:
                conn_old.execute("ALTER TABLE knowledge_items ADD COLUMN revision_history TEXT")
                need_commit = True
                logger.info("Successfully added 'revision_history' column to old legacy knowledge_items table.")
            if need_commit:
                conn_old.commit()
        conn_old.close()
    except Exception as ddl_err:
        logger.warning(f"Failed to check/align DDL on legacy db: {ddl_err}. Proceeding with best-effort ATTACH merge...")

    # B. 第二步：双旧库 ATTACH 原子热熔合合并
    new_db_path = manager.base_dir / "memories.db"
    
    # 确保新库在 merge 前已经进行了表的 DDL 初始化，这可通过触发一次 _get_db() 自动完成
    new_conn = manager._get_db()
    
    try:
        new_conn.execute(f"ATTACH DATABASE '{str(old_db)}' AS old_db")
        new_conn.execute("BEGIN TRANSACTION")
        
        # 1. 增量或覆盖合并长期记忆 knowledge_items
        new_conn.execute("""
            INSERT OR IGNORE INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version, revision_history)
            SELECT id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version, revision_history FROM old_db.knowledge_items
        """)
        
        # 2. 增量合并向量库 ki_embeddings
        new_conn.execute("""
            INSERT OR IGNORE INTO ki_embeddings (ki_id, embedding)
            SELECT ki_id, embedding FROM old_db.ki_embeddings
        """)
        
        # 3. 增量合并全文检索表 memories_fts，排重避免重复导入
        new_conn.execute("""
            INSERT INTO memories_fts (content, description, memory_type, filename, timestamp)
            SELECT content, description, memory_type, filename, timestamp FROM old_db.memories_fts
            WHERE filename NOT IN (SELECT filename FROM memories_fts)
        """)
        
        new_conn.commit()
        logger.info("✨ [热迁移 - 数据库] 双库 SQLite 数据无损原子熔接导入成功！")
    except Exception as merge_err:
        try:
            new_conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error(f"❌ [热迁移 - 数据库] 双库 ATTACH 原子合并失败: {merge_err}")
    finally:
        try:
            new_conn.execute("DETACH DATABASE old_db")
        except Exception:
            pass

    # C. 第三步：物理 Markdown 碎片拷贝与核心记忆微米级合并
    old_index_file = old_base_dir / "MEMORY.md"
    
    try:
        # 遍历老目录下的所有文件并搬家
        for src_file in old_base_dir.iterdir():
            if not src_file.is_file():
                continue
            filename = src_file.name
            
            if filename in ("memories.db", "memories.db.bak", "MEMORY.md", "routing_rules.md") or filename.endswith(".migrated"):
                continue
            
            # 判断是否为 core 记忆文件
            if filename in CORE_FILES:
                dest_file = manager.base_dir / filename
                if dest_file.exists():
                    try:
                        old_text = src_file.read_text(encoding="utf-8")
                        new_text = dest_file.read_text(encoding="utf-8")
                        
                        old_blocks = old_text.split("###")
                        new_blocks = new_text.split("###")
                        
                        seen_hashes = set()
                        for b in new_blocks[1:]:
                            content_cleaned = b.strip()
                            if content_cleaned:
                                h = hashlib.md5(content_cleaned.encode("utf-8")).hexdigest()
                                seen_hashes.add(h)
                                
                        merged_text = new_text.rstrip()
                        
                        for b in old_blocks[1:]:
                            content_cleaned = b.strip()
                            if content_cleaned:
                                h = hashlib.md5(content_cleaned.encode("utf-8")).hexdigest()
                                if h not in seen_hashes:
                                    seen_hashes.add(h)
                                    merged_text += f"\n\n---\n### {b.strip()}"
                                    
                        dest_file.write_text(merged_text + "\n", encoding="utf-8")
                        logger.info(f"💎 [微米级合并] 成功去重熔接核心记忆文件: {filename}")
                    except Exception as core_merge_err:
                        logger.warning(f"Failed to merge core file {filename}: {core_merge_err}, falling back to direct copy")
                        shutil.copy2(src_file, dest_file)
                else:
                    shutil.copy2(src_file, dest_file)
            else:
                dest_file = manager.base_dir / filename
                shutil.copy2(src_file, dest_file)
                
        # 2. 解析老 MEMORY.md 索引并与新 MEMORY.md 合并去重
        if old_index_file.exists():
            old_entries = []
            for line in old_index_file.read_text(encoding="utf-8").split("\n"):
                line = line.strip()
                if not line.startswith("- ["):
                    continue
                m = re.match(r"- \[(.+?)\]\((.+?\.md)\)(?:\s*`([^`]+)`)?", line)
                if m:
                    old_entries.append({
                        "line": line,
                        "desc": m.group(1),
                        "fname": m.group(2)
                    })
            
            new_fnames = set()
            if manager.index_file.exists():
                for line in manager.index_file.read_text(encoding="utf-8").split("\n"):
                    line = line.strip()
                    if not line.startswith("- ["):
                        continue
                    m = re.match(r"- \[(.+?)\]\((.+?\.md)\)(?:\s*`([^`]+)`)?", line)
                    if m:
                        new_fnames.add(m.group(2))
            
            for entry in old_entries:
                if entry["fname"] not in new_fnames:
                    manager._upsert_index(entry["fname"], entry["line"])
        
        logger.info("✨ [热迁移 - 物理文件] 所有的 Markdown 碎片及核心记忆熔接去重完成！")
    except Exception as file_err:
        logger.error(f"❌ [热迁移 - 物理文件] 碎片文件及索引搬运合并失败: {file_err}")

    # D. 第四步：老库及其资产物理重命名归档，终结迁移
    try:
        if old_db.exists():
            old_db.rename(old_base_dir / "memories.db.migrated")
        
        if old_index_file.exists():
            old_index_file.rename(old_base_dir / "MEMORY.md.migrated")
            
        for p in old_base_dir.iterdir():
            if p.is_file() and p.name != "memories.db.migrated" and not p.name.endswith(".migrated") and p.name != "routing_rules.md":
                p.rename(old_base_dir / f"{p.name}.migrated")
                
        logger.info(f"🎉 [热迁移完成] 灵魂搬家全部收尾！老旧无隔离目录已全部归档为 .migrated。")
    except Exception as cleanup_err:
        logger.warning(f"Failed to archive old database files: {cleanup_err}")
        
    manager._mem_cache.invalidate_all()
    manager._note_cache.invalidate_all()
