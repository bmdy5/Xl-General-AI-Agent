import os
import re
import time
import json
import html
import random
import asyncio
import logging
from .tts import parse_voice_test_command, send_voice
from .carrier import CSMAController
from .presenter import StreamPresenter
from .middleware import get_default_middlewares

logger = logging.getLogger("net_gateway.dispatcher")

class MessageDispatcher:
    """QQ 消息分发路由器，负责消息解析、白名单拦截、疲劳打盹状态机与 ReAct 调用"""
    
    def __init__(self, context, bot=None):
        self.context = context
        self.bot = bot
        self.bus = CSMAController(context)
        
        from .executor import AgentExecutor
        self.executor = AgentExecutor(self.context, dispatcher=self)
        
        # 共享状态属性，与 QQGateway 桥接兼容，保证单元测试完美运行
        self._waiting_podcast_topic = {} # session_key -> bool
        self._podcast_choices = {}       # session_key -> list[str]
        self._pending_perms = self.executor._pending_perms
        
        # 配置从环境变量同步
        self.admin_id = self.context.admin_id
        
        from .fatigue_manager import FatigueManager
        self.fatigue_manager = FatigueManager(self)

        from .security import SecurityManager
        self.security_manager = SecurityManager(self)
        self.middlewares = get_default_middlewares()

    @property
    def _fatigue_levels(self) -> dict:
        return self.fatigue_manager._fatigue_levels

    @_fatigue_levels.setter
    def _fatigue_levels(self, value: dict):
        self.fatigue_manager._fatigue_levels = value

    @property
    def _sleep_modes(self) -> dict:
        return self.fatigue_manager._sleep_modes

    @_sleep_modes.setter
    def _sleep_modes(self, value: bool):
        self.fatigue_manager._sleep_modes = value

    @property
    def _active_sleep_tasks(self) -> dict:
        return self.fatigue_manager._active_sleep_tasks

    @_active_sleep_tasks.setter
    def _active_sleep_tasks(self, value: dict):
        self.fatigue_manager._active_sleep_tasks = value

    @property
    def _last_message_times(self) -> dict:
        return self.fatigue_manager._last_message_times

    @_last_message_times.setter
    def _last_message_times(self, value: dict):
        self.fatigue_manager._last_message_times = value

    @property
    def fatigue_sleep_seconds(self) -> float:
        return self.fatigue_manager.fatigue_sleep_seconds

    @fatigue_sleep_seconds.setter
    def fatigue_sleep_seconds(self, value: float):
        self.fatigue_manager.fatigue_sleep_seconds = value

    @property
    def _private_chat_paused(self) -> bool:
        return self.security_manager._private_chat_paused

    @_private_chat_paused.setter
    def _private_chat_paused(self, value: bool):
        self.security_manager._private_chat_paused = value

    @property
    def _non_white_cache(self) -> dict:
        return self.security_manager._non_white_cache

    @_non_white_cache.setter
    def _non_white_cache(self, value: dict):
        self.security_manager._non_white_cache = value

    async def _generate_private_fatigue_announcement(self, user_id: str, sender_name: str = "") -> str:
        """物理私聊打盹宣告代理"""
        return await self.fatigue_manager._generate_private_fatigue_announcement(user_id, sender_name)

    async def _generate_fatigue_announcement(self, group_id: str) -> str:
        """物理群聊打盹宣告代理"""
        return await self.fatigue_manager._generate_fatigue_announcement(group_id)

    async def _private_sleep_and_dream_process(self, session_key: str, user_id: str, agent: object, sender_name: str = ""):
        """私聊异步做梦代理"""
        return await self.fatigue_manager._private_sleep_and_dream_process(session_key, user_id, agent, sender_name)

    async def _sleep_and_dream_process(self, group_id: str, agent: object):
        """群聊异步做梦代理"""
        return await self.fatigue_manager._sleep_and_dream_process(group_id, agent)

    def _is_truly_calling_me(self, text: str) -> bool:
        """启发式分析群聊文本中提到“小萤/小荧”时，是否属于真正的直接呼唤/指令，防止抢答自作多情"""
        text = text.strip()
        if not text:
            return False
        # 1. 直接以名字开头，后接逗号、感叹号、问号、空格或直接带命令动作
        if re.match(r"^(小萤|小荧)([，,！!？?\s]|帮|写|查|做|算|听|读|说|问|看|下|[^\w]|$)", text):
            return True
        # 2. 以名字结尾，前接呼应符号（如 "对吧，小萤" / "帮我看看，小荧"）
        if re.search(r"([，,！!？?\s])(小萤|小荧)$", text):
            return True
        # 3. 消息中包含明确的问句提问或直接呼唤，且名字紧邻状态疑问词
        if re.search(r"(小萤|小荧)(在吗|呢|好|早|晚安|出来|在不)", text):
            return True
        return False

    async def dispatch_event(self, event: dict):
        """解析 QQ 事件，运行中间件管道，最后触发 ReAct 异步处理"""
        msg_type = event.get("message_type", "private")
        raw = html.unescape(event.get("raw_message", "").strip())
        user_id = str(event.get("user_id", ""))
        self_id = str(event.get("self_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        sender_info = event.get("sender", {})
        card = str(sender_info.get("card", "")).strip()
        nickname = str(sender_info.get("nickname", "")).strip()
        sender_name = card or nickname or str(user_id)
        
        session_key = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
        
        context = {
            "msg_type": msg_type,
            "raw": raw,
            "user_id": user_id,
            "self_id": self_id,
            "group_id": group_id,
            "sender_name": sender_name,
            "session_key": session_key,
            "is_other_bot": False,
            "is_allowed": False,
            "is_triggered": True,
            "raw_cleaned": "",
            "this_msg_time": 0.0
        }
        
        for middleware in self.middlewares:
            try:
                should_stop = await middleware.process(self, event, context)
                if should_stop:
                    return
            except Exception as e:
                logger.error(f"Error in middleware {middleware.__class__.__name__}: {e}", exc_info=True)

    async def _execute_agent_run(self, agent, raw: str, session_key: str, msg_type: str, 
                                 user_id: str, group_id: str, sender_name: str, task_start_time: float):
        """兼容代理：委托给物理 executor 运行推理循环"""
        return await self.executor.execute_agent_run(
            agent, raw, session_key, msg_type, user_id, group_id, sender_name, task_start_time
        )

    def _count_tokens(self, text: str) -> int:
        """中英文混合 Token 科学算法兼容代理"""
        return self.executor._count_tokens(text)

    def _load_persona(self) -> tuple:
        """人设加载兼容代理"""
        return self.executor._load_persona()

    def _log_activity_dispatcher(self, category: str, content: str, user_id: str = None):
        """活动日志记录兼容代理"""
        return self.executor._log_activity_dispatcher(category, content, user_id)
