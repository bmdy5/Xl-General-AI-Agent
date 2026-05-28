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


def _find_cliques(edges: dict, min_size: int = 2, max_size: int = 10) -> list[list]:
    """Bron-Kerbosch 极大团算法——要求簇内每对都相连，避免 A~B~C 链式误连。"""
    adj = {n: set(edges.get(n, [])) for n in edges}
    cliques = []

    def bron_kerbosch(r: set, p: set, x: set):
        if len(r) >= max_size:
            return
        if not p and not x and len(r) >= min_size:
            cliques.append(list(r))
            return
        u = next(iter(p | x)) if (p | x) else None
        p_minus_neighbors = p - adj.get(u, set()) if u else p
        for v in list(p_minus_neighbors):
            bron_kerbosch(r | {v}, p & adj.get(v, set()), x & adj.get(v, set()))
            p.discard(v)
            x.add(v)

    bron_kerbosch(set(), set(adj.keys()), set())
    # 按大小排序，优先去重（大团包含的小团独立处理）
    cliques.sort(key=len, reverse=True)
    return cliques


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

    # 4. 连通分量
    clusters = _find_cliques(edges, min_size=2, max_size=10)

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
