"""Memory system - Facade class.

所有的具体数据库、物理IO、中文增强语义检索等高内聚逻辑已被解耦物理拆分至：
- store.py
- index.py
- ki.py
- context.py
- session.py
"""

import logging
import asyncio
import os
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# 导入拆分出的缓存类
from .index import MemoryCache

# 导入所有常量与辅助类以维持 100 percent 向下兼容
from .store import PROTECTED_FILES, CORE_FILES, get_default_routing_rules

class MemoryManager:
    """长期记忆与自学习大脑 — 外观 Facade 编排层."""

    @staticmethod
    def resolve_adaptive_path(path_str: str) -> Path:
        """自适应路径解析引擎：支持 ~ 展开为 Home 目录，以及相对项目根目录 ./ 的动态寻址"""
        if path_str.startswith("~"):
            return Path(os.path.expanduser(path_str))
        if path_str.startswith("./") or path_str.startswith("../") or not path_str.startswith("/"):
            root = Path(__file__).resolve().parents[2]
            return (root / path_str).resolve()
        return Path(path_str).resolve()

    def __init__(self, base_dir: Optional[Union[str, Path]] = None):
        from agent.core.config import settings
        
        # 1. 提取 settings 配置
        memory_cfg = settings.get("memory") or {}
        sec_cfg = settings.get("security") or {}
        admin_id = os.getenv("QQ_ADMIN_ID", sec_cfg.get("admin_id", "1705919142"))
        
        # 2. 定位基础物理路径 (默认 "~/.my-agent/memory")
        base_dir_str = memory_cfg.get("base_dir", "~/.my-agent/memory")
        
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = self.resolve_adaptive_path(base_dir_str)
            if memory_cfg.get("multi_instance_isolation", True) and admin_id:
                self.base_dir = self.base_dir / admin_id

        # 3. 定位项目内的自封包备份路径 (默认 "./.memory")
        backup_dir_str = memory_cfg.get("backup_dir", "./.memory")
        self.backup_dir = self.resolve_adaptive_path(backup_dir_str)
        if admin_id:
            self.backup_dir = self.backup_dir / admin_id

        self.auto_backup = memory_cfg.get("auto_backup_to_project", True)

        # 4. 逆向自愈还原引擎：主目录空但备份目录存在，判定为“换家”，自动在后台无缝复制还原！
        db_file = self.base_dir / "memories.db"
        backup_db_file = self.backup_dir / "memories.db"
        
        if not db_file.exists() and backup_db_file.exists():
            try:
                import shutil
                self.base_dir.mkdir(parents=True, exist_ok=True)
                for src_path in self.backup_dir.iterdir():
                    if src_path.is_file():
                        shutil.copy2(src_path, self.base_dir / src_path.name)
                logger.info(f"✨ [自愈还原] 成功从项目沙箱备份 ({self.backup_dir.name}) 中一键还原了小萤的所有记忆资产！")
            except Exception as restore_err:
                logger.error(f"Failed to reverse restore memory backup: {restore_err}")

        # 5. 强力创建主物理目录
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base_dir / "MEMORY.md"
        self.rules_file = self.base_dir / "routing_rules.md"
        if not self.rules_file.exists():
            self.rules_file.write_text(get_default_routing_rules(), encoding="utf-8")
        self._db = None
        self._mem_cache = MemoryCache(capacity=200, ttl=300)
        self._note_cache = MemoryCache(capacity=200, ttl=300)
        self._vector_cache = {}
        self._debounce_tasks = {}
        
        # 6. 主动触发老旧无隔离数据库到多实例隔离新库的平滑无损热迁移
        self._run_hot_migration_if_needed()

    def trigger_backup(self):
        """异步防抖热双写备份引擎：通过官方 SQLite backup() API 或 copy 进行数据同步"""
        if not self.auto_backup:
            return
            
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        async def _async_backup():
            try:
                import sqlite3
                import shutil
                db_path = self.base_dir / "memories.db"
                backup_db_path = self.backup_dir / "memories.db"
                
                # 1. 事务级 SQLite API 异步备份，延迟 1.5s 以防爆写锁冲突
                if db_path.exists():
                    await asyncio.sleep(1.5)
                    try:
                        src_conn = sqlite3.connect(str(db_path))
                        dest_conn = sqlite3.connect(str(backup_db_path))
                        with dest_conn:
                            src_conn.backup(dest_conn)
                        src_conn.close()
                        dest_conn.close()
                    except Exception as backup_api_err:
                        logger.warning(f"SQLite backup() API failed, falling back to copy: {backup_api_err}")
                        shutil.copy2(db_path, backup_db_path)
                
                # 2. 物理同步核心 Markdown 记忆文件与索引
                for src_file in self.base_dir.iterdir():
                    if src_file.is_file() and src_file.suffix == ".md":
                        shutil.copy2(src_file, self.backup_dir / src_file.name)
                logger.info(f"💾 [热备份成功] 小萤的灵魂记忆已安全双写同步至项目沙箱: {self.backup_dir}")
            except Exception as backup_err:
                logger.warning(f"Failed to backup memory database: {backup_err}")
                
        # 安全创建异步 Task，规避同步/单元测试环境无 running event loop 时的崩溃
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_async_backup())
        except RuntimeError:
            pass

    # ── 1. index.py 代理 ────────────────────────────────────────

    def _get_db(self):
        from .index import _get_db
        return _get_db(self)

    async def load_active_session(self, session_key: str) -> list:
        """从 SQLite 数据库 active_sessions 中安全加载当前会话消息列表."""
        import json
        db = self._get_db()
        try:
            cur = db.execute(
                "SELECT messages FROM active_sessions WHERE session_key = ?",
                (session_key,)
            )
            row = cur.fetchone()
            if row:
                messages_str = row[0]
                try:
                    return json.loads(messages_str)
                except Exception as parse_err:
                    logger.error(f"Failed to parse active_session json for {session_key}: {parse_err}")
                    return []
        except Exception as e:
            logger.error(f"Failed to load active session from DB for {session_key}: {e}")
        return []

    def save_active_session_async(self, session_key: str, messages: list):
        """非阻塞式异步内存防抖刷盘。消息先保留在内存，1.0秒防抖延迟后一次性写入SQLite。"""
        import json
        from datetime import datetime, timezone
        
        # 1. 序列化消息数据以快照保存，规避协程挂起期间 messages 列表被后方修改导致的数据同步不一致
        try:
            serialized_msgs = json.dumps(messages)
        except Exception as ser_err:
            logger.error(f"Failed to serialize messages for {session_key}: {ser_err}")
            return

        # 2. 强行取消当前 session_key 正在等待的旧 Task (Debounce 去重)
        old_task = self._debounce_tasks.get(session_key)
        if old_task and not old_task.done():
            old_task.cancel()

        # 3. 创建全新的防抖物理写入 Task
        async def _do_debounce_write():
            try:
                await asyncio.sleep(1.0)
                db = self._get_db()
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                with db:
                    db.execute(
                        "INSERT OR REPLACE INTO active_sessions (session_key, messages, updated_at) VALUES (?, ?, ?)",
                        (session_key, serialized_msgs, now_str)
                    )
                logger.debug(f"💾 [防抖刷盘成功] session {session_key} 内存消息已被同步至 SQLite。")
            except asyncio.CancelledError:
                # 任务被取消是正常防抖现象，不打印 error
                pass
            except Exception as write_err:
                logger.error(f"Failed to execute debounce write to SQLite: {write_err}")
            finally:
                # 安全清理
                if self._debounce_tasks.get(session_key) == current_task:
                    self._debounce_tasks.pop(session_key, None)

        try:
            loop = asyncio.get_running_loop()
            current_task = loop.create_task(_do_debounce_write())
            self._debounce_tasks[session_key] = current_task
        except RuntimeError:
            # 兼容同步单测环境无事件循环时，直接阻塞写（容灾）
            try:
                db = self._get_db()
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                with db:
                    db.execute(
                        "INSERT OR REPLACE INTO active_sessions (session_key, messages, updated_at) VALUES (?, ?, ?)",
                        (session_key, serialized_msgs, now_str)
                    )
            except Exception as sync_write_err:
                logger.error(f"Failed to execute synchronous active session write: {sync_write_err}")

    def _upsert_index(self, filename: str, new_line: str):
        from .index import _upsert_index
        return _upsert_index(self, filename, new_line)

    def _parse_index(self) -> list[dict]:
        from .index import _parse_index
        return _parse_index(self)

    async def _get_embedding(self, text: str) -> list[float]:
        from .index import _get_embedding
        return await _get_embedding(self, text)

    async def save_ki_embedding(self, ki_id: str, text_to_embed: str):
        from .index import save_ki_embedding
        return await save_ki_embedding(self, ki_id, text_to_embed)

    # ── 2. store.py 代理 ────────────────────────────────────────

    async def append_to_core(self, target_file: str, description: str, content: str) -> str:
        from .store import append_to_core
        res = await append_to_core(self, target_file, description, content)
        self.trigger_backup()
        return res

    async def save(self, filename: str, description: str, content: str,
                   note_path: Optional[str] = None) -> str:
        from .store import save
        res = await save(self, filename, description, content, note_path)
        self.trigger_backup()
        return res

    async def remove(self, filename: str):
        from .store import remove
        res = await remove(self, filename)
        self.trigger_backup()
        return res

    async def get_entry(self, filename: str) -> Optional[str]:
        from .store import get_entry
        return await get_entry(self, filename)

    def get_routing_rules(self) -> str:
        from .store import get_routing_rules
        return get_routing_rules(self)

    async def save_to_notes(self, dir_path: str, filename: str, content: str) -> Optional[str]:
        from .store import save_to_notes
        res = await save_to_notes(self, dir_path, filename, content)
        self.trigger_backup()
        return res

    async def verify_index(self) -> list[dict]:
        from .store import verify_index
        return await verify_index(self)

    async def gc_and_merge_fragmented_memories(self) -> int:
        from .store import gc_and_merge_fragmented_memories
        return await gc_and_merge_fragmented_memories(self)

    # ── 3. ki.py 代理 ───────────────────────────────────────────

    def save_ki(self, ki_data: dict) -> str:
        from .ki import save_ki
        res = save_ki(self, ki_data)
        self.trigger_backup()
        return res

    def merge_ki(self, existing_id: str, title: str, category: str, keywords: list, summary: str, content: str, revision_history: Optional[list] = None) -> str:
        from .ki import merge_ki
        res = merge_ki(self, existing_id, title, category, keywords, summary, content, revision_history)
        self.trigger_backup()
        return res

    def get_ki(self, ki_id: str) -> Optional[dict]:
        from .ki import get_ki
        return get_ki(self, ki_id)

    # ── 4. context.py 代理 ──────────────────────────────────────

    def search_notes(self, query: str, limit: int = 5) -> list[dict]:
        from .context import search_notes
        return search_notes(self, query, limit)

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        from .context import search_memories
        return search_memories(self, query, limit)

    # ── 5. session.py 代理 ──────────────────────────────────────

    def list_memories(self) -> list[str]:
        from .session import list_memories
        return list_memories(self)

    async def build_user_profile(self, llm) -> str:
        from .session import build_user_profile
        return await build_user_profile(self, llm)

    def _run_hot_migration_if_needed(self, old_base_dir_override: Optional[Path] = None):
        """老旧无隔离数据库及 Markdown 碎片到新多实例哈希隔离库的 100% 无损热平滑迁移引擎"""
        import sqlite3
        import shutil
        import json
        import hashlib
        import re
        
        # 1. 确定老旧无隔离库的物理根路径
        from agent.core.config import settings
        memory_cfg = settings.get("memory") or {}
        
        if old_base_dir_override:
            old_base_dir = Path(old_base_dir_override)
        else:
            base_dir_str = memory_cfg.get("base_dir", "~/.my-agent/memory")
            old_base_dir = self.resolve_adaptive_path(base_dir_str)
            
        old_db = old_base_dir / "memories.db"
        
        # 如果老旧无隔离目录就是当前隔离 base，或者老库根本不存在，或者老库被重命名为了 migrated，则安全退回
        try:
            if not old_db.exists() or old_db.resolve() == (self.base_dir / "memories.db").resolve():
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
        new_db_path = self.base_dir / "memories.db"
        
        # 确保新库在 merge 前已经进行了表的 DDL 初始化，这可通过触发一次 _get_db() 自动完成
        new_conn = self._get_db()
        
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
                    dest_file = self.base_dir / filename
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
                    dest_file = self.base_dir / filename
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
                if self.index_file.exists():
                    for line in self.index_file.read_text(encoding="utf-8").split("\n"):
                        line = line.strip()
                        if not line.startswith("- ["):
                            continue
                        m = re.match(r"- \[(.+?)\]\((.+?\.md)\)(?:\s*`([^`]+)`)?", line)
                        if m:
                            new_fnames.add(m.group(2))
                
                for entry in old_entries:
                    if entry["fname"] not in new_fnames:
                        self._upsert_index(entry["fname"], entry["line"])
            
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
            
        self._mem_cache.invalidate_all()
        self._note_cache.invalidate_all()
