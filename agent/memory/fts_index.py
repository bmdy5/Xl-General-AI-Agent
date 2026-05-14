"""FTS5 全文搜索索引 — 支持记忆的语义检索."""
import sqlite3
from typing import Any


def create_table(conn: sqlite3.Connection):
    """创建 FTS5 全文搜索表."""
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(content, description, memory_type, filename,
                   tokenize="porter unicode61")
    """)


def populate(conn: sqlite3.Connection, rows: list[dict]):
    """批量插入记忆."""
    for row in rows:
        conn.execute(
            "INSERT INTO memories_fts(content, description, memory_type, filename) VALUES (?, ?, ?, ?)",
            (row["content"], row["description"], row["memory_type"], row["filename"]),
        )


def search(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    """FTS5 全文搜索，BM25 排序."""
    rows = conn.execute(
        """SELECT rowid, content, description, memory_type, filename, rank
           FROM memories_fts WHERE memories_fts MATCH ?
           ORDER BY rank LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [{"id": r[0], "content": r[1], "description": r[2],
            "memory_type": r[3], "filename": r[4], "rank": r[5]} for r in rows]


def rebuild(conn: sqlite3.Connection):
    conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
