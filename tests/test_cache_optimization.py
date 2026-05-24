import time
import pytest
import sqlite3
import json
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from agent.memory.index import MemoryCache, _get_embedding
from agent.memory.manager import MemoryManager
from agent.memory.context import search_memories, search_notes


# ── 1. 验证 MemoryCache 高精度局部失效 ──
def test_cache_precise_invalidation():
    # 初始化容量 200，TTL 300
    cache = MemoryCache(capacity=200, ttl=300)
    
    # 填充缓存 Key (query, limit)
    cache.set(("小萤的生日是多少", 5), "2026年5月24日")
    cache.set(("关于空调制冷功率的计算", 10), "小米空调额度835W")
    cache.set(("测试完全不相干的内容", 5), "没有任何词汇重叠")
    
    # 物理断言全部就绪
    assert cache.get(("小萤的生日是多少", 5)) == "2026年5月24日"
    assert cache.get(("关于空调制冷功率的计算", 10)) == "小米空调额度835W"
    assert cache.get(("测试完全不相干的内容", 5)) == "没有任何词汇重叠"
    
    # 对 "小萤" 写入新记忆，这应当触发 selective invalidation
    cache.invalidate_keys(keywords="小萤", text="今天小萤高高兴兴写了一段代码")
    
    # 黄金断言：小萤相关的 query 被移除了，但空调和不相干的内容得以保留！
    assert cache.get(("小萤的生日是多少", 5)) is None
    assert cache.get(("关于空调制冷功率的计算", 10)) == "小米空调额度835W"
    assert cache.get(("测试完全不相干的内容", 5)) == "没有任何词汇重叠"
    
    # 对 "空调能耗" 进行失效
    cache.invalidate_keys(keywords=["空调", "能耗"], text="空调制冷很费电")
    
    # 黄金断言：空调相关的 query 也被去除了，不相干的依然健在！
    assert cache.get(("关于空调制冷功率的计算", 10)) is None
    assert cache.get(("测试完全不相干的内容", 5)) == "没有任何词汇重叠"


# ── 2. 验证 MemoryCache Hit/Miss 命中率可观测计数器 ──
def test_cache_hit_rate_observability():
    cache = MemoryCache(capacity=10, ttl=100)
    
    # 初始状态
    assert cache.hits == 0
    assert cache.misses == 0
    assert cache.hit_rate == 0.0
    
    # 产生 Miss
    assert cache.get(("非存在Key", 5)) is None
    assert cache.misses == 1
    assert cache.hits == 0
    assert cache.hit_rate == 0.0
    
    # 产生 Hit
    cache.set(("测试Key", 5), "有值")
    assert cache.get(("测试Key", 5)) == "有值"
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.hit_rate == 50.0  # 1/2


# ── 3. 验证 m3e-base 加载失败熔断自愈（60秒冷却） ──
@pytest.mark.asyncio
async def test_embedding_circuit_breaker_cooldown():
    # 模拟 _LOCAL_MODEL_CACHE
    import agent.memory.index as idx_mod
    if hasattr(idx_mod, "_LOCAL_MODEL_CACHE"):
        delattr(idx_mod, "_LOCAL_MODEL_CACHE")
        
    manager = MagicMock()
    manager.resolve_adaptive_path = MagicMock(return_value=Path("./non_exist_model_path"))
    
    with patch.dict("os.environ", {"EMBEDDING_MODE": "local"}):
        # 第一次执行：路径不存在，应当立刻触发熔断，并记录 _last_fail_time
        res1 = await _get_embedding(manager, "测试熔断")
        assert res1 == [0.0] * 768
        
        # 检查 _LOCAL_MODEL_CACHE 的熔断标志
        cache = idx_mod._LOCAL_MODEL_CACHE
        assert cache["_m3e"] is None
        last_fail_t = cache["_last_fail_time"]
        assert last_fail_t > 0
        
        # 第二次执行（立即执行）：仍在 60 秒冷却期内，应当立即返回 0 向量，而不去执行任何 path/load 检查
        with patch("agent.core.config.settings.get") as mock_settings_get:
            await _get_embedding(manager, "测试熔断2")
            mock_settings_get.assert_not_called()  # 冷却期内直接熔断返回，不会再读取配置
            
        # 模拟 60 秒冷却结束 (时间旅行)
        cache["_last_fail_time"] = time.time() - 61.0
        
        # 第三次执行：冷却结束，应当物理尝试重新载入（会再次触发配置文件的 settings.get 调用尝试加载）
        with patch("agent.core.config.settings.get") as mock_settings_get:
            mock_settings_get.return_value = {"local_model_path": "./model/m3e-base"}
            await _get_embedding(manager, "测试熔断3")
            mock_settings_get.assert_called_once()  # 触发了重试加载！


