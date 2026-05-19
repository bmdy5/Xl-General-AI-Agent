"""Memory system - CC MEMORY.md pattern + timestamp evolution + knowledge index."""

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .fts_index import create_table, populate as fts_populate, search as fts_search, rebuild
from .notes_fts import search as notes_search, index_all, scan_files

logger = logging.getLogger(__name__)

# 9 个核心记忆文件 — save() 时自动匹配追加，不再新建碎片文件
CORE_FILES: dict[str, list[str]] = {
    "user_profile.md":           ["用户", "偏好", "亮哥", "称呼", "模型配置", "情绪", "表达偏好", "个人", "profile"],
    "communication_rules.md":    ["沟通", "格式", "开场", "消息", "回复", "星号", "markdown", "纯文本", "拆分", "对话"],
    "operation_rules.md":        ["操作", "代码纪律", "搜索验证", "执行纪律", "费用", "工作流程", "workaround", "根因"],
    "xl_tool_guide.md":          ["bash", "read_file", "write_file", "web_fetch", "避坑", "成本", "工具", "命令", "超时"],
    "xl_architecture.md":        ["架构", "系统设计", "模块", "组件", "缓存", "DeepSeek", "FTS5", "索引"],
    "xl_code_review.md":         ["代码审查", "review", "bug", "代码质量", "代码"],
    "xl_identity.md":            ["身份", "人格", "小萤", "自我认知", "agent定义"],
    "xl_debugging.md":           ["调试", "debug", "排查", "日志", "traceback", "错误", "报错"],
    "xl_requirement_analysis.md": ["需求分析", "方案设计", "需求理解", "需求"],
}

KB_DIR = os.getenv("MYAGENT_KB_DIR", "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西")
KNOWLEDGE_INDEX = Path(KB_DIR) / "知识索引.md"

