"""Session persistence + cross-session FTS5 search — adapted from tinypace + hermes FTS5.

JSONL append-only + os.fsync crash-safe + SQLite FTS5 full-text index.
Cross-session: FTS5 MATCH with snippet() instead of grep.
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles

logger = logging.getLogger(__name__)

# ── CJK tokenizer for FTS5 unicode61 compatibility ────────────
_CJK_RE = re.compile(r'([一-鿿㐀-䶿　-〿＀-￯])')


def _cjk_space(text: str) -> str:
    """Insert spaces around CJK characters so unicode61 tokenizer splits each char."""
    return _CJK_RE.sub(r' \1 ', text)


class SessionHandler:
    """会话 JSONL 持久化 + SQLite FTS5 全文索引."""

    def __init__(self, session_id: str, storage_dir: Optional[str] = None):
        self.session_id = session_id

        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.home() / ".my-agent" / "sessions"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.storage_dir / f"{session_id}.jsonl"
        self.db_path = self.storage_dir / "sessions.db"
        self._init_db()

    # ── SQLite FTS5 ───────────────────────────────────────────

    def _init_db(self):
        """Create FTS5 virtual table if not exists."""
        db = sqlite3.connect(str(self.db_path))
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5("
            "session_id, role, content, timestamp, tokenize='unicode61'"
            ")"
        )
        db.commit()
        db.close()

    def _fts_insert(self, role: str, content: str):
        """Insert a single message into FTS5 index."""
        ts = datetime.now().isoformat()
        db = sqlite3.connect(str(self.db_path))
        db.execute(
            "INSERT INTO sessions_fts(session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?)",
            [self.session_id, role, _cjk_space(content), ts],
        )
        db.commit()
        db.close()

    def _fts_reindex(self, messages: list[dict]):
        """Delete all entries for this session and re-insert from messages list."""
        db = sqlite3.connect(str(self.db_path))
        db.execute("DELETE FROM sessions_fts WHERE session_id = ?", [self.session_id])
        ts = datetime.now().isoformat()
        for m in messages:
            role = m.get("role", "")
            content = str(m.get("content", ""))
            if content.strip():
                db.execute(
                    "INSERT INTO sessions_fts(session_id, role, content, timestamp) "
                    "VALUES (?, ?, ?, ?)",
                    [self.session_id, role, _cjk_space(content), ts],
                )
        db.commit()
        db.close()

    def _fts_ensure_indexed(self, messages: list[dict]):
        """Index existing messages if not already in FTS5."""
        db = sqlite3.connect(str(self.db_path))
        cur = db.execute(
            "SELECT COUNT(*) FROM sessions_fts WHERE session_id = ?",
            [self.session_id],
        )
        count = cur.fetchone()[0]
        db.close()
        if count == 0 and messages:
            self._fts_reindex(messages)

    # ── session lifecycle ─────────────────────────────────────

    async def initialize(self) -> list[dict]:
        """初始化（备份旧文件 + 加载消息 + 确保 FTS 索引）."""
        self._backup()
        messages = await self.load_messages()
        self._fts_ensure_indexed(messages)
        return messages

    def _backup(self):
        if not self.session_file.exists():
            return
        bak = self.session_file.with_suffix(".jsonl.bak")
        bak.write_bytes(self.session_file.read_bytes())

    async def load_messages(self) -> list[dict]:
        """从 JSONL 文件加载所有消息，自动修复孤儿 tool_calls."""
        if not self.session_file.exists():
            return []

        messages = []
        async with aiofiles.open(self.session_file, mode="r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return self._repair_orphan_tool_calls(messages)

    def _repair_orphan_tool_calls(self, messages: list[dict]) -> list[dict]:
        """扫描并修复孤儿 tool_calls/tool 消息."""
        assistant_tc_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id"):
                        assistant_tc_ids.add(tc["id"])

        tool_result_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                tool_result_ids.add(m["tool_call_id"])

        valid_ids = assistant_tc_ids & tool_result_ids

        repaired = []
        removed = 0
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                valid_calls = [
                    tc for tc in m["tool_calls"]
                    if tc.get("id") in valid_ids
                ]
                if valid_calls:
                    repaired.append({**m, "tool_calls": valid_calls})
                elif m.get("content"):
                    cleaned = {k: v for k, v in m.items() if k != "tool_calls"}
                    repaired.append(cleaned)
                else:
                    removed += 1
                continue

            if m.get("role") == "tool":
                if m.get("tool_call_id") in valid_ids:
                    repaired.append(m)
                else:
                    removed += 1
                continue

            repaired.append(m)

        if removed > 0:
            logger.warning(
                f"Transcript repair: removed {removed} orphan messages "
                f"({len(messages)} → {len(repaired)} total)"
            )

        return repaired

    async def replace_all(self, messages: list[dict]) -> None:
        """压缩后重写整个会话文件 + 重建 FTS 索引."""
        async with aiofiles.open(self.session_file, mode="w", encoding="utf-8") as f:
            for msg in messages:
                await f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            await f.flush()
            loop = __import__("asyncio").get_running_loop()
            await loop.run_in_executor(None, os.fsync, f.fileno())

        # 重建 FTS 索引
        self._fts_reindex(messages)

    async def append_message(self, message: dict) -> None:
        """追加一条消息到 JSONL + FTS5 索引."""
        async with aiofiles.open(self.session_file, mode="a", encoding="utf-8") as f:
            await f.write(json.dumps(message, ensure_ascii=False) + "\n")
            await f.flush()
            loop = __import__("asyncio").get_running_loop()
            await loop.run_in_executor(None, os.fsync, f.fileno())

        # 同步 FTS5 索引
        role = message.get("role", "")
        content = str(message.get("content", ""))
        if content.strip():
            self._fts_insert(role, content)

    # ── cross-session search (FTS5) ──────────────────────────

    async def search_all_sessions(
        self, query: str, llm, max_results: int = 5
    ) -> str:
        """FTS5 全文搜索历史会话，支持 snippet 上下文."""
        db = sqlite3.connect(str(self.db_path))

        # FTS5 MATCH 查询，排除当前会话
        # CJK 字符需要分字处理以匹配 unicode61 索引
        fts_query = _cjk_space(query)
        try:
            cur = db.execute(
                "SELECT session_id, role, snippet(sessions_fts, 2, '<b>', '</b>', '...', 40) "
                "FROM sessions_fts "
                "WHERE sessions_fts MATCH ? AND session_id != ? "
                "ORDER BY rank "
                "LIMIT ?",
                [fts_query, self.session_id, max_results * 3],
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            # FTS5 语法错误时（特殊字符），降级为 LIKE
            # LIKE 查询 content 已分字，query 也需同样分字
            like_q = f"%{_cjk_space(query)}%"
            cur = db.execute(
                "SELECT session_id, role, content "
                "FROM sessions_fts "
                "WHERE content LIKE ? AND session_id != ? "
                "LIMIT ?",
                [like_q, self.session_id, max_results * 3],
            )
            rows = cur.fetchall()
        finally:
            db.close()

        if not rows:
            # 降级：如果 FTS 里没数据，回退到 JSONL grep
            return await self._grep_fallback(query, llm, max_results)

        matches = []
        for row in rows:
            sid, role, snippet = row
            matches.append(f"[{sid}] {role}: {snippet}")

        return "\n".join(matches[:max_results])

    async def _grep_fallback(self, query: str, llm, max_results: int = 5) -> str:
        """原有 grep 逻辑作为降级方案（不调 LLM，省 token + 延迟）."""
        if not self.storage_dir.exists():
            return ""

        matches = []
        for f in sorted(self.storage_dir.glob("*.jsonl"), reverse=True)[:100]:
            if f.name == self.session_file.name:
                continue
            try:
                async with aiofiles.open(f, mode="r", encoding="utf-8") as fh:
                    content = await fh.read()
            except Exception:
                continue
            keywords = query.lower().split()
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in keywords):
                    try:
                        msg = json.loads(line)
                        text = str(msg.get("content", ""))[:200]
                        if text:
                            matches.append(f"[{f.stem}] {text}")
                    except json.JSONDecodeError:
                        continue
            if len(matches) >= 10:
                break

        if not matches:
            return ""
        return "\n".join(matches[:max_results])
