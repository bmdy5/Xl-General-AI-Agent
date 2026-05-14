"""Memory system - CC MEMORY.md pattern + timestamp evolution + knowledge index."""

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fts_index import create_table, populate as fts_populate, search as fts_search, rebuild

KB_DIR = os.getenv("MYAGENT_KB_DIR", "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西")
KNOWLEDGE_INDEX = Path(KB_DIR) / "知识索引.md"


def update_knowledge_index(section: str, entry: str):
    """Append to knowledge index (non-blocking)."""
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        marker = f"<!-- AUTO: {section} -->"
        line = f"| {now} | {entry} |"
        content = KNOWLEDGE_INDEX.read_text(encoding="utf-8")
        if marker in content:
            content = content.replace(marker, f"{marker}\n{line}")
            KNOWLEDGE_INDEX.write_text(content, encoding="utf-8")
    except Exception:
        pass


class MemoryManager:
    """Long-term memory manager with timestamp evolution."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".my-agent" / "memory"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base_dir / "MEMORY.md"
        self._db: sqlite3.Connection | None = None

    def _get_db(self) -> sqlite3.Connection:
        """惰性初始化 SQLite + FTS5 索引."""
        if self._db is None:
            self._db = sqlite3.connect(self.base_dir / "memories.db")
            create_table(self._db)
        return self._db

    async def load_context(self) -> str:
        """Read MEMORY.md, sort by timestamp, latest first."""
        if not self.index_file.exists():
            return ""
        entries = self._parse_index()
        if not entries:
            return ""
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        seen = set()
        deduped = []
        for e in entries:
            fname = e.get("filename", "")
            if fname not in seen:
                seen.add(fname)
                deduped.append(e)
        lines = ["# Memory (cross-session, latest first)\n"]
        for e in deduped:
            ts = e.get("timestamp", "")[:19]
            lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")
        content = "\n".join(lines)
        max_bytes = 25 * 1024
        encoded = content.encode("utf-8")
        if len(encoded) > max_bytes:
            content = encoded[:max_bytes].decode("utf-8", errors="ignore")
            content += "\n\n... (truncated)"
        return f"\n\n{content}\n"

    async def save(self, filename: str, description: str, content: str) -> str:
        """Save memory with auto timestamp. Returns timestamp string."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        topic_file = self.base_dir / safe_name
        is_update = topic_file.exists()
        if is_update:
            old_content = topic_file.read_text(encoding="utf-8")
            content = (
                f"<!-- updated: {timestamp} -->\n{content}\n\n"
                f"---\n<!-- previous version -->\n{old_content[:500]}"
            )
        topic_file.write_text(content, encoding="utf-8")
        index_line = f"- [{description}]({safe_name}) `{timestamp}`"
        self._upsert_index(safe_name, index_line)
        mtype = description.split("]")[0].replace("[", "") if "[" in description else "other"
        update_knowledge_index("memory", f"{mtype} | {description} | {safe_name}")
        # 同步到 FTS5 索引
        try:
            db = self._get_db()
            fts_populate(db, [{
                "content": content[:5000],
                "description": description[:200],
                "memory_type": mtype,
                "filename": safe_name,
            }])
            db.commit()
        except Exception:
            pass
        return timestamp

    def _upsert_index(self, filename: str, new_line: str):
        """Update index: replace existing or append new."""
        if self.index_file.exists():
            existing = self.index_file.read_text(encoding="utf-8")
            pattern = re.compile(rf"^- \[.*\]\({re.escape(filename)}\)")
            if pattern.search(existing):
                new_text = pattern.sub(new_line, existing)
                self.index_file.write_text(new_text, encoding="utf-8")
                return
            with open(self.index_file, "a", encoding="utf-8") as f:
                f.write(new_line + "\n")
        else:
            self.index_file.write_text(f"# Memory Index\n\n{new_line}\n", encoding="utf-8")

    def _parse_index(self) -> list[dict]:
        """Parse MEMORY.md into list of entries."""
        if not self.index_file.exists():
            return []
        entries = []
        for line in self.index_file.read_text(encoding="utf-8").split("\n"):
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

    async def remove(self, filename: str):
        """Remove memory file, index entry, and FTS5 index."""
        safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        topic_file = self.base_dir / safe_name
        if topic_file.exists():
            topic_file.unlink()
        if self.index_file.exists():
            lines = self.index_file.read_text(encoding="utf-8").split("\n")
            new_lines = [l for l in lines if safe_name not in l]
            self.index_file.write_text("\n".join(new_lines), encoding="utf-8")
        # 从 FTS5 删除
        try:
            db = self._get_db()
            db.execute("DELETE FROM memories_fts WHERE filename=?", (safe_name,))
            db.commit()
        except Exception:
            pass

    async def get_entry(self, filename: str) -> Optional[str]:
        """Read memory file content."""
        safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        topic_file = self.base_dir / safe_name
        if topic_file.exists():
            return topic_file.read_text(encoding="utf-8")
        return None

    def list_memories(self) -> list[str]:
        """List all memory entries, latest first."""
        entries = self._parse_index()
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return [
            f"- [{e['description']}]({e['filename']}) `{e.get('timestamp', '')}`"
            for e in entries
        ]

    async def build_user_profile(self, llm) -> str:
        """合成用户画像（抄 hermes Honcho）：读取所有 user+feedback 记忆 → LLM 一次合成."""
        entries = self._parse_index()
        user_facts = []
        for e in entries:
            desc = e.get("description", "")
            fname = e.get("filename", "")
            if "[user]" in desc or "[feedback]" in desc:
                content = await self.get_entry(fname)
                if content:
                    clean = content.split("<!-- previous version -->")[0].strip()[:500]
                    user_facts.append(clean)
        if not user_facts:
            return ""

        profile_file = self.base_dir / "USER_PROFILE.md"
        prompt = (
            "从以下关于用户的事实和反馈中，合成一段深层用户画像（100字以内）。\n"
            "不是复述事实，而是描述'这是一个什么样的人'：\n"
            "工作风格、决策偏好、技术品味、沟通习惯、核心价值观。\n\n"
            + "\n---\n".join(user_facts)
        )
        try:
            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            profile = response.get("content", "").strip()
            if profile:
                profile_file.write_text(profile, encoding="utf-8")
                return f"\n\n## Who You Are (User Profile)\n{profile}\n"
        except Exception:
            pass
        # 使用缓存的 profile
        if profile_file.exists():
            return f"\n\n## Who You Are (User Profile)\n{profile_file.read_text(encoding='utf-8')}\n"
        return ""
