"""无向图聚类引擎 — 将 micro KI 聚类为 macro KI。"""
import json
import math
import logging
from datetime import datetime, timezone
from operator import mul

logger = logging.getLogger("agent.memory.clustering")

MACRO_MERGE_PROMPT = """将以下 {n} 条相关记忆碎片合并为一条精炼的专题总结。

要求：
- 提炼共同主题，不遗漏任何踩坑教训或用户偏好
- 每条碎片的关键细节用「引用: {ki_id}」标注来源
- 不写废话，纯干货

碎片列表：
{children_text}

只输出合并后的 Markdown 正文。"""


def _cosine_similarity(a: list, b: list) -> float:
    """余弦相似度。"""
    dot = sum(mul(x, y) for x, y in zip(a, b))
    mag_a = math.sqrt(sum(mul(x, x) for x in a))
    mag_b = math.sqrt(sum(mul(x, x) for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


GENERIC_KW = {"reflection", "learn", "project", "user", "feedback", "audit", "preference",
              "experience", "insight", "observation", "note", "general", "other"}

def _has_keyword_overlap(kw_a, kw_b) -> bool:
    """检查两个 keywords 列表是否有实质性交集（过滤通用词）。"""
    try:
        set_a = set(kw_a) if isinstance(kw_a, list) else set(json.loads(kw_a))
        set_b = set(kw_b) if isinstance(kw_b, list) else set(json.loads(kw_b))
        specific = (set_a & set_b) - GENERIC_KW
        return len(specific) >= 1  # 至少 1 个专有词重合（极大团已提供严格过滤）
    except Exception:
        return False


def _find_connected_components(edges: dict, nodes: list, max_size: int = 10) -> list[list]:
    """DFS 提取连通分量，>max_size 的簇递归用更高阈值切分。"""
    visited = set()
    components = []

    def dfs(node, comp):
        visited.add(node)
        comp.append(node)
        for neighbor in edges.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, comp)

    for node in nodes:
        if node not in visited:
            comp = []
            dfs(node, comp)
            components.append(comp)

    return components


async def build_macros(manager, agent=None) -> dict:
    """扫描 micro KI，聚类生成 macro KI。返回 {created: n, skipped: n}。"""
    db = manager._get_db()

    # 1. 拉取所有未归档的 micro KI
    rows = db.execute("""
        SELECT id, title, category, keywords, summary, content, ki_type
        FROM knowledge_items
        WHERE ki_type = 'micro' AND parent_id IS NULL
    """).fetchall()

    if len(rows) == 0:
        logger.info("No micro KIs found, skipping clustering")
        return {"created": 0, "skipped": 0, "absorbed": 0}

    # 2. 提取 embeddings
    ki_map = {}
    for r in rows:
        ki_map[r[0]] = {
            "id": r[0], "title": r[1], "category": r[2],
            "keywords": r[3], "summary": r[4], "content": r[5]
        }

    # 批量获取向量
    from .embedding import _get_embedding
    embeddings = {}
    for ki_id, ki in ki_map.items():
        try:
            text = f"{ki['title']} {ki['summary']} {ki.get('content', '')[:1000]}"
            vec = await _get_embedding(manager, text)
            embeddings[ki_id] = vec
        except Exception as e:
            logger.debug(f"Embedding failed for {ki_id}: {e}")

    # 2.5 增量吸收机制 (Incremental Absorption)
    from datetime import datetime, timezone
    import json
    
    macro_rows = db.execute("""
        SELECT m.id, m.category, m.keywords, m.child_ids, e.embedding
        FROM knowledge_items m
        JOIN ki_embeddings e ON m.id = e.ki_id
        WHERE m.ki_type = 'macro'
    """).fetchall()

    macros = []
    for mr in macro_rows:
        try:
            vec = json.loads(mr[4]) if isinstance(mr[4], str) else mr[4]
            macros.append({
                "id": mr[0], "category": mr[1], "keywords": mr[2],
                "child_ids": mr[3], "vec": vec
            })
        except Exception:
            pass

    absorbed_count = 0
    remaining_ids = []
    threshold = 0.82

    for ki_id in list(ki_map.keys()):
        ki = ki_map[ki_id]
        if ki_id not in embeddings:
            remaining_ids.append(ki_id)
            continue
            
        best_macro = None
        best_sim = 0.0
        
        for m in macros:
            if m["category"] != ki["category"]: continue
            if not _has_keyword_overlap(m["keywords"] or "[]", ki["keywords"] or "[]"): continue
            
            sim = _cosine_similarity(embeddings[ki_id], m["vec"])
            if sim > best_sim and sim >= threshold:
                best_sim = sim
                best_macro = m
                
        if best_macro:
            m_id = best_macro["id"]
            logger.info(f"Incremental absorption: {ki_id} -> {m_id} (sim={best_sim:.3f})")
            try:
                cids = json.loads(best_macro["child_ids"] or "[]")
            except Exception:
                cids = []
            if ki_id not in cids:
                cids.append(ki_id)
                
            try:
                m_kws = set(json.loads(best_macro["keywords"] or "[]"))
            except Exception:
                m_kws = set()
            try:
                k_kws = set(json.loads(ki["keywords"] or "[]")) if isinstance(ki["keywords"], str) else set(ki["keywords"] or [])
            except Exception:
                k_kws = set()
            
            new_kws = list(m_kws | k_kws)
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            
            db.execute("UPDATE knowledge_items SET child_ids = ?, keywords = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(cids, ensure_ascii=False), json.dumps(new_kws, ensure_ascii=False), now_str, m_id))
            from .fts_index import _cjk_space
            db.execute("UPDATE kis_fts SET keywords = ? WHERE ki_id = ?",
                       (_cjk_space(json.dumps(new_kws, ensure_ascii=False)), m_id))
            db.execute("UPDATE knowledge_items SET parent_id = ? WHERE id = ?", (m_id, ki_id))
            db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (ki_id,))
            
            absorbed_count += 1
            del ki_map[ki_id]
        else:
            remaining_ids.append(ki_id)

    if len(ki_map) < 2:
        logger.info(f"Only {len(ki_map)} micro KIs remaining after absorption, skipping graph clustering")
        return {"created": 0, "skipped": 0, "absorbed": absorbed_count}

    # 3. 构建无向图 (仅用 remaining_ids)
    edges = {kid: [] for kid in ki_map}
    ids = list(ki_map.keys())

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            if a_id not in embeddings or b_id not in embeddings:
                continue

            ki_a, ki_b = ki_map[a_id], ki_map[b_id]

            # 锁1: 同 category
            if ki_a["category"] != ki_b["category"]:
                continue

            # 锁2: keywords 交集
            if not _has_keyword_overlap(ki_a["keywords"], ki_b["keywords"]):
                continue

            sim = _cosine_similarity(embeddings[a_id], embeddings[b_id])
            if sim >= threshold:
                edges[a_id].append(b_id)
                edges[b_id].append(a_id)
                logger.debug(f"Edge: {a_id[:20]} <-> {b_id[:20]} (sim={sim:.3f})")

    # 4. 连通分量提取（>10的簇内部用 0.85 二次切分）
    raw_components = _find_connected_components(edges, list(ki_map.keys()))
    clusters = []
    for comp in raw_components:
        n = len(comp)
        if n < 2: continue
        if n > 10:
            sub_edges = {cid: [n2 for n2 in edges.get(cid, []) if n2 in comp] for cid in comp}
            sub_comps = _find_connected_components(sub_edges, comp)
            clusters.extend([c for c in sub_comps if len(c) >= 2])
        else:
            clusters.append(comp)

    logger.info(f"Found {len(clusters)} clusters (from {len(rows)} micros)")

    # 5. 为每个簇生成 macro
    created = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for cluster in clusters:
        children = [ki_map[cid] for cid in cluster]
        child_ids = [c["id"] for c in children]

        # 合并 keywords
        all_kw = set()
        for c in children:
            kws = c["keywords"]
            if isinstance(kws, str):
                try:
                    kws = json.loads(kws)
                except Exception:
                    kws = [kws]
            for kw in kws:
                all_kw.add(str(kw))

        macro_id = f"macro_{abs(hash(tuple(sorted(child_ids)))) % 10**12:012d}"
        title = f"专题: {children[0]['title'][:40]}"

        # 如果 agent 可用，调 LLM 生成 content
        if agent and hasattr(agent, "llm"):
            children_text = "\n\n---\n\n".join(
                f"[{c['id']}] {c['title']}\n{c.get('content', '')[:500]}"
                for c in children
            )
            prompt = MACRO_MERGE_PROMPT.format(
                n=len(children), ki_id="{ki_id}", children_text=children_text
            )
            try:
                resp = await agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    tools=None,
                )
                summary = resp.get("content", "").strip()[:2000]
            except Exception as e:
                logger.warning(f"LLM distill failed for {macro_id}: {e}")
                summary = f"{len(children)} 条相关记忆的专题索引"
        else:
            summary = f"{len(children)} 条相关记忆的专题索引"

        # 6. 写入 macro
        db.execute("""
            INSERT OR REPLACE INTO knowledge_items
            (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version, ki_type, parent_id, child_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 'macro', NULL, ?)
        """, (macro_id, title, children[0]["category"], json.dumps(list(all_kw), ensure_ascii=False),
              summary[:300], summary, now, now, now, json.dumps(child_ids, ensure_ascii=False)))

        # 7. 更新 micros 的 parent_id，从 FTS5 注销
        for cid in child_ids:
            db.execute("UPDATE knowledge_items SET parent_id = ? WHERE id = ?", (macro_id, cid))
            db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (cid,))

        # 8. macro 加入 FTS5
        from .fts_index import _cjk_space
        db.execute("""
            INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (macro_id, _cjk_space(title), _cjk_space(children[0]["category"]),
              _cjk_space(json.dumps(list(all_kw), ensure_ascii=False)),
              _cjk_space(summary[:300]), _cjk_space(summary)))

        created += 1
        logger.info(f"Macro created: {macro_id} ({len(children)} children)")

    # 为 macro 生成 embedding（使用 summary 文本）
    for macro_id in [r[0] for r in db.execute("SELECT id FROM knowledge_items WHERE ki_type='macro' AND id NOT IN (SELECT ki_id FROM ki_embeddings)").fetchall()]:
        try:
            macro = db.execute("SELECT title, summary FROM knowledge_items WHERE id=?", (macro_id,)).fetchone()
            if macro:
                from .embedding import _get_embedding
                embed_text = f"{macro[0]} {macro[1]}"
                vec = await _get_embedding(manager, embed_text)
                db.execute("INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)",
                           (macro_id, str(vec)))
        except Exception as e:
            logger.debug(f"Embedding failed for macro {macro_id}: {e}")

    db.commit()
    return {"created": created, "skipped": len(rows) - sum(len(c) for c in clusters)}


def expand_macro_result(manager, search_results: list[dict], max_content_chars: int = 5000) -> str:
    """展开搜索结果中的 macro，读取子 micro 内容注入上下文。"""
    db = manager._get_db()
    expanded = []
    total_chars = 0

    for r in search_results:
        fid = r.get("filename", "")
        k_id = fid[3:-3] if fid.startswith("ki_") and fid.endswith(".md") else fid
        ki = db.execute("SELECT ki_type, child_ids, content FROM knowledge_items WHERE id=?", (k_id,)).fetchone()
        if not ki:
            continue

        ki_type, child_ids_str, content = ki[0], ki[1], ki[2] or ""

        if ki_type == "macro" and child_ids_str:
            import json
            try:
                child_ids = json.loads(child_ids_str)
            except Exception:
                child_ids = []

            children_text = []
            if child_ids:
                placeholders = ",".join(["?"] * len(child_ids))
                crows = db.execute(f"SELECT title, content FROM knowledge_items WHERE id IN ({placeholders}) ORDER BY updated_at DESC", child_ids).fetchall()
                for crow in crows:
                    ctext = crow[1] or ""
                    segment = ctext[:500]
                    if total_chars + len(segment) > max_content_chars:
                        break
                    children_text.append(f"[{crow[0]}]: {segment}")
                    total_chars += len(segment)

            expanded.append(f"## {r.get('description','')}\n{chr(10).join(children_text)}")
        else:
            if total_chars + len(content) > max_content_chars:
                content = content[:max_content_chars - total_chars] + "..."
            expanded.append(f"## {r.get('description','')}\n{content[:800]}")
            total_chars += len(content[:800])

    return "\n\n".join(expanded) if expanded else ""
