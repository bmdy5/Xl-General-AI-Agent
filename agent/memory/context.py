import os
import re
import math
import sqlite3
import logging
import asyncio
from pathlib import Path
from typing import Optional
from operator import mul

logger = logging.getLogger("agent.memory.context")

def _run_async(coro):
    """大师级同步包装器：安全在各种 event loop 环境下执行协程."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def search_notes(manager, query: str, limit: int = 5) -> list[dict]:
    """搜索笔记知识库。返回 BM25 排序的分块结果。"""
    cache_key = (query, limit)
    cached_res = manager._note_cache.get(cache_key)
    if cached_res is not None:
        return cached_res

    clean = re.sub(r'[^\w\u4e00-\u9fff\s]', " ", query).strip()
    if not clean or len(clean) < 2:
        return []
    try:
        from agent.core.config import settings
        # 1. 动态定位 notes.db，优先放入沙箱隔离区，避免锁冲突
        if manager and hasattr(manager, "base_dir"):
            notes_db = manager.base_dir / "notes.db"
        else:
            notes_db = Path.home() / ".my-agent" / "notes.db"
            notes_db.parent.mkdir(parents=True, exist_ok=True)
            
        db = sqlite3.connect(str(notes_db))
        from .notes_fts import create_table as nt_create, sync_incremental
        nt_create(db)
        
        # 2. 从 settings 动态提取配置并执行自愈增量同步
        kb_cfg = settings.get("knowledge_base") or {}
        notes_paths = kb_cfg.get("notes_paths") or ["~/Desktop/学习笔记/Agent开发", "~/Desktop/学习笔记/后端开发"]
        
        for path_str in notes_paths:
            resolved_path = Path(os.path.expanduser(path_str)).resolve()
            if resolved_path.exists() and resolved_path.is_dir():
                sync_incremental(db, resolved_path)
            else:
                logger.debug(f"ℹ️ [自愈] 增量同步笔记路径不存在，安全跳过: {path_str}")
                
        from .notes_fts import search as notes_search
        res = notes_search(db, query, limit)
        manager._note_cache.set(cache_key, res)
        return res
    except Exception as e:
        logger.warning(f"Error during incremental search_notes: {e}")
        return []


def _like_search(db, table: str, query: str, limit: int = 5) -> list[dict]:
    """LIKE 降级搜索."""
    results = []
    keywords = [k for k in re.split(r'\s+', query) if len(k) >= 2]
    if not keywords:
        keywords = [query]
    for kw in keywords[:3]:
        try:
            cur = db.execute(
                f"SELECT content, description, memory_type, filename, timestamp "
                f"FROM {table} WHERE content LIKE ? LIMIT ?",
                (f"%{kw}%", limit),
            )
            for row in cur:
                results.append({
                    "content": row[0], "description": row[1],
                    "memory_type": row[2], "filename": row[3],
                    "timestamp": row[4],
                })
                if len(results) >= limit:
                    break
        except Exception:
            continue
        if len(results) >= limit:
            break
    return results[:limit]


def search_memories(manager, query: str, limit: int = 5) -> list[dict]:
    """FTS5 + 768维语义向量双通道混合检索 (RRF 融合重排 + 时序热度衰减)"""
    cache_key = (query, limit)
    cached_res = manager._mem_cache.get(cache_key)
    if cached_res is not None:
        return cached_res

    clean = re.sub(r'[^\w\u4e00-\u9fff\s]', ' ', query).strip()
    if not clean or len(clean) < 2:
        return []

    try:
        db = manager._get_db()
        
        # --- 1. 通道一：FTS5 精准检索 ---
        from .fts_index import _cjk_space, search as fts_search
        fts_query = _cjk_space(clean)
        
        ki_fts_rows = []
        try:
            cur = db.execute("""
                SELECT ki_id, title, category, keywords, summary, content
                FROM kis_fts
                WHERE kis_fts MATCH ? LIMIT ?
            """, (fts_query, limit + limit))
            ki_fts_rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"KI FTS search failed: {e}")
            
        legacy_rows = []
        try:
            legacy_rows = fts_search(db, ' '.join(clean.split()), limit)
            if len(legacy_rows) < 2 and clean:
                like_legacy = _like_search(db, "memories_fts", clean, limit)
                legacy_rows = like_legacy or legacy_rows
        except Exception:
            pass

        # --- 2. 通道二：768 维向量语义检索 ---
        ki_vector_rows = []
        try:
            count_cur = db.execute("SELECT COUNT(*) FROM knowledge_items")
            total_ki = count_cur.fetchone()[0]

            query_vec = _run_async(manager._get_embedding(query))
            
            q_mag = math.sqrt(sum(mul(x, x) for x in query_vec))
            if q_mag > 0:
                scored_kis = []
                
                if total_ki <= 200:
                    cur = db.execute("SELECT ki_id, embedding FROM ki_embeddings")
                    all_embeds = cur.fetchall()
                else:
                    candidate_ids = set()
                    
                    stop_words = {"刚才", "跟我", "聊到", "讨论", "关于", "事情", "的", "了", "和", "与", "是", "我", "你", "他", "们", "这", "那"}
                    clean_terms = [t for t in clean.split() if t not in stop_words]
                    
                    or_terms = []
                    for term in clean_terms:
                        cjk_spaced = _cjk_space(term).strip()
                        if cjk_spaced:
                            term_or = " OR ".join(f'"{char}"' for char in cjk_spaced.split() if char.strip())
                            if term_or:
                                or_terms.append(f"({term_or})")
                    
                    fts_query_or = " OR ".join(or_terms) if or_terms else _cjk_space(clean)
                    
                    fts_cand_rows = []
                    if fts_query_or.strip():
                        try:
                            cur = db.execute("""
                                SELECT ki_id FROM kis_fts 
                                WHERE kis_fts MATCH ? LIMIT 40
                            """, (fts_query_or, ))
                            fts_cand_rows = cur.fetchall()
                        except Exception:
                            try:
                                like_q = f"%{clean}%"
                                cur = db.execute("""
                                    SELECT id FROM knowledge_items 
                                    WHERE title LIKE ? OR keywords LIKE ? OR summary LIKE ? LIMIT 40
                                """, (like_q, like_q, like_q))
                                fts_cand_rows = cur.fetchall()
                            except Exception:
                                pass
                                
                    for r in fts_cand_rows:
                        candidate_ids.add(r[0])
                        
                    try:
                        cur = db.execute("""
                            SELECT id FROM knowledge_items 
                            ORDER BY last_hit_at DESC, visit_count DESC LIMIT 40
                        """)
                        for r in cur.fetchall():
                            candidate_ids.add(r[0])
                    except Exception:
                        pass
                        
                    is_debug_intent = any(w in query.lower() for w in ["错误", "报错", "调试", "bug", "error", "exception", "traceback"])
                    if is_debug_intent:
                        try:
                            cur = db.execute("SELECT id FROM knowledge_items WHERE category = 'xl_debugging' LIMIT 20")
                            for r in cur.fetchall():
                                candidate_ids.add(r[0])
                        except Exception:
                            pass
                            
                    all_embeds = []
                    if candidate_ids:
                        placeholders = ",".join("?" for _ in candidate_ids)
                        cur = db.execute(
                            f"SELECT ki_id, embedding FROM ki_embeddings WHERE ki_id IN ({placeholders})",
                            list(candidate_ids)
                        )
                        all_embeds = cur.fetchall()
                        
                for row in all_embeds:
                    k_id = row[0]
                    try:
                        import json
                        k_vec = json.loads(row[1])
                        k_mag = math.sqrt(sum(mul(x, x) for x in k_vec))
                        if k_mag > 0:
                            dot = sum(mul(a, b) for a, b in zip(query_vec, k_vec))
                            cos_sim = dot / mul(q_mag, k_mag)
                            if cos_sim >= 0.60:
                                scored_kis.append((k_id, cos_sim))
                    except Exception:
                        continue
                
                scored_kis.sort(key=lambda x: x[1], reverse=True)
                ki_vector_rows = scored_kis[:limit + limit]
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

        # --- 3. 混合重排 (RRF) ---
        rrf_scores = {}
        for rank, row in enumerate(ki_fts_rows):
            k_id = row[0]
            rrf_scores[k_id] = rrf_scores.get(k_id, 0.0) + (1.0 / (60.0 + rank))
            
        for rank, (k_id, _) in enumerate(ki_vector_rows):
            rrf_scores[k_id] = rrf_scores.get(k_id, 0.0) + (1.0 / (60.0 + rank))

        # --- 4. 融合意图加权与时序/热度衰减评分 ---
        is_debug_intent = any(w in query.lower() for w in ["错误", "报错", "调试", "bug", "error", "exception", "traceback"])
        
        merged_kis = []
        for k_id, score in rrf_scores.items():
            cur = db.execute("""
                SELECT id, title, category, keywords, summary, content, visit_count, last_hit_at, updated_at
                FROM knowledge_items WHERE id = ?
            """, (k_id,))
            ki_row = cur.fetchone()
            if not ki_row:
                continue
            
            title, category, summary, content = ki_row[1], ki_row[2], ki_row[4], ki_row[5]
            visit_count, last_hit_at, updated_at = ki_row[6], ki_row[7], ki_row[8]
            
            heat_multiplier = 1.0 + mul(0.1, math.log(1 + visit_count))
            
            intent_multiplier = 1.0
            if is_debug_intent:
                if category == "xl_debugging":
                    intent_multiplier = 1.3
                else:
                    intent_multiplier = 0.9
            
            final_score = mul(mul(score, heat_multiplier), intent_multiplier)
            
            merged_kis.append({
                "content": content,
                "description": f"[{category}] {title}",
                "memory_type": "ki",
                "filename": f"ki_{k_id}.md",
                "timestamp": updated_at,
                "score": final_score
            })
            
            try:
                from datetime import datetime
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                db.execute("""
                    UPDATE knowledge_items
                    SET visit_count = visit_count + 1, last_hit_at = ?
                    WHERE id = ?
                """, (now, k_id))
                db.commit()
            except Exception:
                pass

        merged_kis.sort(key=lambda x: x["score"], reverse=True)
        
        res = []
        res.extend(merged_kis[:limit])
        
        if len(res) < limit:
            for row in legacy_rows:
                if len(res) >= limit:
                    break
                legacy_fname = row.get("filename", "")
                if not any(legacy_fname in r.get("filename", "") for r in res):
                    res.append(row)
        
        for r in res:
            r.pop("score", None)
            
        manager._mem_cache.set(cache_key, res)
        return res
    except Exception as e:
        logger.error(f"Search memories hybrid engine error: {e}")
        return []
