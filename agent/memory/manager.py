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
        self._mem_cache = MemoryCache(capacity=50, ttl=30)
        self._note_cache = MemoryCache(capacity=50, ttl=30)

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
                
        # 起后台非阻塞 Task，保障主 ReAct 0ms 等待
        asyncio.create_task(_async_backup())

    # ── 1. index.py 代理 ────────────────────────────────────────

    def _get_db(self):
        from .index import _get_db
        return _get_db(self)

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

    def merge_ki(self, existing_id: str, title: str, category: str, keywords: list, summary: str, content: str) -> str:
        from .ki import merge_ki
        res = merge_ki(self, existing_id, title, category, keywords, summary, content)
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
