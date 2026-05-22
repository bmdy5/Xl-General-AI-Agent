"""Memory system - CC MEMORY.md pattern + timestamp evolution + knowledge index."""

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import time
from collections import OrderedDict

from .fts_index import create_table, populate as fts_populate, search as fts_search, rebuild
from .notes_fts import search as notes_search, index_all, scan_files

logger = logging.getLogger(__name__)

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

# save()/append_to_core() 绝对不能写入的文件（保护人设/系统配置不被覆盖）
PROTECTED_FILES: set[str] = {
    "persona_profile.json",
}

# 9 个核心记忆文件 — save() 时自动匹配追加，不再新建碎片文件
CORE_FILES: dict[str, list[str]] = {
    "user_profile.md":           ["用户", "偏好", "亮哥", "称呼", "模型配置", "情绪", "表达偏好", "个人", "profile"],
    "communication_rules.md":    ["沟通", "格式", "开场", "消息", "回复", "星号", "markdown", "纯文本", "拆分", "对话"],
    "operation_rules.md":        ["操作", "代码纪律", "搜索验证", "执行纪律", "费用", "工作流程", "workaround", "根因"],
    "xl_tool_guide.md":          ["bash", "read_file", "write_file", "edit_file", "read_image",
                                   "image2", "web_fetch", "web_search", "save_memory",
                                   "schedule_task", "避坑", "成本", "工具", "命令", "超时", "审计"],
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
        self._mem_cache = MemoryCache(capacity=50, ttl=30)
        self._note_cache = MemoryCache(capacity=50, ttl=30)

    def _get_db(self) -> sqlite3.Connection:
        """惰性初始化 SQLite + FTS5 索引，自动无损升级老数据为 CJK 高精度索引."""
        if self._db is None:
            db_path = self.base_dir / "memories.db"
            is_new = not db_path.exists()
            self._db = sqlite3.connect(str(db_path))
            self._db.execute("PRAGMA foreign_keys = ON")
            create_table(self._db)
            
            # 自动创建长期大脑关系表 knowledge_items
            self._db.execute("""
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
            self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS kis_fts
                USING fts5(ki_id, title, category, keywords, summary, content,
                           tokenize="porter unicode61")
            """)
            # 创建 768维中文增强语义向量库表
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS ki_embeddings (
                    ki_id TEXT PRIMARY KEY,
                    embedding TEXT NOT NULL,  -- 保存 JSON 格式的 768维浮点数列表
                    FOREIGN KEY(ki_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
                )
            """)
            self._db.commit()
            
            # 自动升级老 memories.db：若已有无空格的旧中文索引，自动从物理 md 重新构建
            if not is_new:
                try:
                    # 在 Python 内存中进行正则验证，这是 100% 绝对兼容和安全的
                    cur = self._db.execute("SELECT content FROM memories_fts LIMIT 50")
                    has_legacy = False
                    for row in cur:
                        content = row[0]
                        if content and re.search(r'[\u4e00-\u9fff]{2,}', content):
                            has_legacy = True
                            break
                    if has_legacy:
                        logger.info("Upgrading legacy memories_fts database for CJK space indexing...")
                        self._db.execute("DELETE FROM memories_fts")
                        self._db.commit()
                        
                        entries = self._parse_index()
                        rows_to_populate = []
                        for e in entries:
                            fname = e.get("filename", "")
                            filepath = self.base_dir / fname
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
                            fts_populate(self._db, rows_to_populate)
                            self._db.commit()
                            logger.info(f"Successfully upgraded {len(rows_to_populate)} memory files to CJK indexes!")
                except Exception as e:
                    logger.warning(f"Error upgrading memories_fts: {e}")
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
        if target_file in PROTECTED_FILES:
            logger.warning(f"append_to_core blocked: {target_file} is protected")
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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

        # MD5 精准去重
        import hashlib
        content_hash = hashlib.md5(content.strip().encode('utf-8')).hexdigest()
        hash_tag = f"<!-- hash:{content_hash} -->"
        if hash_tag in existing:
            return timestamp

        append_entry = (
            f"\n\n---\n"
            f"<!-- {timestamp} -->\n"
            f"{hash_tag}\n"
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

        self._mem_cache.invalidate_all()
        self._note_cache.invalidate_all()
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
        if filename in PROTECTED_FILES or Path(filename).name in PROTECTED_FILES:
            logger.warning(f"save blocked: {filename} is protected")
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 先匹配核心文件 → 追加而非新建碎片
        core_file = self._match_core_file(description, content)
        
        # 针对反思与审计碎片记忆的强制核心分流路由（防新建零碎物理文件）
        if not core_file and not note_path:
            name_lower = filename.lower()
            desc_lower = description.lower()
            if name_lower.startswith("reflect_") or name_lower.startswith("audit_"):
                if "user" in name_lower or "feedback" in name_lower or "user" in desc_lower or "feedback" in desc_lower:
                    core_file = "user_profile.md"
                elif "tool" in name_lower or "audit" in name_lower or "tool" in desc_lower or "audit" in desc_lower:
                    core_file = "xl_tool_guide.md"
                elif "project" in name_lower or "project" in desc_lower:
                    core_file = "xl_code_review.md"
                else:
                    core_file = "xl_debugging.md"

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
        self._mem_cache.invalidate_all()
        self._note_cache.invalidate_all()
        return timestamp

    def save_ki(self, ki_data: dict) -> str:
        """以原子事务形式将 KI 数据存入 SQLite，并同步更新高精度 CJK 全文检索表."""
        ki_id = ki_data["id"]
        title = ki_data["title"]
        category = ki_data["category"]
        keywords = ki_data["keywords"]
        if isinstance(keywords, list):
            import json
            keywords_str = json.dumps(keywords, ensure_ascii=False)
        else:
            keywords_str = str(keywords)
        summary = ki_data["summary"]
        content = ki_data["content"]
        
        from datetime import datetime, timezone as _tz
        now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        db = self._get_db()
        with db:
            # 1. 检查是否存在该 ID
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
                # FTS5 中先删除旧记录
                db.execute("DELETE FROM kis_fts WHERE ki_id = ?", (ki_id,))
            else:
                created_at = now
                visit_count = 0
                version = 1
                db.execute("""
                    INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ki_id, title, category, keywords_str, summary, content, created_at, now, now, visit_count, version))
            
            # 2. 插入 CJK 空间处理后的高精度分词全文检索表
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
            
        self._mem_cache.invalidate_all()
        return now

    def merge_ki(self, existing_id: str, title: str, category: str, keywords: list, summary: str, content: str) -> str:
        """合并并更新已有的 KI 数据，自动重用 save_ki 的强一致事务逻辑."""
        ki_data = {
            "id": existing_id,
            "title": title,
            "category": category,
            "keywords": keywords,
            "summary": summary,
            "content": content
        }
        return self.save_ki(ki_data)

    def get_ki(self, ki_id: str) -> Optional[dict]:
        """根据 ID 查询单条长期大脑的 KI 记录."""
        db = self._get_db()
        cur = db.execute("""
            SELECT id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count, version
            FROM knowledge_items WHERE id = ?
        """, (ki_id,))
        row = cur.fetchone()
        if row:
            import json
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

    async def _get_embedding(self, text: str) -> list[float]:
        """调用 LiteLLM 提取 768 维中文增强语义向量。支持从环境变量配置模型。"""
        import os
        import litellm
        model = os.getenv("MYAGENT_EMBEDDING_MODEL", "text-embedding-3-small")
        
        # 优先使用专门的 Embedding 路由配置，兜底使用 OpenAI 默认配置
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        api_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE") or None
        
        kwargs = {
            "model": model,
            "input": [text],
            "dimensions": 768,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
            
        try:
            response = await litellm.aembedding(**kwargs)
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Failed to fetch embedding: {e}")
            # 自动降级兜底：当网络或 API 故障时返回 768 维全零向量，保障小萤长脑的绝对可用性与容灾
            return [0.0] * 768

    async def save_ki_embedding(self, ki_id: str, text_to_embed: str):
        """后台异步协程任务：非阻塞为指定 KI 提取 768 维 Embedding 并原子保存至 SQLite。"""
        embedding = await self._get_embedding(text_to_embed)
        import json
        embedding_str = json.dumps(embedding)
        db = self._get_db()
        with db:
            db.execute("""
                INSERT OR REPLACE INTO ki_embeddings (ki_id, embedding)
                VALUES (?, ?)
            """, (ki_id, embedding_str))

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
        self._mem_cache.invalidate_all()
        self._note_cache.invalidate_all()

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
            self._mem_cache.invalidate_all()
            self._note_cache.invalidate_all()
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

    async def gc_and_merge_fragmented_memories(self) -> int:
        """一键垃圾回收与去冗余归档器：扫描 reflect_ 和 audit_ 的碎片小 md，
        将其去重合并至核心 md 文件，然后物理删除碎片并清理 SQLite 索引与 MEMORY.md 索引。
        返回清理合并的碎片文件数量。
        """
        if not self.base_dir.exists():
            return 0

        # 扫描符合条件的所有碎片文件
        fragments = []
        for p in self.base_dir.glob("*.md"):
            name = p.name.lower()
            if name.startswith("reflect_") or name.startswith("audit_"):
                fragments.append(p)

        if not fragments:
            return 0

        merged_count = 0
        db = self._get_db()

        for path in fragments:
            filename = path.name
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue

            # 寻找该碎片文件在 MEMORY.md 中的描述
            description = ""
            entries = self._parse_index()
            for e in entries:
                if e.get("filename") == filename:
                    description = e.get("description", "")
                    break

            if not description:
                # 兜底描述
                description = filename.replace(".md", "").replace("_", " ")

            # 进行路由分发
            name_lower = filename.lower()
            desc_lower = description.lower()
            core_file = None
            if "user" in name_lower or "feedback" in name_lower or "user" in desc_lower or "feedback" in desc_lower:
                core_file = "user_profile.md"
            elif "tool" in name_lower or "audit" in name_lower or "tool" in desc_lower or "audit" in desc_lower:
                core_file = "xl_tool_guide.md"
            elif "project" in name_lower or "project" in desc_lower:
                core_file = "xl_code_review.md"
            else:
                core_file = "xl_debugging.md"

            try:
                # 精准去重合并到核心主文件
                await self.append_to_core(core_file, description, content)

                # 物理安全删除碎片文件
                path.unlink()

                # 从 MEMORY.md 中剔除对应的索引描述
                if self.index_file.exists():
                    idx_lines = self.index_file.read_text(encoding="utf-8").split("\n")
                    new_idx_lines = [l for l in idx_lines if filename not in l]
                    self.index_file.write_text("\n".join(new_idx_lines), encoding="utf-8")

                # 从 SQLite db 索引表中清除
                try:
                    db.execute("DELETE FROM memories_fts WHERE filename=?", (filename,))
                    db.commit()
                except Exception:
                    pass

                merged_count += 1
                logger.info(f"Memory GC: Merged and removed fragment '{filename}' -> '{core_file}'")
            except Exception as e:
                logger.warning(f"Memory GC: Failed to merge '{filename}': {e}")

        # 重建索引以保证数据纯净
        if merged_count > 0:
            try:
                from .fts_index import rebuild
                rebuild(db)
            except Exception:
                pass
            self._mem_cache.invalidate_all()
            self._note_cache.invalidate_all()

        return merged_count

    def search_notes(self, query: str, limit: int = 5) -> list[dict]:
        """搜索笔记知识库（学习笔记目录）。返回 BM25 排序的分块结果。"""
        cache_key = (query, limit)
        cached_res = self._note_cache.get(cache_key)
        if cached_res is not None:
            return cached_res

        import re
        clean = re.sub(r'[^\w\u4e00-\u9fff\s]', " ", query).strip()
        if not clean or len(clean) < 2:
            return []
        try:
            notes_db = Path.home() / ".my-agent" / "notes.db"
            db = sqlite3.connect(str(notes_db))
            from .notes_fts import create_table as nt_create, sync_incremental
            nt_create(db)
            # 每次搜索前自动快速增量同步
            sync_incremental(db, Path("/Users/xiaofeng/Desktop/学习笔记/Agent开发"))
            sync_incremental(db, Path("/Users/xiaofeng/Desktop/学习笔记/后端开发"))
            res = notes_search(db, query, limit)
            self._note_cache.set(cache_key, res)
            return res
        except Exception as e:
            logger.warning(f"Error during incremental search_notes: {e}")
            return []

    def _run_async(self, coro):
        """大师级同步包装器：在各种复杂已运行或未运行的 asyncio event loop 环境下安全执行协程，彻底规避重入报错。"""
        import asyncio
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

    def search_memories(self, query: str, limit: int = 5) -> list[dict]:
        """FTS5 + 768维语义向量双通道混合检索 (RRF 融合重排 + 时序热度衰减 + 故障类别意图纠偏)"""
        cache_key = (query, limit)
        cached_res = self._mem_cache.get(cache_key)
        if cached_res is not None:
            return cached_res

        import re
        import math
        clean = re.sub(r'[^\w\u4e00-\u9fff\s]', ' ', query).strip()
        if not clean or len(clean) < 2:
            return []

        try:
            db = self._get_db()
            
            # --- 1. 通道一：FTS5 精准检索 ---
            from .fts_index import _cjk_space, search as fts_search
            fts_query = _cjk_space(clean)
            
            # (a) 检索最新知识库 KI 虚拟表
            ki_fts_rows = []
            try:
                cur = db.execute("""
                    SELECT ki_id, title, category, keywords, summary, content
                    FROM kis_fts
                    WHERE kis_fts MATCH ? LIMIT ?
                """, (fts_query, limit * 2))
                ki_fts_rows = cur.fetchall()
            except Exception as e:
                logger.warning(f"KI FTS search failed: {e}")
                
            # (b) 检索存量旧 memories 虚拟表 (保持 100% 向下兼容)
            legacy_rows = []
            try:
                legacy_rows = fts_search(db, ' OR '.join(clean.split()), limit)
                if len(legacy_rows) < 2 and clean:
                    like_legacy = _like_search(db, "memories_fts", clean, limit)
                    legacy_rows = like_legacy or legacy_rows
            except Exception:
                pass

            # --- 2. 通道二：768 维向量语义检索 ---
            ki_vector_rows = []
            try:
                # 使用大师级同步安全包装运行异步向量提取
                query_vec = self._run_async(self._get_embedding(query))
                
                # 计算 magnitude 避免除以零
                q_mag = math.sqrt(sum(x * x for x in query_vec))
                if q_mag > 0:
                    # 获取向量库中所有的向量
                    cur = db.execute("SELECT ki_id, embedding FROM ki_embeddings")
                    all_embeds = cur.fetchall()
                    
                    scored_kis = []
                    for row in all_embeds:
                        k_id = row[0]
                        try:
                            import json
                            k_vec = json.loads(row[1])
                            k_mag = math.sqrt(sum(x * x for x in k_vec))
                            if k_mag > 0:
                                dot = sum(a * b for a, b in zip(query_vec, k_vec))
                                cos_sim = dot / (q_mag * k_mag)
                                if cos_sim >= 0.60:  # 语义匹配过滤阀值
                                    scored_kis.append((k_id, cos_sim))
                        except Exception:
                            continue
                    
                    # 按相似度倒序排序，截取前 limit * 2 个
                    scored_kis.sort(key=lambda x: x[1], reverse=True)
                    ki_vector_rows = scored_kis[:limit * 2]
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

            # --- 3. 混合重排 (Reciprocal Rank Fusion, RRF) ---
            # 建立召回文档字典以进行去重合并
            # RRF 常数 k=60
            rrf_scores = {}  # ki_id -> float
            
            # (a) 处理 FTS5 召回的 KI 排名
            for rank, row in enumerate(ki_fts_rows):
                k_id = row[0]
                rrf_scores[k_id] = rrf_scores.get(k_id, 0.0) + (1.0 / (60.0 + rank))
                
            # (b) 处理 Vector 召回的 KI 排名
            for rank, (k_id, _) in enumerate(ki_vector_rows):
                rrf_scores[k_id] = rrf_scores.get(k_id, 0.0) + (1.0 / (60.0 + rank))

            # --- 4. 融合意图加权与时序/热度衰减评分 ---
            # 判断 query 中是否含报错/调试关键词
            is_debug_intent = any(w in query.lower() for w in ["错误", "报错", "调试", "bug", "error", "exception", "traceback"])
            
            merged_kis = []
            for k_id, score in rrf_scores.items():
                # 从 SQLite 获取该 KI 的完整信息以返回并计算热度
                cur = db.execute("""
                    SELECT id, title, category, keywords, summary, content, visit_count, last_hit_at, updated_at
                    FROM knowledge_items WHERE id = ?
                """, (k_id,))
                ki_row = cur.fetchone()
                if not ki_row:
                    continue
                
                title, category, summary, content = ki_row[1], ki_row[2], ki_row[4], ki_row[5]
                visit_count, last_hit_at, updated_at = ki_row[6], ki_row[7], ki_row[8]
                
                # 热度乘子： visit_count 越高频越重要
                heat_multiplier = 1.0 + 0.1 * math.log(1 + visit_count)
                
                # 意图纠偏：如果用户搜索报错且分类属于 debugging，给予 1.3 倍分流倾向加权
                intent_multiplier = 1.3 if (is_debug_intent and category == "xl_debugging") else 1.0
                
                final_score = score * heat_multiplier * intent_multiplier
                
                # 将此 KI 构造成向下兼容的 dict 格式
                merged_kis.append({
                    "content": content,
                    "description": f"[{category}] {title}",
                    "memory_type": "ki",
                    "filename": f"ki_{k_id}.md",
                    "timestamp": updated_at,
                    "score": final_score
                })
                
                # 增加一次命中统计（热度累积与时序更新，限制在事务中）
                try:
                    from datetime import datetime, timezone as _tz
                    now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    db.execute("""
                        UPDATE knowledge_items
                        SET visit_count = visit_count + 1, last_hit_at = ?
                        WHERE id = ?
                    """, (now, k_id))
                    db.commit()
                except Exception:
                    pass

            # 按融合评分排序
            merged_kis.sort(key=lambda x: x["score"], reverse=True)
            
            # --- 5. 组装与旧 memories 的向下兼容合并 ---
            res = []
            # 优先取前 limit 条高质量 KI 记忆
            res.extend(merged_kis[:limit])
            
            # 若结果没填满，由 Legacy 传统记忆补全，保证原本的功能百分百完美可用
            if len(res) < limit:
                for row in legacy_rows:
                    if len(res) >= limit:
                        break
                    # 去重，防止与新知识库内容重复
                    legacy_fname = row.get("filename", "")
                    if not any(legacy_fname in r.get("filename", "") for r in res):
                        res.append(row)
            
            # 清理 score 临时字段并缓存
            for r in res:
                r.pop("score", None)
                
            self._mem_cache.set(cache_key, res)
            return res
        except Exception as e:
            logger.error(f"Search memories hybrid engine error: {e}")
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
