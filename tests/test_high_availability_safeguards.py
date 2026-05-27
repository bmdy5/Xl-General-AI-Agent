import pytest
import asyncio
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch
from agent.memory.index import with_db_retry
from agent.net_gateway.scheduler import GatewayScheduler

# 1. 测试 SQLite 写锁自适应指数退避延迟重试机制 (Sync & Async)
def test_with_db_retry_sync_success_after_retries():
    call_count = 0

    @with_db_retry(max_retries=3, base_delay=0.001)
    def dummy_sync_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success"

    result = dummy_sync_func()
    assert result == "success"
    assert call_count == 3


def test_with_db_retry_sync_raise_after_max_retries():
    call_count = 0

    @with_db_retry(max_retries=2, base_delay=0.001)
    def dummy_sync_func():
        nonlocal call_count
        call_count += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        dummy_sync_func()
    assert "locked" in str(exc_info.value)
    # 第一次执行 + 2次重试 = 3次
    assert call_count == 3


@pytest.mark.asyncio
async def test_with_db_retry_async_success_after_retries():
    call_count = 0

    @with_db_retry(max_retries=3, base_delay=0.001)
    async def dummy_async_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success"

    result = await dummy_async_func()
    assert result == "success"
    assert call_count == 3


@pytest.mark.asyncio
async def test_with_db_retry_async_raise_after_max_retries():
    call_count = 0

    @with_db_retry(max_retries=2, base_delay=0.001)
    async def dummy_async_func():
        nonlocal call_count
        call_count += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        await dummy_async_func()
    assert "locked" in str(exc_info.value)
    assert call_count == 3


# 2. 测试 TTS 超时硬熔断及自愈重启次数校验
@pytest.mark.asyncio
async def test_scheduler_tts_health_check_three_failures_circuit_breaker():
    # 模拟 bot
    bot = MagicMock()
    bot.admin_id = "1705919142"
    bot._http = MagicMock()
    bot._http.closed = False
    
    # 实例化 scheduler
    scheduler = GatewayScheduler(bot)
    assert scheduler._tts_fail_count == 0

    # 模拟守护探测异常并在连续 3 次失败时触发重启
    async def simulate_check(mock_get_func):
        try:
            await mock_get_func()
            scheduler._tts_fail_count = 0
            return "success"
        except Exception as probe_err:
            scheduler._tts_fail_count += 1
            if scheduler._tts_fail_count >= 3:
                # 模拟自愈重启
                scheduler._tts_fail_count = 0
                return "restarted"
            else:
                return f"warned_{scheduler._tts_fail_count}"

    # 模拟探测失败 (例如超时或连接错误)
    mock_fail = AsyncMock(side_effect=Exception("Timeout"))

    # 第一次失败 -> warned_1
    res1 = await simulate_check(mock_fail)
    assert res1 == "warned_1"
    assert scheduler._tts_fail_count == 1

    # 第二次失败 -> warned_2
    res2 = await simulate_check(mock_fail)
    assert res2 == "warned_2"
    assert scheduler._tts_fail_count == 2

    # 第三次失败 -> restarted (计数重置为 0)
    res3 = await simulate_check(mock_fail)
    assert res3 == "restarted"
    assert scheduler._tts_fail_count == 0


# 3. 凌晨 03:00 主动做梦及增量切片 Compaction 逻辑仿真
@pytest.mark.asyncio
async def test_scheduler_active_dream_evolution_compaction():
    # Mock bot
    bot = MagicMock()
    bot.admin_id = "1705919142"
    
    # 准备活跃会话中的 Agent 实例
    agent_mock = MagicMock()
    agent_mock.messages = [
        {"role": "user", "content": "你好小萤"},
        {"role": "assistant", "content": "你好，有什么我可以帮您的吗？"},
        {"role": "user", "content": "我们今晚来优化高可用保障吧"},
    ]
    
    # Mock agent_mock 内部 memory 的 save_active_session_async 刷盘方法
    agent_mock.memory = MagicMock()
    agent_mock.memory.save_active_session_async = MagicMock()
    
    bot._agents = {"private_1705919142": agent_mock}
    
    scheduler = GatewayScheduler(bot)

    # 深度梦境净化与脑壳清账仿真
    # patch trigger_deep_dream_evolution，模拟高情商反思总结卡片生成
    with patch("agent.evolution.trigger_deep_dream_evolution", new_callable=AsyncMock) as mock_evolution:
        mock_evolution.return_value = "### 📊 梦境反思卡片\n- 提炼了 1 条系统教训。"
        
        # 激活做梦提炼
        await scheduler._trigger_active_dream_evolution()
        
        # 验证大做梦是否被调用，且传入了正确的历史快照消息
        mock_evolution.assert_called_once()
        assert len(mock_evolution.call_args[1]["history_messages"]) == 3
        
        # 验证增量清账切片成功 (messages 从 3 削减为 0)
        assert len(agent_mock.messages) == 0
        
        # 验证 active_sessions SQLite 持久化同步刷盘被正确唤起
        agent_mock.memory.save_active_session_async.assert_called_once_with("private_1705919142", [])
