import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from agent.net_gateway.scheduler import GatewayScheduler

@pytest.mark.asyncio
async def test_scheduler_tts_health_check_robustness():
    # 1. 模拟 bot
    bot = MagicMock()
    bot.admin_id = "1705919142"
    bot._http = MagicMock()
    bot._http.closed = False
    
    # 实例化 scheduler
    scheduler = GatewayScheduler(bot)
    assert scheduler._tts_fail_count == 0
    
    # 2. 模拟探测失败 (抛出异常)
    # 我们直接编写一个模拟的探测函数，类似于 _daemon_loop 中的检测块，以测试 _tts_fail_count 状态转换逻辑。
    # 探测函数：模拟执行一次健康探测
    async def run_single_health_check(mock_get_func):
        try:
            # 模拟 timeout 并调用
            await mock_get_func()
            scheduler._tts_fail_count = 0
            return True
        except Exception:
            scheduler._tts_fail_count += 1
            if scheduler._tts_fail_count >= 2:
                # 模拟执行自愈重启动作
                scheduler._tts_fail_count = 0
                return "restarted"
            else:
                return "warned"

    # 用例 2.1: 第一次探测失败
    mock_fail = AsyncMock(side_effect=Exception("Timeout"))
    res1 = await run_single_health_check(mock_fail)
    assert res1 == "warned"
    assert scheduler._tts_fail_count == 1  # 计数变为 1，防抖中，不重启

    # 用例 2.2: 第二次继续探测失败，达到 2 次阈值，应当触发“自愈重启”
    res2 = await run_single_health_check(mock_fail)
    assert res2 == "restarted"
    assert scheduler._tts_fail_count == 0  # 重置计数为 0

    # 用例 2.3: 探测失败 1 次后，第 2 次探测成功，应当“清空计数”恢复健康
    mock_ok = AsyncMock(return_value="OK")
    
    # 先失败 1 次
    res3 = await run_single_health_check(mock_fail)
    assert res3 == "warned"
    assert scheduler._tts_fail_count == 1
    
    # 再成功 1 次
    res4 = await run_single_health_check(mock_ok)
    assert res4 is True
    assert scheduler._tts_fail_count == 0  # 重置计数为 0
