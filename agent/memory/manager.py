"""Memory system - Facade class.

所有的具体数据库、物理IO、中文增强语义检索等高内聚逻辑已被解耦物理拆分至：
- store.py
- index.py
- ki.py
- context.py
- session.py
"""

import logging
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# 导入拆分出的缓存类
from .index import MemoryCache

# 导入所有常量与辅助类以维持 100 percent 向下兼容
from .store import PROTECTED_FILES, CORE_FILES, DEFAULT_ROUTING_RULES, KB_DIR, KNOWLEDGE_INDEX

class MemoryManager:
    """长期记忆与自学习大脑 — 外观 Facade 编排层."""

    def __init__(self, base_dir: Optional[Union[str, Path]] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".my-agent" / "memory"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base_dir / "MEMORY.md"
        self.rules_file = self.base_dir / "routing_rules.md"
        if not self.rules_file.exists():
            self.rules_file.write_text(DEFAULT_ROUTING_RULES, encoding="utf-8")
        self._db = None
        self._mem_cache = MemoryCache(capacity=50, ttl=30)
        self._note_cache = MemoryCache(capacity=50, ttl=30)

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
        return await append_to_core(self, target_file, description, content)

    async def save(self, filename: str, description: str, content: str,
                   note_path: Optional[str] = None) -> str:
        from .store import save
        return await save(self, filename, description, content, note_path)

    async def remove(self, filename: str):
        from .store import remove
        return await remove(self, filename)

    async def get_entry(self, filename: str) -> Optional[str]:
        from .store import get_entry
        return await get_entry(self, filename)

    def get_routing_rules(self) -> str:
        from .store import get_routing_rules
        return get_routing_rules(self)

    async def save_to_notes(self, dir_path: str, filename: str, content: str) -> Optional[str]:
        from .store import save_to_notes
        return await save_to_notes(self, dir_path, filename, content)

    async def verify_index(self) -> list[dict]:
        from .store import verify_index
        return await verify_index(self)

    async def gc_and_merge_fragmented_memories(self) -> int:
        from .store import gc_and_merge_fragmented_memories
        return await gc_and_merge_fragmented_memories(self)

    # ── 3. ki.py 代理 ───────────────────────────────────────────

    def save_ki(self, ki_data: dict) -> str:
        from .ki import save_ki
        return save_ki(self, ki_data)

    def merge_ki(self, existing_id: str, title: str, category: str, keywords: list, summary: str, content: str) -> str:
        from .ki import merge_ki
        return merge_ki(self, existing_id, title, category, keywords, summary, content)

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
