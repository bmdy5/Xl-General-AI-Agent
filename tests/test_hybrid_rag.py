"""Verify Hybrid Dual-Channel RAG and Optimization."""
import asyncio
import os
import shutil
import sqlite3
import sys
import time
import math
import json
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.memory.manager import MemoryManager
from agent.memory.fts_index import _cjk_space

# 模拟一个 MockEmbeddingManager 以进行可预测的向量检索测试
# 这里我们将直接在 memory_manager 对象上进行 mock
class SimpleMockMemoryManager(MemoryManager):
    async def _get_embedding(self, text: str) -> list[float]:
        # 为不同文本生成具有可预测相似度的 768维浮点向量
        # 比如：如果含有 "image" 或 "画图"，前 10 维设为 0.8，否则设为 0.1
        vec = [0.0] * 768
        text_lower = text.lower()
        if "image" in text_lower or "画图" in text_lower or "painting" in text_lower:
            for i in range(10):
                vec[i] = 0.8
        elif "error" in text_lower or "bug" in text_lower or "错误" in text_lower:
            for i in range(10, 20):
                vec[i] = 0.8
        else:
            # 默认填充一些微弱值
            for i in range(768):
                vec[i] = 0.05
        return vec

async def test_hybrid_rag_flow():
    print("=== Testing Hybrid Dual-Channel RAG Pipeline ===")
    
    project_root = Path(__file__).resolve().parent.parent
    temp_mem_dir = project_root / "temp_test_hybrid_rag"
    if temp_mem_dir.exists():
        shutil.rmtree(temp_mem_dir)
    temp_mem_dir.mkdir(parents=True, exist_ok=True)

    # 实例化我们的 mock 内存管理器
    manager = SimpleMockMemoryManager(base_dir=str(temp_mem_dir))
    db = manager._get_db()

    # 1. 模拟插入少量数据 (N <= 200)，验证自适应全表扫描路径
    print("Inserting 5 knowledge items for small-scale test...")
    # 模拟数据 A (调试报错分类)
    db.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count)
        VALUES ('ki_debug_1', 'Python Async Loop Exception Handling', 'xl_debugging', '["async", "loop", "exception", "错误"]', 'Async errors', 'Exception details in loop...', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', 10)
    """)
    # 给它存入对应的 Mock 向量数据 (基于 _get_embedding 规则，包含 错误/exception，第 10~20维为0.8)
    vec_debug = [0.0] * 768
    for i in range(10, 20):
        vec_debug[i] = 0.8
    db.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES ('ki_debug_1', ?)", (json.dumps(vec_debug),))

    # 模拟数据 B (英文画图语义文档，通过中文 keywords 丰富解决英文语义盲区)
    db.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count)
        VALUES ('ki_draw_1', 'AI Image Generation Error Recovery', 'xl_multimedia', '["image", "generation", "error", "画图bug", "图片生成错误"]', 'Drawing recovering', 'Details about drawing bug recovery...', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', 5)
    """)
    # 存入对应的向量 (包含 image / generation，第 0~10 维为 0.8)
    vec_draw = [0.0] * 768
    for i in range(10):
        vec_draw[i] = 0.8
    db.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES ('ki_draw_1', ?)", (json.dumps(vec_draw),))

    # 写入 FTS 索引表
    db.execute("""
        INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content)
        VALUES ('ki_debug_1', ?, 'xl_debugging', ?, 'Async errors', 'Exception details in loop...')
    """, (_cjk_space('Python Async Loop Exception Handling'), _cjk_space('["async", "loop", "exception", "错误"]')))

    db.execute("""
        INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content)
        VALUES ('ki_draw_1', ?, 'xl_multimedia', ?, 'Drawing recovering', 'Details about drawing bug recovery...')
    """, (_cjk_space('AI Image Generation Error Recovery'), _cjk_space('["image", "generation", "error", "画图bug", "图片生成错误"]')))
    
    db.commit()

    # 测试 A.1: 搜索 “画图bug”
    # 在小规模路径下，即使分词匹配有限，全表向量扫描应能 100% 召回 ki_draw_1
    res = manager.search_memories("画图bug", limit=3)
    print("DEBUG RES SMALL SCALE RETRIEVED:", res)
    assert len(res) > 0, "Should recall drawing bug item"
    assert res[0]["filename"] == "ki_ki_draw_1.md", "First item should be ki_ki_draw_1"
    print("✔ Small scale Adaptive Full Table vector search passed!")

    # 2. 模拟填充大规模数据 (N > 200)，触发双通道粗筛重排路径
    print("Populating 210 padding database records for large-scale test...")
    for idx in range(210):
        k_id = f"ki_pad_{idx}"
        # 写入 padding 无关数据
        db.execute("""
            INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at, visit_count)
            VALUES (?, ?, 'xl_general', '["pad"]', 'padding data', 'nothing here to see...', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', '2026-05-23T00:00:00Z', 0)
        """, (k_id, f"Padding content for idx {idx}"))
        
        vec_pad = [0.02] * 768
        db.execute("INSERT INTO ki_embeddings (ki_id, embedding) VALUES (?, ?)", (k_id, json.dumps(vec_pad)))
        db.execute("INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content) VALUES (?, ?, 'xl_general', ?, 'padding data', 'nothing here to see...')",
                   (k_id, _cjk_space(f"Padding content for idx {idx}"), _cjk_space('["pad"]')))
    db.commit()

    # 确认 N > 200 条
    cur = db.execute("SELECT COUNT(*) FROM knowledge_items")
    N = cur.fetchone()[0]
    assert N > 200, "Should exceed 200 total items"
    print(f"Total knowledge items in DB: {N}")

    # 测试 B.1: 英文画图语义跨语言检索：搜索 “刚才小萤跟我聊到了关于画图相关的bug”
    # 核心汉字：“画图”
    # 停用词去除了：“刚才”, “跟我”, “聊到”, “关于”
    # 我们看宽幅 FTS5 能否召回 ki_draw_1，以及向量重排后相似度达标排到第一位
    start_t = time.perf_counter()
    res_large = manager.search_memories("刚才小萤跟我聊到了关于画图相关的bug", limit=3)
    duration = time.perf_counter() - start_t
    print(f"Large hybrid search took {duration*1000:.2f} ms")

    assert len(res_large) > 0, "Should recall image item in large DB"
    assert res_large[0]["filename"] == "ki_ki_draw_1.md", f"Top match should be draw doc, got: {res_large[0]['filename'] if res_large else 'None'}"
    assert duration < 0.05, "Hybrid search should complete within 50ms"
    print("✔ Large scale Adaptive Double-Channel recall passed!")

    # 测试 B.2: 意图过滤与调试纠偏
    # 搜索 “异常错误调试” 
    # ki_debug_1 分类是 xl_debugging，应该获得加权；其他 padding 虽然也带调试关键词但无关，或者由于硬相似度被淘汰
    res_debug = manager.search_memories("异常错误调试", limit=3)
    assert len(res_debug) > 0, "Should recall debug item"
    assert res_debug[0]["filename"] == "ki_ki_debug_1.md", "Top match should be debug loop item"
    print("✔ Category-specific intent weight correction passed!")

    shutil.rmtree(temp_mem_dir)
    print("🎉 All hybrid dual-channel RAG integration tests passed successfully!")

