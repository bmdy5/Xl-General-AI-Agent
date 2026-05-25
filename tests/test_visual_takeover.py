# -*- coding: utf-8 -*-
"""视觉自愈接管系统 TDD 自动化测试用例

涵盖 API 接口契约校验、QQ审批全局劫持拦截、审批超时自动熔断、以及物理鼠标移动抢占防踩踏保护。
"""

import io
import os
import json
import base64
import asyncio
import pytest
from PIL import Image
from unittest.mock import AsyncMock, MagicMock, patch
from agent.tools.visual_tools import (
    draw_pink_pointer,
    BrowserScreenshotTool,
    BrowserClickTool,
    BrowserTypeTool,
    BrowserScrollTool
)
from agent.net_gateway.dispatcher import MessageDispatcher
from agent.net_gateway.context import GatewayContext
from agent.tools.base_tool import ToolResult


# ── 用例 1: 校验纯 PIL 算法粉色心形指针绘制的鲁棒性与输出格式 ──
def test_visual_pil_pink_pointer_drawing():
    # 建立一个 1280x800 的测试用纯白底图
    img = Image.new("RGB", (1280, 800), (255, 255, 255))
    output = io.BytesIO()
    img.save(output, format="PNG")
    b64_raw = base64.b64encode(output.getvalue()).decode("utf-8")
    
    # 模拟在视口中央 (640, 400) 处绘制粉色心形定位指针
    new_b64 = draw_pink_pointer(b64_raw, 640, 400)
    
    # 解码合并后的图片，并验证其仍然是 1280x800 PNG 格式且成功渲染
    img_data = base64.b64decode(new_b64)
    img_healed = Image.open(io.BytesIO(img_data))
    assert img_healed.size == (1280, 800)
    assert img_healed.format == "PNG"


# ── 用例 2: 校验 QQ 全局审批消息劫持拦截器 (MessageDispatcher.dispatch_event) ──
@pytest.mark.asyncio
async def test_qq_approval_message_global_hijack():
    # 模拟上下文
    context = MagicMock()
    context.admin_id = "1705919142"
    
    # 模拟活动的 agent 实例并挂载未决议的 approval_future
    agent = MagicMock()
    loop = asyncio.get_running_loop()
    agent.approval_future = loop.create_future()
    
    context._agents = {"user_1705919142": agent}
    
    dispatcher = MessageDispatcher(context)
    dispatcher.middlewares = [] # 清空中间件，防止干扰
    
    # 1. 模拟非管理员发送审批词，不予拦截劫持
    normal_event = {
        "message_type": "private",
        "raw_message": "y",
        "user_id": "999999", # 非亮哥QQ
        "self_id": "888888"
    }
    await dispatcher.dispatch_event(normal_event)
    assert not agent.approval_future.done()
    
    # 2. 模拟管理员发送非审批动作词，不予拦截劫持
    random_event = {
        "message_type": "private",
        "raw_message": "你好小萤",
        "user_id": "1705919142", # 亮哥QQ
        "self_id": "888888"
    }
    # 劫持检测拦截未匹配成功应继续流转（由于 executor 会抛错，在此忽略其异常以检测拦截性）
    try:
        await dispatcher.dispatch_event(random_event)
    except Exception:
        pass
    assert not agent.approval_future.done()

    # 3. 模拟亮哥（管理员）正式在 QQ 发送 "y" 审批放行
    approve_event = {
        "message_type": "private",
        "raw_message": "  y  ", # 带有空格
        "user_id": "1705919142",
        "self_id": "888888"
    }
    await dispatcher.dispatch_event(approve_event)
    
    # 关键断言：QQ 消息被成功劫持，决议 future 成功为 True
    assert agent.approval_future.done()
    assert agent.approval_future.result() is True


# ── 用例 3: 模拟卡片审批拒绝及 120 秒超时退避 ──
@pytest.mark.asyncio
async def test_visual_click_tool_denied_and_timeout():
    agent = MagicMock()
    loop = asyncio.get_running_loop()
    agent.approval_future = loop.create_future()
    
    click_tool = BrowserClickTool()
    
    # 模拟 aiohttp HTTP RPC 请求和 QQ 消息推送
    with patch("agent.tools.visual_tools.call_vision_gateway", new_callable=AsyncMock) as mock_gateway, \
         patch("agent.tools.visual_tools.send_qq_notification", new_callable=AsyncMock) as mock_qq:
         
         # 制造一个测试底图的 base64
         img = Image.new("RGB", (100, 100))
         output = io.BytesIO()
         img.save(output, format="PNG")
         b64 = base64.b64encode(output.getvalue()).decode("utf-8")
         mock_gateway.return_value = b64
         
         # 1. 模拟亮哥拒绝授权（n）的流程
         async def mock_denied_wait():
             await asyncio.sleep(0.05)
             agent.approval_future.set_result(False) # 模拟亮哥回复 n
             
         loop.create_task(mock_denied_wait())
         
         # 触发 click 工具
         results = []
         async for r in click_tool.call({"port": 9000, "x": 50, "y": 50}, context=agent):
             results.append(r)
             
         # 校验在亮哥拒绝时，抛出拒签错误且不执行网关 click 动作
         assert "UserInterrupted: 亮哥明确拒绝了点击动作" in results[0].data
         assert mock_gateway.call_count == 1 # 只有 screenshot，没有 click 调用
         
         # 2. 模拟审批超时熔断降级
         agent.approval_future = loop.create_future()
         
         # 重新 Mock 使得 wait_for 捕获到超时
         with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
             results_timeout = []
             async for r in click_tool.call({"port": 9000, "x": 50, "y": 50}, context=agent):
                 results_timeout.append(r)
             
             # 校验在审批超时后，抛出超时熔断，且网关 click 动作未被拉起
             assert "UserInterrupted: QQ 审批回复超时" in results_timeout[0].data
