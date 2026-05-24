import os
import time
import logging

logger = logging.getLogger("net_gateway.security")

class SecurityManager:
    """网关安全与访问拦截管理器，负责私聊暂停/恢复状态维护、白名单鉴权过滤及阻断响应冷却机制。"""
    
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.context = dispatcher.context
        self.admin_id = dispatcher.admin_id
        
        # 移出的安全过滤状态
        self._private_chat_paused = False
        self._non_white_cache = {}  # user_id -> last_reply_time

    def is_allowed(self, user_id: str, msg_type: str, group_id: str) -> bool:
        """安全白名单判定"""
        if str(user_id).startswith("douyin_"):
            return True

        WHITE_LIST = {self.admin_id}
        coworker_ids = os.getenv("QQ_COWORKER_IDS", "")
        if coworker_ids:
            WHITE_LIST.update(x.strip() for x in coworker_ids.split(",") if x.strip())
        extra_white = os.getenv("MY_AGENT_WHITE_LIST", "")
        if extra_white:
            WHITE_LIST.update(x.strip() for x in extra_white.split(",") if x.strip())

        # 加载 QQ 群白名单
        white_groups_env = os.getenv("QQ_WHITE_GROUPS", "693134080")
        WHITE_GROUPS = {x.strip() for x in white_groups_env.split(",") if x.strip()}

        if user_id in WHITE_LIST:
            return True
        if msg_type == "group" and group_id in WHITE_GROUPS:
            return True
        return False

    async def handle_security_interception(self, event: dict, is_allowed: bool, is_triggered: bool) -> bool:
        """处理非白名单用户的物理阻断逻辑，返回是否应当阻断退出"""
        if is_allowed:
            return False

        msg_type = event.get("message_type", "private")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        raw = event.get("raw_message", "").strip()

        if msg_type == "private" or (msg_type == "group" and is_triggered):
            now = time.monotonic()
            last_reply = self._non_white_cache.get(user_id, 0.0)
            
            if now - last_reply >= 300.0:  # 5分钟冷却
                self._non_white_cache[user_id] = now
                reject_msg = "抱歉，我是亮哥的专属 AI 助手小萤，目前仅对主人开放私聊与管理服务哦。"
                if msg_type == "group":
                    await self.context.send_msg("group", "", group_id, f"[CQ:at,qq={user_id}] {reject_msg}")
                else:
                    await self.context.send_msg(msg_type, user_id, "", reject_msg)
                logger.warning(f"🛡️ [安全拦截] 拦截非白名单 QQ 用户 [{user_id}] 消息: {raw[:50]}")
        return True

    async def handle_admin_commands(self, msg_type: str, user_id: str, raw: str) -> bool:
        """拦截并处理物理开关管理指令，返回是否已命中且应拦截退出"""
        if user_id != self.admin_id or msg_type != "private":
            return False

        if raw == "暂停私聊":
            self._private_chat_paused = True
            await self.context.send_msg("private", self.admin_id, "", "[系统提示] 已物理暂停非主人私聊，小萤将保持静默。")
            return True
        elif raw == "恢复私聊":
            self._private_chat_paused = False
            await self.context.send_msg("private", self.admin_id, "", "[系统提示] 已恢复私聊服务，非主人私聊将重新恢复交互与疲劳累加。")
            return True
        return False

    def is_private_chat_paused(self, msg_type: str, user_id: str) -> bool:
        """判断私聊是否被暂停"""
        if msg_type == "private" and user_id != self.admin_id:
            return self._private_chat_paused
        return False