DEFAULT_ROUTING_RULES = """\
# 知识路由规则

学习笔记根目录: /Users/xiaofeng/Desktop/学习笔记

## 路由判断
1. 跟小萤自身相关（人格、行为、成长、自学习）→ 01-小萤/自学习笔记/
2. Agent 通用技术（工具、记忆、多智能体、循环）→ 02-Agent技术/记忆系统/
3. 具体项目经验、踩坑记录、bug修复 → 06-工作记录/工程实践/
4. 用户知识（亮哥教给我的）→ 01-小萤/自学习笔记/

## 记忆类型路由
- feedback/behavior → core memory (不写笔记)
- learn/technical → 知识索引 → 上述笔记目录
- project/experience → 06-工作记录/工程实践/
- personal/identity → core memory (不写笔记)
"""


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
        self.rules_file = self.base_dir / "routing_rules.md"
        if not self.rules_file.exists():
            self.rules_file.write_text(DEFAULT_ROUTING_RULES, encoding="utf-8")
        self._db: sqlite3.Connection | None = None

    def _get_db(self) -> sqlite3.Connection:
        """惰性初始化 SQLite + FTS5 索引."""
        if self._db is None:
            self._db = sqlite3.connect(self.base_dir / "memories.db")
            create_table(self._db)
        return self._db

    @staticmethod
    def _match_core_file(description: str, content: str) -> Optional[str]:
        """根据描述和内容关键词匹配核心文件。返回文件名或 None."""
        text = f"{description} {content[:200]}"
        for fname, keywords in CORE_FILES.items():
            score = sum(1 for kw in keywords if kw in text)
            if score >= 2:
                return fname
        return None

    async def append_to_core(self, target_file: str, description: str, content: str) -> str:
        """追加到核心文件。去重，加时间戳分隔线，更新索引和 FTS5."""
        from datetime import datetime, timezone as _tz
        timestamp = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        filepath = self.base_dir / target_file

        # 确保核心文件存在
        if not filepath.exists():
            filepath.write_text(
                f"# {target_file.replace('.md','').replace('_',' ').title()}\n\n",
                encoding="utf-8"
            )

        existing = filepath.read_text(encoding="utf-8")

        # 去重
        content_clean = content[:300].replace("\n", " ").replace(" ", "")
        existing_clean = existing.replace("\n", " ").replace(" ", "")
        if content_clean in existing_clean:
            return timestamp

        append_entry = (
            f"\n\n---\n"
            f"<!-- {timestamp} -->\n"
            f"### {description}\n"
            f"{content}\n"
        )
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(append_entry)

        self._upsert_index(target_file, f"- [{description}]({target_file}) `{timestamp}`")

        try:
            db = self._get_db()
            fts_populate(db, [{
                "content": content[:5000],
                "description": description[:200],
                "memory_type": "merged",
                "filename": target_file,
                "timestamp": timestamp,
            }])
            db.commit()
        except Exception:
            pass

        return timestamp

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

        # Append recent error recipes to context
        error_content = ""
        error_log = self.base_dir / "error_log.md"
        if error_log.exists():
            try:
                recent_errors = error_log.read_text(encoding="utf-8")
                # Take last 2000 chars (most recent errors first since we append)
                if len(recent_errors) > 2000:
                    recent_errors = recent_errors[-2000:]
                if recent_errors.strip():
                    error_content = f"\n\n## 错误配方库（来源: 过往错误修复记录）\n{recent_errors}\n"
            except Exception:
                pass

        return f"\n\n{content}\n{error_content}"

    async def save(self, filename: str, description: str, content: str,
                   note_path: Optional[str] = None) -> str:
        """Save memory. If note_path given, stores pointer instead of full content."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 先匹配核心文件 → 追加而非新建碎片
        core_file = self._match_core_file(description, content)
        if core_file and not note_path:
            return await self.append_to_core(core_file, description, content)

        safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        topic_file = self.base_dir / safe_name
        is_update = topic_file.exists()

        if note_path:
            # Knowledge index mode: store pointer, not content
            index_content = (
                f"<!-- pointer -->\n"
                f"# {description}\n\n"
                f"→ 笔记位置: {note_path}\n\n"
                f"_内容存储在{note_path}，这里只做索引。_"
            )
            topic_file.write_text(index_content, encoding="utf-8")
            index_line = f"- [{description}]({note_path}) `{timestamp}`"
            mtype = "knowledge"
            update_knowledge_index("knowledge", f"{mtype} | {description} | {note_path}")
        else:
            # Core memory mode: store full content (existing behavior)
            if is_update:
                old_content = topic_file.read_text(encoding="utf-8")
                content = (
                    f"<!-- updated: {timestamp} -->\n{content}\n\n"
                    f"---\n<!-- previous version -->\n{old_content[:500]}"
                )
            topic_file.write_text(content, encoding="utf-8")
            index_line = f"- [{description}]({safe_name}) `{timestamp}`"
            mtype = description.split("]")[0].replace("[", "") if "[" in description else "other"
            update_knowledge_index("memory", f"{mtype} | {description} | {safe_name}")

        self._upsert_index(safe_name, index_line)

        # 同步到 FTS5 索引
        try:
            db = self._get_db()
            fts_populate(db, [{
                "content": content[:5000],
                "description": description[:200],
                "memory_type": mtype,
                "filename": safe_name,
                "timestamp": timestamp,
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

    def get_routing_rules(self) -> str:
        """返回当前路由规则，供 bot 读取和修改."""
        if self.rules_file and self.rules_file.exists():
            return self.rules_file.read_text(encoding="utf-8")
        return DEFAULT_ROUTING_RULES

    async def save_to_notes(self, dir_path: str, filename: str, content: str) -> Optional[str]:
        """Save knowledge to learning notes directory. Returns the full path or None."""
        try:
            import re
            from pathlib import Path as _Path
            rules = self.get_routing_rules()
            m = re.search(r'学习笔记根目录:\s*(.+)', rules)
            if not m:
                return None
            base = _Path(m.group(1).strip())
            target_dir = base / dir_path
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_name = filename.replace("/", "_").replace("\\", "_")
            if not safe_name.endswith(".md"):
                safe_name += ".md"
            filepath = target_dir / safe_name
            filepath.write_text(content, encoding="utf-8")
            return str(filepath)
        except Exception as e:
            logger.warning(f"Failed to save to notes: {e}")
            return None

    async def verify_index(self) -> list[dict]:
        """Check MEMORY.md for broken note pointers. Returns list of broken entries."""
        broken = []
        entries = self._parse_index()
        from pathlib import Path as _Path
        import re
        rules = self.get_routing_rules()
        m = re.search(r'学习笔记根目录:\s*(.+)', rules)
        base = _Path(m.group(1).strip()) if m else _Path.home() / "Desktop" / "学习笔记"

        for e in entries:
            fname = e.get("filename", "")
            # Check if it's a pointer (has directory separators or starts with 0N-)
            if "/" in fname or (fname.startswith("0") and "-" in fname[:3]):
                full_path = base / fname
                if not full_path.exists():
                    broken.append({**e, "expected_path": str(full_path)})
        return broken

    def search_notes(self, query: str, limit: int = 5) -> list[dict]:
        """搜索笔记知识库（学习笔记目录）。返回 BM25 排序的分块结果。"""
        import re
        clean = re.sub(r'[^\w\u4e00-\u9fff\s]', " ", query).strip()
        if not clean or len(clean) < 2:
            return []
        try:
            notes_db = Path.home() / ".my-agent" / "notes.db"
            db = sqlite3.connect(str(notes_db))
            from .notes_fts import create_table as nt_create, index_all
            nt_create(db)
            # 首次搜索自动索引（仅限 Agent开发/ 和 后端开发/ 目录）
            count = db.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
            if count == 0:
                count = index_all(db, Path("/Users/xiaofeng/Desktop/学习笔记/Agent开发"))
                count += index_all(db, Path("/Users/xiaofeng/Desktop/学习笔记/后端开发"))
                db.commit()
            return notes_search(db, query, limit)
        except Exception:
            return []

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        """FTS5 全文搜索记忆，BM25 排序 + LIKE 降级."""
        import re
        clean = re.sub(r'[^\w\u4e00-\u9fff\s]', ' ', query).strip()
        if not clean or len(clean) < 2:
            return []
        try:
            fts_query = ' OR '.join(clean.split())
            db = self._get_db()
            results = fts_search(db, fts_query, limit)
            # LIKE fallback for CJK queries where FTS5 tokenization fails
            if len(results) < 2 and clean:
                like_results = _like_search(db, "memories_fts", clean, limit)
                return like_results or results
            return results
        except Exception:
            return []

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

def _like_search(db, table: str, query: str, limit: int = 5) -> list[dict]:
    """LIKE 降级搜索 — CJK FTS5 分词失效时的兜底方案."""
    import re
    results = []
    keywords = [k for k in re.split(r'\s+', query) if len(k) >= 2]
    if not keywords:
        keywords = [query]
    for kw in keywords[:3]:  # 最多3个关键词
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
