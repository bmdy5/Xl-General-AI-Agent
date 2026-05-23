import os
import re
import time
import sqlite3
import logging
import asyncio
from pathlib import Path
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger("agent.memory.index")

class MemoryCache:
    def __init__(self, capacity=50, ttl=30):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.ttl = ttl
    
    def get(self, key):
        if key not in self.cache:
            return None
        ts, val = self.cache[key]
        if time.time() - ts > self.ttl:
            del self.cache[key]
            return None
        self.cache.move_to_end(key)
        return val
    
    def set(self, key, val):
        self.cache[key] = (time.time(), val)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def invalidate_all(self):
        self.cache.clear()


def _get_db(manager) -> sqlite3.Connection:
    """惰性初始化 SQLite + FTS5 索引，自动无损升级老数据为 CJK 高精度索引."""
    if manager._db is None:
        db_path = manager.base_dir / "memories.db"
        is_new = not db_path.exists()
        manager._db = sqlite3.connect(str(db_path), timeout=1.0)
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
                version INTEGER DEFAULT 1
            )
        """)
        # 创建高精度 CJK 知识全文检索虚拟表
        manager._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS kis_fts
            USING fts5(ki_id, title, category, keywords, summary, content,
                       tokenize="porter unicode61")
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


async def _get_embedding(manager, text: str) -> list[float]:
    """提取 768 维中文增强语义向量。支持本地 m3e-base 或远程 API 两种模式。"""
    text = text.strip()[:300]
    embedding_mode = os.getenv("EMBEDDING_MODE", "local").lower()

    if embedding_mode == "local":
        try:
            global _LOCAL_MODEL_CACHE
            if "_LOCAL_MODEL_CACHE" not in globals():
                globals()["_LOCAL_MODEL_CACHE"] = {}
            
            cache = globals()["_LOCAL_MODEL_CACHE"]
            
            if "_m3e" in cache and cache["_m3e"] is None:
                return [0.0] * 768
            
            if "_m3e" not in cache:
                current_dir = Path(__file__).resolve().parent
                local_model_path = current_dir.parent.parent / "model" / "m3e-base"
                
                if not (local_model_path.exists() and local_model_path.is_dir()):
                    cache["_m3e"] = None
                    logger.error(f"Offline model path not found at {local_model_path}. Circuit breaker activated instantly. 0ms fallback to zeros.")
                    return [0.0] * 768
                
                from sentence_transformers import SentenceTransformer
                
                try:
                    logger.info(f"Loading m3e-base model from local path: {local_model_path}")
                    model = await asyncio.wait_for(
                        asyncio.to_thread(SentenceTransformer, str(local_model_path)),
                        timeout=10.0
                    )
                    cache["_m3e"] = model
                    logger.info("Local m3e-base model loaded successfully!")
                except Exception as load_err:
                    cache["_m3e"] = None
                    logger.error(f"Failed to load m3e-base model: {load_err}. Circuit breaker activated, local embedding disabled.")
                    return [0.0] * 768
            
            model = cache["_m3e"]
            if model is None:
                return [0.0] * 768
            
            embeddings = await asyncio.wait_for(
                asyncio.to_thread(model.encode, [text], show_progress_bar=False),
                timeout=10.0
            )
            return [float(x) for x in embeddings[0]]
        except Exception as e:
            logger.error(f"Failed to extract local embedding via m3e-base: {e}")
            return [0.0] * 768

    import litellm
    model = os.getenv("MYAGENT_EMBEDDING_MODEL", "text-embedding-3-small")
    api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    api_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE") or None
    
    try:
        response = await litellm.aembedding(
            model=model,
            input=[text],
            dimensions=768,
            api_key=api_key if api_key else None,
            api_base=api_base if api_base else None
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Failed to fetch embedding: {e}")
        return [0.0] * 768


async def save_ki_embedding(manager, ki_id: str, text_to_embed: str):
    """后台异步协程任务：非阻塞为指定 KI 提取 768 维 Embedding 并原子保存至 SQLite。"""
    embedding = await manager._get_embedding(text_to_embed)
    import json
    embedding_str = json.dumps(embedding)
    db = manager._get_db()
    with db:
        db.execute("""
            INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding)
            VALUES (?, ?)
        """, (ki_id, embedding_str))
