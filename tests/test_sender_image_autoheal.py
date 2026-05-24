import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock
from agent.net_gateway.sender import MessageSender

@pytest.mark.asyncio
async def test_sender_image_autoheal():
    # 模拟 bot
    bot = MagicMock()
    bot._http = MagicMock()
    bot._http.closed = False
    
    # 模拟 post 返回 ok
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value='{"status": "ok", "retcode": 0}')
    
    # post as a context manager
    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post.__aexit__ = AsyncMock(return_value=None)
    bot._http.post = MagicMock(return_value=mock_post)
    
    sender = MessageSender(bot)
    
    # 1. 测试未加 file:/// 的绝对路径 CQ 码
    faulty_text = "扫码登录小红书 [CQ:image,file=/Users/xiaofeng/bot/qrcode_login.png] 请尽快"
    await sender.send("private", "1705919142", "", faulty_text)
    
    # 验证最终发给 OneBot 的 payload 中的 message 已经被自动纠错补全了 file:///
    post_args = bot._http.post.call_args
    assert post_args is not None
    payload = post_args[1]["json"]
    assert payload["message"] == "扫码登录小红书 [CQ:image,file=file:///Users/xiaofeng/bot/qrcode_login.png] 请尽快"
    
    # 2. 测试已经加了 file:/// 的绝对路径 CQ 码
    bot._http.post.reset_mock()
    correct_text = "扫码登录小红书 [CQ:image,file=file:///Users/xiaofeng/bot/qrcode_login.png]"
    await sender.send("private", "1705919142", "", correct_text)
    payload_correct = bot._http.post.call_args[1]["json"]
    assert payload_correct["message"] == "扫码登录小红书 [CQ:image,file=file:///Users/xiaofeng/bot/qrcode_login.png]"

    # 3. 测试 base64 格式的 CQ 码 (不带绝对路径前缀 /)
    bot._http.post.reset_mock()
    base64_text = "[CQ:image,file=base64://iVBORw0KGgoAAA]"
    await sender.send("private", "1705919142", "", base64_text)
    payload_base64 = bot._http.post.call_args[1]["json"]
    assert payload_base64["message"] == "[CQ:image,file=base64://iVBORw0KGgoAAA]"
