"""数据库热迁移 — 迁移完成后保留为 no-op 存根。"""
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger("agent.memory.migration")


def _run_hot_migration_if_needed(manager, old_base_dir_override: Optional[Path] = None):
    """迁移已完成，直接返回。"""
    pass
