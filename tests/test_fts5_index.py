"""Verify SQLite FTS5 index layer works correctly."""
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.session.handler import SessionHandler, _cjk_space


async def test_fts5_indexing():
    """Test that messages are indexed in FTS5 and searchable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        h = SessionHandler("test_session", storage_dir=tmpdir)

        # 1. Write messages
        await h.append_message({"role": "user", "content": "我喜欢用 pytest 写测试"})
        await h.append_message({"role": "assistant", "content": "好的记住了"})
        await h.append_message({"role": "user", "content": "帮我部署到生产环境"})

        # 2. Verify JSONL file
        lines = h.session_file.read_text().strip().split("\n")
        assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"
        print("✅ JSONL: 3 messages written")

        # 3. Verify FTS5 index
        db = sqlite3.connect(str(h.db_path))
        cur = db.execute("SELECT COUNT(*) FROM sessions_fts WHERE session_id = 'test_session'")
        count = cur.fetchone()[0]
        assert count == 3, f"Expected 3 indexed, got {count}"
        print(f"✅ FTS5: {count} messages indexed")

        # 4. Test FTS5 search
        # snippet column index: 0=session_id, 1=role, 2=content
        cur = db.execute(
            "SELECT snippet(sessions_fts, 2, '', '', '...', 20) "
            "FROM sessions_fts WHERE sessions_fts MATCH 'pytest'"
        )
        results = cur.fetchall()
        assert len(results) > 0, "No results for 'pytest'"
        assert "pytest" in results[0][0], f"Snippet missing 'pytest': {results[0][0]}"
        print(f"✅ FTS5 search 'pytest': {results[0][0]}")

        cur = db.execute(
            "SELECT snippet(sessions_fts, 2, '', '', '...', 20) "
            "FROM sessions_fts WHERE sessions_fts MATCH ?",
            [_cjk_space("部署")]
        )
        assert len(cur.fetchall()) > 0, "No results for '部署'"
        print("✅ FTS5 search '部署': found (CJK OK)")

        # 5. Test replace_all maintains index
        await h.replace_all([
            {"role": "user", "content": "压缩后的消息"},
            {"role": "assistant", "content": "收到"},
        ])
        db = sqlite3.connect(str(h.db_path))
        cur = db.execute("SELECT COUNT(*) FROM sessions_fts WHERE session_id = 'test_session'")
        count = cur.fetchone()[0]
        assert count == 2, f"Expected 2 after replace_all, got {count}"
        print(f"✅ replace_all: FTS index rebuilt, {count} messages")

        # 6. Test _ensure_indexed for existing messages
        h2 = SessionHandler("test_session", storage_dir=tmpdir)
        msgs = await h2.load_messages()
        assert len(msgs) == 2, f"Expected 2 messages loaded, got {len(msgs)}"
        h2._fts_ensure_indexed(msgs)
        db = sqlite3.connect(str(h2.db_path))
        cur = db.execute("SELECT COUNT(*) FROM sessions_fts WHERE session_id = 'test_session'")
        # Should still be 2 (already indexed by replace_all)
        count = cur.fetchone()[0]
        assert count == 2, f"Expected 2, got {count}"
        print("✅ _ensure_indexed: idempotent (no double insert)")

        db.close()
        print("\n🎉 All FTS5 tests passed!")


async def test_search_all_sessions():
    """Test cross-session search with FTS5."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # session A
        h1 = SessionHandler("session_a", storage_dir=tmpdir)
        await h1.append_message({"role": "user", "content": "Python 异步编程的最佳实践"})

        # session B (current)
        h2 = SessionHandler("session_b", storage_dir=tmpdir)
        await h2.append_message({"role": "user", "content": "今天天气真好"})

        # Search from session B should find session A's content
        # (no LLM, testing raw FTS5 match only)
        db = sqlite3.connect(str(h2.db_path))
        cur = db.execute(
            "SELECT session_id, snippet(sessions_fts, 2, '', '', '...', 20) "
            "FROM sessions_fts WHERE sessions_fts MATCH ? AND session_id != 'session_b'",
            [_cjk_space("异步编程")]
        )
        rows = cur.fetchall()
        db.close()
        assert len(rows) > 0, "Cross-session search failed"
        assert rows[0][0] == "session_a", f"Expected session_a, got {rows[0][0]}"
        assert "异" in rows[0][1] and "步" in rows[0][1]
        print(f"✅ Cross-session FTS5 search: [{rows[0][0]}] {rows[0][1]}")

        print("🎉 search_all_sessions test passed!")


if __name__ == "__main__":
    asyncio.run(test_fts5_indexing())
    asyncio.run(test_search_all_sessions())
