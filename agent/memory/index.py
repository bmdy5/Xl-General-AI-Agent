import os
import re
import time
import math
import sqlite3
import logging
import asyncio
import random
import functools
import inspect
from pathlib import Path
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("agent.memory.index")

# 代理导入以保持向下兼容与 100% 外界零感知
from .cache import MemoryCache
from .embedding import _get_embedding, save_ki_embedding

def with_db_retry(max_retries: int = 5, base_delay: float = 0.01):
    """自适应指数退避延迟重试装饰器，应对 SQLite 排他写锁 database is locked 的并发高吞吐"""
    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                attempt = 0
                while True:
                    try:
                        return await func(*args, **kwargs)
                    except sqlite3.OperationalError as e:
                        err_msg = str(e).lower()
                        if "locked" in err_msg and attempt < max_retries:
                            attempt += 1
                            delay = base_delay * (2 ** attempt) + random.uniform(0.005, 0.02)
                            logger.warning(f"⚠️ [SQLite 写锁碰撞] 数据库写锁碰撞 (locked)，第 {attempt}/{max_retries} 次指数退避，延迟 {delay*1000:.1f}ms 后重试...")
                            await asyncio.sleep(delay)
                        else:
                            raise e
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                attempt = 0
                while True:
                    try:
                        return func(*args, **kwargs)
                    except sqlite3.OperationalError as e:
                        err_msg = str(e).lower()
                        if "locked" in err_msg and attempt < max_retries:
                            attempt += 1
                            delay = base_delay * (2 ** attempt) + random.uniform(0.005, 0.02)
                            logger.warning(f"⚠️ [SQLite 写锁碰撞] 数据库同步写锁碰撞 (locked)，第 {attempt}/{max_retries} 次指数退避，延迟 {delay*1000:.1f}ms 后重试...")
                            time.sleep(delay)
                        else:
                            raise e
            return sync_wrapper
    return decorator