# ── 4. 验证 RAG 嵌入向量内存缓存惰性查表降噪 ──
@pytest.mark.asyncio
async def test_rag_embedding_memory_cache():
    # 构建内存 SQLite 表并填充模拟数据
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE knowledge_items (
            id TEXT PRIMARY KEY, title TEXT, category TEXT, keywords TEXT, summary TEXT, content TEXT,
            created_at TEXT, updated_at TEXT, last_hit_at TEXT, visit_count INTEGER DEFAULT 0, version INTEGER DEFAULT 1, revision_history TEXT
        )
    """)
    db.execute("""
        CREATE TABLE ki_embeddings (
            ki_id TEXT PRIMARY KEY, embedding TEXT
        )
    """)
    db.execute("""
        CREATE VIRTUAL TABLE kis_fts USING fts5(ki_id, title, category, keywords, summary, content)
    """)
    
    # 模拟数据
    vec_data = [0.1] * 768
    db.execute("""
        INSERT INTO knowledge_items (id, title, category, keywords, summary, content, created_at, updated_at, last_hit_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("ki_test_1", "测试标题", "xl_debugging", '["测试"]', "摘要", "内容", "2026-05-24", "2026-05-24", "2026-05-24"))
    
    db.execute("""
        INSERT INTO ki_embeddings (ki_id, embedding)
        VALUES (?, ?)
    """, ("ki_test_1", json.dumps(vec_data)))
    
    db.execute("""
        INSERT INTO kis_fts (ki_id, title, category, keywords, summary, content)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("ki_test_1", "测试 标题", "xl_debugging", "测试", "摘要", "内容"))
    db.commit()
    
    # 模拟 MemoryManager
    manager = MagicMock()
    manager._get_db = MagicMock(return_value=db)
    manager._mem_cache = MemoryCache(capacity=10, ttl=100)
    manager._note_cache = MemoryCache(capacity=10, ttl=100)
    manager._vector_cache = {}
    
    # Mock embedding 提取，直接返回 [0.1]*768 向量
    manager._get_embedding = AsyncMock(return_value=vec_data)
    
    # 第一次查询：RAG 需要从 DB 读取 ki_test_1 的向量，查完后应写入 manager._vector_cache
    assert "ki_test_1" not in manager._vector_cache
    res1 = search_memories(manager, "测试", limit=5)
    assert len(res1) == 1
    assert res1[0]["filename"] == "ki_ki_test_1.md"
    
    # 黄金断言：_vector_cache 已惰性填充
    assert "ki_test_1" in manager._vector_cache
    assert manager._vector_cache["ki_test_1"] == vec_data
    
    # 我们故意把 SQLite 中的 embeddings 清空
    db.execute("DELETE FROM ki_embeddings")
    db.commit()
    
    # 清空搜索缓存，迫使其重新走语义计算
    manager._mem_cache.invalidate_all()
    
    # 第二次查询：此时 DB 已被清空！若发生 DB 查库，则无法查到任何向量；
    # 黄金断言：由于内存中已经缓存了 ki_test_1 的 768维向量，检索依然 100% 成功，证明无需再去查 SQLite！
    res2 = search_memories(manager, "测试", limit=5)
    assert len(res2) == 1
    assert res2[0]["filename"] == "ki_ki_test_1.md"


# ── 5. 验证字符串形式 Key 能够被选择性淘汰 ──
def test_cache_string_key_invalidation():
    cache = MemoryCache(capacity=10, ttl=100)
    str_key = "亮哥的暗号小萤是宇宙超级美少女"
    cache.set(str_key, "测试数据")
    
    assert cache.get(str_key) == "测试数据"
    
    # 淘汰 "小萤" 关键词
    cache.invalidate_keys(keywords="小萤")
    
    # 黄金断言：字符串形式的 Key 也能被精准拦截并安全移除！
    assert cache.get(str_key) is None


# ── 6. 验证超时条目被物理从内存字典中删除 ──
def test_cache_expired_lazy_cleanup():
    cache = MemoryCache(capacity=10, ttl=1)
    
    query_vec = [0.1] * 768
    cache.set(("测试缓存", 5), "缓存数据", embedding=query_vec)
    
    # 等待其自然超时
    time.sleep(1.2)
    
    # 执行语义检索，即使未命中，也要触发物理清理
    res = cache.get(("不相干的查询", 5), query_vec=[0.2]*768, semantic_threshold=0.85)
    assert res is None
    
    # 黄金断言：超时的数据必须物理从缓存 OrderedDict 中被剔除干净！
    assert len(cache.cache) == 0
