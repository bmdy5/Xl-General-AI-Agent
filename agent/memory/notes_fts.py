"""笔记知识库 FTS5 索引 — RAG 检索.

用于索引 /Users/xiaofeng/Desktop/学习笔记/ 下的 .md 文件。
与 memories_fts 独立，数据生命周期和检索逻辑不同。

参考: fts_index.py (同类实现), hermes-agent session_search_tool.py
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

NOTES_DIR = Path("/Users/xiaofeng/Desktop/学习笔记")


def create_table(conn: sqlite3.Connection):
    """创建笔记知识库 FTS5 表."""
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
        USING fts5(content, title, path, directory,
                   tokenize="porter unicode61")
    """)


def scan_files(base: Path = NOTES_DIR) -> list[dict]:
    """扫描目录下所有 .md 文件，返回 [{path, title, directory}]."""
    results = []
    for f in sorted(base.rglob("*.md")):
        if ".obsidian" in str(f) or ".DS_Store" in str(f):
            continue
        rel = f.relative_to(base)
        results.append({
            "path": str(rel),
            "title": f.stem,
            "directory": str(rel.parent) if str(rel.parent) != "." else "",
            "full_path": str(f),
            "mtime": os.path.getmtime(f),
        })
    return results


def chunk_text(text: str, max_chars: int = 2000) -> list[str]:
    """将长文本切成段落块，每块不超过 max_chars 字符。"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = line
        else:
            if current:
                current += "\n" + line
            else:
                current = line
    if current:
        chunks.append(current.strip())
    return chunks if chunks else [text.strip()]


def index_all(conn: sqlite3.Connection, base: Path = NOTES_DIR) -> int:
    """扫描全部 .md 文件，分块后插入 FTS5 索引。返回总块数。"""
    files = scan_files(base)
    total_chunks = 0
    conn.execute("DELETE FROM notes_fts")  # 全量重建前清理
    for f in files:
        try:
            with open(f["full_path"], "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except Exception:
            continue
        chunks = chunk_text(raw)
        for i, chunk in enumerate(chunks):
            conn.execute(
                "INSERT INTO notes_fts(content, title, path, directory) VALUES (?, ?, ?, ?)",
                (chunk[:5000], f["title"][:200], f["path"][:500], f["directory"][:500]),
            )
            total_chunks += 1
    conn.commit()
    return total_chunks


def search(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    """FTS5 搜索笔记知识库，BM25 排序。"""
    clean = re.sub(r'[^\w\u4e00-\u9fff\s]', " ", query).strip()
    if not clean or len(clean) < 2:
        return []
    fts_query = " OR ".join(clean.split())
    rows = conn.execute(
        """SELECT rowid, content, title, path, directory, rank
           FROM notes_fts WHERE notes_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (fts_query, limit),
    ).fetchall()
    return [
        {"id": r[0], "content": r[1], "title": r[2],
         "path": r[3], "directory": r[4], "rank": r[5]}
        for r in rows
    ]


def rebuild(conn: sqlite3.Connection):
    """重建 FTS5 索引."""
    conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
