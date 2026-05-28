"""一次性迁移：将 .md 记忆文件导入 knowledge_items 表。幂等（已存在的跳过）。"""
import json
import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("agent.memory.migration")

_YAML_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)


def _parse_md(filepath: Path) -> dict:
    """解析 .md 文件，返回 {title, keywords, summary, content, trigger, description}。"""
    text = filepath.read_text(encoding="utf-8")
    meta = {}
    body = text
    m = _YAML_RE.match(text)
    if m:
        for line in m.group(1).strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():].strip()
    name = filepath.stem
    return {
        "name": name,
        "title": meta.get("title", name),
        "keywords": [kw.strip() for kw in meta.get("trigger", name).replace("/", ",").split(",") if kw.strip()],
        "summary": meta.get("description", body[:200].replace("\n", " ")),
        "content": body,
        "trigger": meta.get("trigger", ""),
        "description": meta.get("description", ""),
    }


async def run_migration(manager) -> dict:
    """导入所有 .md 文件到 knowledge_items。返回 {ki/experience/skill: count}。"""
    from agent.core.paths import PROJECT_ROOT

    sources = [
        (PROJECT_ROOT / "agent_memory" / "core", "ki"),
        (PROJECT_ROOT / "agent_memory" / "experiences", "experience"),
        (PROJECT_ROOT / "agent_memory" / "skills", "skill"),
    ]

    counts = {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for src_dir, ki_type in sources:
        if not src_dir.exists():
            continue
        n = 0
        for item in src_dir.iterdir():
            if not item.is_file() or not item.name.endswith(".md"):
                continue
            # 跳过索引和路由文件
            if item.name in ("MEMORY.md", "routing_rules.md"):
                continue

            try:
                parsed = _parse_md(item)
            except Exception:
                continue

            ki_id = f"migrated_{ki_type}_{hashlib.md5(item.name.encode()).hexdigest()[:12]}"

            # 幂等：已存在则跳过
            try:
                db = manager._get_db()
                cur = db.execute("SELECT 1 FROM knowledge_items WHERE id = ?", (ki_id,))
                if cur.fetchone():
                    logger.debug(f"Skip existing: {ki_id}")
                    continue
            except Exception:
                continue

            ki_data = {
                "id": ki_id,
                "title": parsed["title"],
                "category": ki_type,
                "keywords": parsed["keywords"],
                "summary": parsed["summary"],
                "content": parsed["content"],
                "ki_type": ki_type,
            }

            try:
                manager.save_ki(ki_data)
                # 生成 embedding
                embed_text = f"{parsed['title']} {parsed['summary']} {parsed['content'][:2000]}"
                try:
                    await manager.save_ki_embedding(ki_id, embed_text)
                except Exception:
                    logger.debug(f"Embedding skipped for {ki_id}")
                n += 1
            except Exception as e:
                logger.warning(f"Failed to migrate {item.name}: {e}")

        counts[ki_type] = n
        logger.info(f"Migrated {n} {ki_type} items from {src_dir}")

    return counts
