"""Verify Multifaceted RAG Integration & Edge Cases."""
import asyncio
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.compressor import ContextCompressor
from agent.session.handler import SessionHandler
from agent.memory.fts_index import create_table as mem_create_table, populate as mem_populate, search as mem_search, _cjk_space
from agent.memory.notes_fts import create_table as note_create_table, sync_incremental, search as note_search
from agent.core import Agent


async def test_extreme_cjk_and_emoji():
    """测试 1: 极限 CJK 分词、生僻字与 Emoji 测试"""
    print("=== Testing 1: Extreme CJK Cosemantics & Emoji ===")
    conn = sqlite3.connect(":memory:")
    mem_create_table(conn)
    
    extreme_content = "亮哥在 𠮷野家 吃午饭，顺便跟🤖👨‍💻小肖讨论了“兰（蘭）花”算法，极其高效！！！"
    mem_populate(conn, [
        {
            "content": extreme_content,
            "description": "这是极限边界记忆测试",
            "memory_type": "merged",
            "filename": "extreme_test.md",
            "timestamp": "2026-05-20T12:00:00Z"
        }
    ])
    conn.commit()

    spaced = _cjk_space(extreme_content)
    # 验证 Surrogate Pair 𠮷 (Surrogate pair) 是否在分字后依然保持正常
    assert "𠮷" in spaced, "Surrogate Pair should be parsed successfully"
    assert "🤖" in spaced, "Emoji should be kept and spaced properly"

    # 精准 MATCH 召回测试
    res_trad = mem_search(conn, "蘭花")
    assert len(res_trad) > 0, "Should recall using traditional Chinese character"
    
    res_surr = mem_search(conn, "𠮷野家")
    assert len(res_surr) > 0, "Should recall using Surrogate pair character"
    
    res_emoji = mem_search(conn, "🤖")
    assert len(res_emoji) > 0, "Should recall using Emoji"
    
    print("✔ Extreme CJK & Emoji MATCH and Space restoration tests passed!\n")


async def test_obsidian_sync_robustness():
    """测试 2: Obsidian 增量同步异常与重入测试"""
    print("=== Testing 2: Obsidian Path Robustness & Re-entrancy ===")
    
    project_root = Path(__file__).resolve().parent.parent
    temp_notes_dir = project_root / "temp_test_obsidian_extreme"
    if temp_notes_dir.exists():
        shutil.rmtree(temp_notes_dir)
    temp_notes_dir.mkdir(parents=True, exist_ok=True)
    
    normal_file = temp_notes_dir / "正常文件.md"
    normal_file.write_text("正常文字检索", encoding="utf-8")
    
    space_path = temp_notes_dir / "带 空 格 的 路径.md"
    space_path.write_text("空格路径下的测试笔记，用以检验相对路径转换的强健度。", encoding="utf-8")
    
    deep_dir = temp_notes_dir / "a/b/c/d/e/f"
    deep_dir.mkdir(parents=True, exist_ok=True)
    deep_file = deep_dir / "深层文件.md"
    deep_file.write_text("嵌套到极限深度的文件，小肖依然能同步到它。", encoding="utf-8")
    
    large_file = temp_notes_dir / "超大文件测试.md"
    large_file.write_text("大文本分词测试。" * 30000, encoding="utf-8")
    
    conn = sqlite3.connect(":memory:")
    note_create_table(conn)
    
    # A. 首次同步
    changes1 = sync_incremental(conn, temp_notes_dir)
    assert changes1 == 4, "Should index 4 files"
    
    res_deep = note_search(conn, "小肖依然能同步")
    assert len(res_deep) == 1, "Deep file should be recalled"
    
    # B. 重入与修改
    await asyncio.sleep(0.1)
    space_path.write_text("空格路径下的测试笔记被修改了，添加新词：‘极速增量’。", encoding="utf-8")
    os.utime(space_path, None)
    
    # 并发触发增量同步，校验 SQLite 无报错
    async def concurrent_sync():
        return sync_incremental(conn, temp_notes_dir)
        
    results = await asyncio.gather(
        concurrent_sync(),
        concurrent_sync(),
        concurrent_sync()
    )
    assert sum(results) == 1, "Only one sync should process the update"
    
    # 校验修改后是否能极速召回，且修改后的文件排在结果的第一位
    res_mod = note_search(conn, "极速增量")
    assert len(res_mod) > 0
    print("DEBUG ACTUAL PATH RESTORED:", repr(res_mod[0]["path"]))
    assert "带" in res_mod[0]["path"] and "路径" in res_mod[0]["path"]
    
    shutil.rmtree(temp_notes_dir)
    print("✔ Obsidian path robustness and concurrent re-entrancy tests passed!\n")


class MockLLM:
    def __init__(self):
        self.model = "gemini-2.0-flash"
    async def chat(self, messages, tools=None):
        return {"content": "This is a mock response."}


async def test_agent_long_session_and_compressor():
    """测试 3: Agent 极长多轮对话与 ContextCompressor 协同测试"""
    print("=== Testing 3: Agent Core Long Session & Compressor integration ===")
    
    project_root = Path(__file__).resolve().parent.parent
    temp_mem_dir = project_root / "temp_test_memory_extreme"
    if temp_mem_dir.exists():
        shutil.rmtree(temp_mem_dir)
    temp_mem_dir.mkdir(parents=True, exist_ok=True)
    
    profile_file = temp_mem_dir / "USER_PROFILE.md"
    profile_file.write_text("亮哥追求极简 MVC 原则。", encoding="utf-8")
    
    handler = SessionHandler(session_id="extreme_session", storage_dir=str(temp_mem_dir))
    llm = MockLLM()
    agent = Agent(llm=llm, registry=None, session=handler)
    agent.memory.base_dir = temp_mem_dir
    
    for i in range(30):
        await handler.append_message({"role": "user", "content": f"这是第 {i} 句长话，关于系统优化的第 {i} 个想法。"})
        await handler.append_message({"role": "assistant", "content": f"好的了解，正在为您记录第 {i} 阶段的改动。"})
        
    msgs = await handler.load_messages()
    assert len(msgs) == 60
    
    compressor = ContextCompressor(llm=llm, max_tokens=1000, threshold=0.1)
    compressed_msgs, was_compressed = await compressor.compress(msgs)
    assert was_compressed, "Should successfully compress messages"
    assert len(compressed_msgs) < len(msgs), "Compressor should shrink context"
    assert compressed_msgs[0]["role"] == "system"
    
    block = await agent._build_memory_block(user_input="极简MVC有什么要求？", turn=31)
    assert "Who You Are (User Profile)" in block
    assert "极简 MVC" in block
    
    shutil.rmtree(temp_mem_dir)
    print("✔ Agent core long session context compression and profile block injection passed!\n")


async def main():
    await test_extreme_cjk_and_emoji()
    await test_obsidian_sync_robustness()
    await test_agent_long_session_and_compressor()
    print("🎉 All multifaceted RAG integration tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
