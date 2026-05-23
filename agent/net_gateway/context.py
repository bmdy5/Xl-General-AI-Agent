import time
import logging

logger = logging.getLogger("net_gateway.context")

class GatewayContext:
    """网关统一状态总线与抽象通信上下文，彻底解耦子模块，杜绝循环导入。"""
    
    def __init__(self, admin_id="", factory=None):
        self.admin_id = admin_id
        self._factory = factory
        
        # 共享状态变量，多模块共用
        self._agents = {}
        self._last_voice_time = 0.0
        self._last_receive_time = {}  # session_key -> float
        
        # 动态绑定长连接发包接口，避免与 bot.py 物理硬耦合
        self.send_handler = None
        self.send_chunk_handler = None
        self.send_voice_handler = None

    async def send_msg(self, msg_type, user_id, group_id, text, skip_delay=False):
        """路由发送文本消息。"""
        if self.send_handler:
            if skip_delay:
                await self.send_handler(msg_type, user_id, group_id, text, skip_delay=skip_delay)
            else:
                await self.send_handler(msg_type, user_id, group_id, text)
        else:
            logger.warning(f"send_msg handler not bound! Context skipped message: {text[:50]}")

    async def send_chunk(self, msg_type, user_id, group_id, text):
        """路由发送流式消息块。"""
        if self.send_chunk_handler:
            await self.send_chunk_handler(msg_type, user_id, group_id, text)
        else:
            await self.send_msg(msg_type, user_id, group_id, text)

    async def send_voice(self, msg_type, user_id, group_id, text, style, is_test=False):
        """路由发送语音消息。"""
        if self.send_voice_handler:
            await self.send_voice_handler(msg_type, user_id, group_id, text, style, is_test)
        else:
            logger.warning(f"send_voice handler not bound! Context skipped voice text: {text[:50]}")
