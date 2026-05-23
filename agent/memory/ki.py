import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("agent.memory.ki")

def save_ki(manager, ki_data: dict) -> str:
    """以原子事务形式将 KI 数据存入 SQLite，并同步更新高精度 CJK 全文检索表."""
    ki_id = ki_data["id"]
    title = ki_data["title"]
    category = ki_data["category"]
    keywords = ki_data["keywords"]
    if isinstance(keywords, list):
        keywords_str = json.dumps(keywords, ensure_ascii=False)
    else:
        keywords_str = str(keywords)
    summary = ki_data["summary"]
    content = ki_data["content"]
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    db = manager._get_db()
    with db:
        cur = db.execute("SELECT created_at, visit_count, version FROM knowledge_items WHERE id = ?", (ki_id,))
        row = cur.fetchone()
        if row:
            created_at = row[0]
            visit_count = row[1]
            version = row[2] + 1
            db.execute("""
                UPDATE knowledge_items
                SET title = ?, category = ?, keywords = ?, summary = ?, content = ?,
                    updated_at = ?, last_hit_at = ?, version = ?
                WHERE id = ?
            """, (title, category, keywords_str, summary, content, now, now, version, ki_id))
            db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (ki_id,))
        else:
            created_at = now
            visit_count = 0
            version = 1
            db.execute("""
                INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ki_id, title, category, keywords_str, summary, content, created_at, now, now, visit_count, version))
        
        from .fts_index import _cjk_space
        db.execute("""
            INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ki_id,
            _cjk_space(title),
            _cjk_space(category),
            _cjk_space(keywords_str),
            _cjk_space(summary),
            _cjk_space(content)
        ))
        
    manager._mem_cache.invalidate_all()
    return now


def merge_ki(manager, existing_id: str, title: str, category: str, keywords: list, summary: str, content: str) -> str:
    """合并并更新已有的 KI 数据，自动重用 save_ki 的强一致事务逻辑."""
    ki_data = {
        "id": existing_id,
        "title": title,
        "category": category,
        "keywords": keywords,
        "summary": summary,
        "content": content
    }
    return manager.save_ki(ki_data)


def get_ki(manager, ki_id: str) -> Optional[dict]:
    """根据 ID 查询单条长期大脑的 KI 记录."""
    db = manager._get_db()
    cur = db.execute("""
        SELECT id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version
        FROM knowledge_items WHERE id = ?
    """, (ki_id,))
    row = cur.fetchone()
    if row:
        try:
            keywords = json.loads(row[3])
        except Exception:
            keywords = row[3]
        return {
            "id": row[0],
            "title": row[1],
            "category": row[2],
            "keywords": keywords,
            "summary": row[4],
            "content": row[5],
            "created_at": row[6],
            "updated_at": row[7],
            "last_hit_at": row[8],
            "visit_count": row[9],
            "version": row[10]
        }
    return None