def _get_db(manager) -> sqlite3.Connection:
    """惰性初始化 SQLite + FTS5 索引，自动无损升级老数据为 CJK 高精度索引."""
    if manager._db is None:
        db_path = manager.base_dir / "memories.db"
        is_new = not db_path.exists()
        
        # 物理快照双重防御准备
        bak_path = manager.base_dir / "memories.db.bak"
        if not is_new and db_path.exists():
            import shutil
            try:
                shutil.copy2(str(db_path), str(bak_path))
            except Exception as backup_err:
                logger.warning(f"Failed to create pre-upgrade physical database backup: {backup_err}")
        
        try:
            manager._db = sqlite3.connect(str(db_path), timeout=1.0)
            
            # 主动进行 integrity_check 与 vtable 构造白盒探测，自愈物理损坏与 malformed 错误
            if not is_new:
                try:
                    # 极其轻量的完整性测试
                    manager._db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
                    # 针对 FTS 虚拟表 vtable 故障进行特异性测试，先检测表是否存在，防止误判
                    cur_check = manager._db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kis_fts'")
                    if cur_check.fetchone():
                        manager._db.execute("SELECT 1 FROM kis_fts LIMIT 1").fetchall()
                except sqlite3.DatabaseError as integrity_err:
                    logger.critical(f"🚨 [DB探测] 物理数据库或虚拟表损坏: {integrity_err}！触发原地隔离自愈流程...")
                    raise integrity_err
            
            manager._db.execute("PRAGMA foreign_keys = ON")
            manager._db.execute("PRAGMA busy_timeout = 1000")
            manager._db.execute("PRAGMA journal_mode = WAL")
            
            from .fts_index import create_table
            create_table(manager._db)
            
            # 自动创建长期大脑关系表 knowledge_items
            manager._db.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    keywords TEXT NOT NULL,       -- JSON 字符串
                    summary TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,     -- ISO-8601 UTC
                    updated_at TEXT NOT NULL,
                    last_hit_at TEXT NOT NULL,
                    visit_count INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1,
                    revision_history TEXT
                )
            """)
            
            # 探查并热迁移已有表结构，确保自愈
            if not is_new:
                cur = manager._db.execute("PRAGMA table_info(knowledge_items)")
                columns = [row[1] for row in cur.fetchall()]
                
                if "version" not in columns or "revision_history" not in columns or "ki_type" not in columns:
                    manager._db.execute("BEGIN TRANSACTION")
                    try:
                        if "version" not in columns:
                            manager._db.execute("ALTER TABLE knowledge_items ADD COLUMN version INTEGER DEFAULT 1")
                            logger.info("Added 'version' column to knowledge_items.")
                        if "revision_history" not in columns:
                            manager._db.execute("ALTER TABLE knowledge_items ADD COLUMN revision_history TEXT")
                            logger.info("Added 'revision_history' column to knowledge_items.")
                        if "ki_type" not in columns:
                            manager._db.execute("ALTER TABLE knowledge_items ADD COLUMN ki_type TEXT DEFAULT 'ki'")
                            logger.info("Added 'ki_type' column to knowledge_items.")
                        manager._db.commit()
                    except Exception as tx_err:
                        manager._db.execute("ROLLBACK")
                        raise tx_err
            
            # 创建高精度 CJK 知识全文检索虚拟表
            manager._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS kis_fts
                USING fts5(ki_id, title, category, keywords, summary, content,
                           tokenize="porter unicode61")
            """)
            # 创建短期记忆实时持久化表 active_sessions
            manager._db.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    session_key TEXT PRIMARY KEY,
                    messages TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # 创建 768维中文增强语义向量库表
            manager._db.execute("""
                CREATE TABLE IF NOT EXISTS ki_embeddings (
                    ki_id TEXT PRIMARY KEY,
                    embedding TEXT NOT NULL,
                    FOREIGN KEY(ki_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
                )
            """)
            manager._db.commit()
            
            # 升级成功，清理临时物理备份
            if bak_path.exists():
                try:
                    bak_path.unlink()
                except Exception:
                    pass
                    
        except sqlite3.DatabaseError as db_err:
            db_err_msg = str(db_err).lower()
            if "malformed" in db_err_msg or "vtable" in db_err_msg or "corrupt" in db_err_msg:
                logger.critical(f"🚨 [DB自愈] 确认数据库物理损坏: {db_err}。正在对坏库实施物理热隔离...")
                if manager._db is not None:
                    try:
                        manager._db.close()
                    except Exception:
                        pass
                    manager._db = None
                
                # 强力重命名隔离损坏的主库，包含所有的 WAL、SHM 伴生文件
                import time
                ts = int(time.time())
                for ext in ["", "-wal", "-shm", ".bak"]:
                    p = Path(str(db_path) + ext)
                    if p.exists():
                        try:
                            p.rename(Path(str(p) + f".malformed.{ts}"))
                        except Exception as rename_err:
                            logger.error(f"❌ [DB自愈] 坏库文件 {p.name} 隔离重命名失败: {rename_err}")
                
                logger.critical("🎉 [DB自愈] 坏库已全线物理隔离。尝试备份库回卷还原...")
                
                # 检查项目沙箱备份库是否也损坏，如果不损坏，尝试回卷备份库作为 0ms 瞬间还原
                backup_db_path = manager.backup_dir / "memories.db"
                if backup_db_path.exists():
                    import shutil
                    try:
                        # 尝试验证备份库的健康度
                        test_conn = sqlite3.connect(str(backup_db_path))
                        test_conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
                        # 测试备份库里的虚拟表是否完全健康
                        try:
                            test_conn.execute("SELECT 1 FROM kis_fts LIMIT 1").fetchall()
                            is_backup_healthy = True
                        except sqlite3.DatabaseError:
                            is_backup_healthy = False
                        test_conn.close()
                        
                        if is_backup_healthy:
                            manager.base_dir.mkdir(parents=True, exist_ok=True)
                            for ext in ["", "-wal", "-shm"]:
                                src = Path(str(backup_db_path) + ext)
                                if src.exists():
                                    shutil.copy2(str(src), str(manager.base_dir / (src.name)))
                            logger.critical("✨ [DB自愈] 极度幸运！项目沙箱备份库完全健康，已成功实现 0ms 完美物理回卷自愈还原！")
                        else:
                            logger.error("❌ [DB自愈] 备份库测试失败，虚拟表已损坏，跳过物理回卷。")
                    except Exception as backup_test_err:
                        logger.error(f"❌ [DB自愈] 备份库连接或验证失败: {backup_test_err}。跳过物理回卷。")
                        try:
                            test_conn.close()
                        except Exception:
                            pass
                
                # 重新递归调用 _get_db 重建新库结构
                manager._db = None
                return _get_db(manager)
            
            # 向上抛出其他 DatabaseError
            raise db_err
            
        except Exception as upgrade_err:
            logger.critical(f"🚨 [DB自愈] 数据库 DDL 热升级失败: {upgrade_err}. 启动物理自愈快照还原...")
            if manager._db is not None:
                try:
                    manager._db.close()
                except Exception:
                    pass
                manager._db = None
            
            if bak_path.exists():
                try:
                    import shutil
                    shutil.copy2(str(bak_path), str(db_path))
                    logger.info("🎉 [DB自愈] 物理自愈快照还原成功！")
                except Exception as restore_err:
                    logger.error(f"❌ [DB自愈] 物理快照还原失败: {restore_err}")
                finally:
                    try:
                        bak_path.unlink()
                    except Exception:
                        pass
            
            # 重新安全建立旧版数据库连接并返回
            manager._db = sqlite3.connect(str(db_path), timeout=1.0)
            manager._db.execute("PRAGMA foreign_keys = ON")
            manager._db.execute("PRAGMA busy_timeout = 1000")
            manager._db.execute("PRAGMA journal_mode = WAL")
        
        if not is_new:
            try:
                cur = manager._db.execute("SELECT content FROM memories_fts LIMIT 50")
                has_legacy = False
                for row in cur:
                    content = row[0]
                    if content and re.search(r'[\u4e00-\u9fff]{2,}', content):
                        has_legacy = True
                        break
                if has_legacy:
                    logger.info("Upgrading legacy memories_fts database for CJK space indexing...")
                    manager._db.execute("DELETE FROM memories_fts")
                    manager._db.commit()
                    
                    entries = manager._parse_index()
                    rows_to_populate = []
                    for e in entries:
                        fname = e.get("filename", "")
                        filepath = manager.base_dir / fname
                        if filepath.exists():
                            try:
                                content = filepath.read_text(encoding="utf-8")
                                mtype = "merged"
                                if "user" in e["description"].lower() or "亮哥" in e["description"]:
                                    mtype = "user"
                                elif "feedback" in e["description"].lower():
                                    mtype = "feedback"
                                rows_to_populate.append({
                                    "content": content[:5000],
                                    "description": e["description"][:200],
                                    "memory_type": mtype,
                                    "filename": fname,
                                    "timestamp": e.get("timestamp", ""),
                                })
                            except Exception:
                                continue
                    if rows_to_populate:
                        from .fts_index import populate as fts_populate
                        fts_populate(manager._db, rows_to_populate)
                        manager._db.commit()
                        logger.info(f"Successfully upgraded {len(rows_to_populate)} memory files to CJK indexes!")
            except Exception as e:
                logger.warning(f"Error upgrading memories_fts: {e}")
    return manager._db


def _upsert_index(manager, filename: str, new_line: str):
    """Update index: replace existing or append new."""
    if manager.index_file.exists():
        existing = manager.index_file.read_text(encoding="utf-8")
        pattern = re.compile(rf"^- \[.*\]\({re.escape(filename)}\)")
        if pattern.search(existing):
            new_text = pattern.sub(new_line, existing)
            manager.index_file.write_text(new_text, encoding="utf-8")
            return
        with open(manager.index_file, "a", encoding="utf-8") as f:
            f.write(new_line + "\n")
    else:
        manager.index_file.write_text(f"# Memory Index\n\n{new_line}\n", encoding="utf-8")


def _parse_index(manager) -> list[dict]:
    """Parse MEMORY.md into list of entries."""
    if not manager.index_file.exists():
        return []
    entries = []
    for line in manager.index_file.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if not line.startswith("- ["):
            continue
        m = re.match(r"- \[(.+?)\]\((.+?\.md)\)(?:\s*`([^`]+)`)?", line)
        if m:
            entries.append({
                "description": m.group(1),
                "filename": m.group(2),
                "timestamp": m.group(3) or "",
            })
    return entries
