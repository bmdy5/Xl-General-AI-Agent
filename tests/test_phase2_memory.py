"""Verify Phase 2: Token optimization (keyword extraction) + Memory merge."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.memory_tool import _find_similar_memory, _merge_memories
from agent.memory.manager import MemoryManager


def _keyword_score(keywords: list[str], text: str) -> float:
    text_lower = text.lower()
    score = 0.0
    for kw in keywords:
        if kw.lower() in text_lower:
            score += 1.0
    return score


def test_keyword_score():
    """Keyword scoring for memory ranking."""
    assert _keyword_score(["python", "async"], "Python async programming tips") == 2.0
    assert _keyword_score(["python", "async"], "Docker deployment guide") == 0.0
    assert _keyword_score(["部署", "docker"], "Docker 部署最佳实践") == 2.0
    print("✅ _keyword_score: 2/2 match, 0/2, 2/2 CJK OK")


async def test_find_similar_memory():
    """Detect same-topic existing memory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mm = MemoryManager(base_dir=tmpdir)
        await mm.save("coding_prefs", "[user] Python coding preferences",
                      "Use pytest for testing. No mocks.")
        await mm.save("deploy_notes", "[project] Deployment notes",
                      "Deploy via GitHub Actions to Vercel.")

        # Exact filename match
        result = await _find_similar_memory(mm, "Python coding prefs", "coding_prefs")
        assert result is not None, "Should find exact filename match"
        assert "pytest" in result["content"]
        print(f"✅ Exact match: {result['filename']}")

        # Keyword overlap: "coding Python" → 2 keywords overlap with "Python coding preferences"
        result = await _find_similar_memory(mm, "coding Python guide", "python_guide")
        assert result is not None, "Should find keyword-overlap match"
        print(f"✅ Keyword overlap match: {result['filename']}")

        # No match
        result = await _find_similar_memory(mm, "React frontend guide", "react_guide")
        assert result is None, "Should not match unrelated topic"
        print("✅ No match for unrelated topic")


async def test_merge_memories():
    """LLM merge of old + new memory."""
    # Mock LLM
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock(return_value={
        "content": "Use pytest for testing. Prefer integration tests without mocks."
    })
    mock_ctx = MagicMock()
    mock_ctx.llm = mock_llm

    old = {"filename": "test.md", "content": "Use pytest for testing. No mocks.",
           "description": "[user] testing prefs"}
    result = await _merge_memories(
        mock_ctx, old, "testing prefs v2",
        "Use pytest. Prefer integration tests over mocking."
    )
    assert result is not None
    assert "integration" in result
    mock_llm.chat.assert_called_once()
    print(f"✅ Merge result: {result[:80]}...")


async def main():
    test_keyword_score()
    await test_find_similar_memory()
    await test_merge_memories()
    print("\n🎉 All Phase 2 tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