async def test_solution_a_instant_breaker():
    print("=== Testing Solution A: Instant Circuit Breaker Fallback ===")
    project_root = Path(__file__).resolve().parent.parent
    temp_mem_dir = project_root / "temp_test_solution_a"
    if temp_mem_dir.exists():
        shutil.rmtree(temp_mem_dir)
    temp_mem_dir.mkdir(parents=True, exist_ok=True)
    
    # 实体实例化真实 MemoryManager
    manager = MemoryManager(base_dir=str(temp_mem_dir))
    
    # 使用 Mock 拦截 Path.exists，模拟本地物理模型丢失的极端灾难场景
    import unittest.mock
    
    # 我们拦截 Path.exists，使其返回 False
    with unittest.mock.patch("pathlib.Path.exists", return_value=False):
        start_t = time.perf_counter()
        vec = await manager._get_embedding("刚才小萤停止了")
        duration = time.perf_counter() - start_t
        
    print(f"Breaker fallback took {duration*1000:.4f} ms")
    assert len(vec) == 768, "Should return 768-dim vector"
    assert all(x == 0.0 for x in vec), "Should fall back to all-zero vector"
    assert duration < 0.010, "Breaker should act instantly (< 10ms)"
    
    shutil.rmtree(temp_mem_dir)
    print("✔ Solution A Instant Circuit Breaker tests passed successfully!\n")

async def main():
    await test_hybrid_rag_flow()
    await test_solution_a_instant_breaker()

if __name__ == "__main__":
    asyncio.run(main())
