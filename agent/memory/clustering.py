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

    if len(rows) < 2:
        logger.info(f"Only {len(rows)} micro KIs, skipping clustering")
        return {"created": 0, "skipped": 0}

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

    # 3. 构建无向图
    edges = {kid: [] for kid in ki_map}
    ids = list(ki_map.keys())
    threshold = 0.82

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
            for cid in child_ids[:5]:  # 最多展开 5 个子 micro
                crow = db.execute("SELECT title, content FROM knowledge_items WHERE id=?", (cid,)).fetchone()
                if crow:
                    ctext = crow[1] or ""
                    if total_chars + len(ctext) > max_content_chars:
                        ctext = ctext[:max_content_chars - total_chars] + "..."
                    children_text.append(f"[{crow[0]}]: {ctext[:500]}")
                    total_chars += len(ctext[:500])

            expanded.append(f"## {r.get('description','')}\n{chr(10).join(children_text)}")
        else:
            if total_chars + len(content) > max_content_chars:
                content = content[:max_content_chars - total_chars] + "..."
            expanded.append(f"## {r.get('description','')}\n{content[:800]}")
            total_chars += len(content[:800])

    return "\n\n".join(expanded) if expanded else ""
