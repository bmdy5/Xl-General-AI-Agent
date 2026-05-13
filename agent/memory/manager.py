"""Memory system - CC MEMORY.md pattern + timestamp evolution + knowledge index."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

KNOWLEDGE_INDEX = Path(os.environ.get(
    "MYAGENT_KB",
    "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西"
)) / "知识索引.md"


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
        """Remove memory file and index entry."""
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
