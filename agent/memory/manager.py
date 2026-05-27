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
from .migration import _run_hot_migration_if_needed

def _migrate_old_paths_to_unified_brain():
    """将项目下旧版散落的脑区（.memory, memory, skills, experience）无损且原子地物理热迁移归并至 agent_memory 下"""
    project_root = Path(__file__).resolve().parents[2]
    flag_file = project_root / "agent_memory" / ".migrated_to_agent_memory"
    
    if flag_file.exists():
        return
        
    old_dot_memory = project_root / ".memory"
    old_memory = project_root / "memory"
    old_skills = project_root / "skills"
    old_experience = project_root / "experience"
    
    # 没有任何旧版散落脑区时直接写入标志，防止重复扫描
    if not (old_dot_memory.exists() or old_memory.exists() or old_skills.exists() or old_experience.exists()):
        new_dir = project_root / "agent_memory"
        new_dir.mkdir(parents=True, exist_ok=True)
        flag_file.write_text("ok", encoding="utf-8")
        return

    import shutil
    logging.getLogger("agent.memory.migration").info("🔄 [大脑大统一物理搬家] 检测到旧有分散脑区资产，正在进行无损迁移升级...")
    
    # 创建新版四叶脑叶结构
    new_core = project_root / "agent_memory" / "core"
    new_skills = project_root / "agent_memory" / "skills"
    new_experiences = project_root / "agent_memory" / "experiences"
    new_context = project_root / "agent_memory" / "context"
    
    for d in [new_core, new_skills, new_experiences, new_context]:
        d.mkdir(parents=True, exist_ok=True)
        
    try:
        # 1. 迁移 .memory -> core/
        if old_dot_memory.exists():
            for item in old_dot_memory.iterdir():
                if item.name.startswith("."):
                    continue
                dest = new_core / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            shutil.rmtree(old_dot_memory)
            
        # 2. 迁移 memory -> context/
        if old_memory.exists():
            for item in old_memory.iterdir():
                if item.is_file() and item.name.endswith(".json"):
                    shutil.copy2(item, new_context / item.name)
            shutil.rmtree(old_memory)
            
        # 3. 迁移 skills -> skills/
        if old_skills.exists():
            for item in old_skills.iterdir():
                if item.is_file() and item.name.endswith(".md"):
                    shutil.copy2(item, new_skills / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    dest = new_skills / item.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
            shutil.rmtree(old_skills)
            
        # 4. 迁移 experience -> experiences/
        if old_experience.exists():
            for item in old_experience.iterdir():
                if item.is_file() and item.name.endswith(".md"):
                    shutil.copy2(item, new_experiences / item.name)
            shutil.rmtree(old_experience)
            
        flag_file.write_text("migration_success", encoding="utf-8")
        logging.getLogger("agent.memory.migration").info("✨ [大脑大统一物理搬家] 旧有分散脑区资产无损搬家热升级大获成功！")
        
    except Exception as e:
        logging.getLogger("agent.memory.migration").error(f"❌ [大脑大统一物理搬家] 热迁移时遇到了阻碍异常: {e}")

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
        
        # 0. 启动大脑物理资产热迁移搬家
        _migrate_old_paths_to_unified_brain()
        
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

        # 7. 启动后延迟 1 秒在后台自动启动物理碎片大蒸馏 GC，实现脑区脱水极致纯净 (方案 A 全自动静默守护)
        async def _startup_gc_daemon():
            await asyncio.sleep(1.0)
            try:
                cleaned = await self.gc_and_merge_fragmented_memories()
                if cleaned > 0:
                    logging.getLogger("agent.memory.gc").info(
                        f"✨ [大脑自动蒸馏] 成功自动熔接并物理清退了 {cleaned} 个零散碎片，脑区已脱水极致纯净！"
                    )
            except Exception as gc_err:
                logging.getLogger("agent.memory.gc").error(f"Failed to run startup auto-gc: {gc_err}")
                
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_startup_gc_daemon())
        except RuntimeError:
            pass

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
                
                # 2. 物理同步核心 Markdown 记忆文件与索引 (镜像级删除与拷贝一致性对齐)
                # 首先，前置清理：如果项目备份目录中存在的 .md 文件在主物理目录中已被清退删除，同步执行物理 unlink 销毁
                if self.backup_dir.exists():
                    for dest_file in self.backup_dir.iterdir():
                        if dest_file.is_file() and dest_file.suffix == ".md":
                            src_file = self.base_dir / dest_file.name
                            if not src_file.exists():
                                dest_file.unlink()

                # 接着，正向覆盖拷贝
                for src_file in self.base_dir.iterdir():
                    if src_file.is_file() and src_file.suffix == ".md":
                        shutil.copy2(src_file, self.backup_dir / src_file.name)
                logger.info(f"💾 [热备份成功] 小萤的灵魂记忆已安全镜像双写同步至项目沙箱: {self.backup_dir}")
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
        from .index import with_db_retry
        
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

        # 3. 带自适应指数重试的写库协程与同步函数
        @with_db_retry()
        async def _async_write_db():
            db = self._get_db()
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO active_sessions (session_key, messages, updated_at) VALUES (?, ?, ?)",
                    (session_key, serialized_msgs, now_str)
                )

        @with_db_retry()
        def _sync_write_db():
            db = self._get_db()
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO active_sessions (session_key, messages, updated_at) VALUES (?, ?, ?)",
                    (session_key, serialized_msgs, now_str)
                )

        # 4. 创建全新的防抖物理写入 Task
        async def _do_debounce_write():
            try:
                await asyncio.sleep(1.0)
                await _async_write_db()
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
                _sync_write_db()
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

    def save_ki(self, ki_data: dict, _existing_db=None) -> str:
        from .ki import save_ki
        res = save_ki(self, ki_data, _existing_db=_existing_db)
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
        return _run_hot_migration_if_needed(self, old_base_dir_override)
