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
        """解析 QQ 事件，进行白名单拦截、私聊控制，最后触发 ReAct 异步处理"""
        msg_type = event.get("message_type", "private")
        raw = html.unescape(event.get("raw_message", "").strip())
        user_id = str(event.get("user_id", ""))
        self_id = str(event.get("self_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        sender_info = event.get("sender", {})
        card = str(sender_info.get("card", "")).strip()
        nickname = str(sender_info.get("nickname", "")).strip()
        sender_name = card or nickname or str(user_id)
        
        # 1. 物理静默过滤自己发出的回执消息
        if self_id and user_id == self_id:
            return

        # 写入物理用户指令审计日志，以实现最前端全量流量隔离审计
        _pn, _ua = self._load_persona()
        _skey = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
        self._log_activity_dispatcher("用户输入", f"{_ua} ({_skey}): {raw}", user_id=user_id)

        other_bot_ids = {x.strip() for x in os.getenv("QQ_OTHER_BOT_IDS", "1911828529").split(",") if x.strip()}
        is_other_bot = user_id in other_bot_ids

        # ── 2. 安全白名单前置拦截判定 ──
        is_allowed = self.security_manager.is_allowed(user_id, msg_type, group_id)

        # ── 3. 群聊消息「智能静默旁听 + 名字/At 唤醒」重构 ──
        session_key = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
        is_triggered = True  # 默认为 True (私聊始终唤醒)

        if msg_type == "group" and group_id:
            is_at_bot = f"[CQ:at,qq={self_id}]" in raw
            if is_other_bot:
                is_at_bot = False
            
            raw_cleaned = re.sub(r'\[CQ:at,qq=\d+\]', '', raw).strip()
            if not raw_cleaned:
                return

            # 真假呼唤判定：At 唤醒，或者在非机器人消息中匹配到名字指令呼唤
            is_triggered = is_at_bot or (self._is_truly_calling_me(raw_cleaned) and not is_other_bot)

            # A. 若在白名单群，且未触发唤醒：静默旁听并追加历史缓存，直接退出
            if is_allowed and not is_triggered:
                # 获取或惰性初始化 Agent 实例
                agent = self.context._agents.get(session_key)
                if agent is None:
                    agent = self.context._factory(session_key)
                    self.context._agents[session_key] = agent
                
                # 默默追加群聊闲聊到缓存（0大模型开销）
                user_msg = {"role": "user", "content": f"[{sender_name}]: {raw_cleaned}"}
                agent.messages.append(user_msg)
                
                # 防爆裁剪保护
                if len(agent.messages) > 100:
                    agent.messages = [agent.messages[0]] + agent.messages[-50:]
                
                if agent.session:
                    await agent.session.replace_all(agent.messages)
                return  # 完美降载退出

            # B. 若被触发唤醒：补齐群聊发言人元数据身份，确保大模型 100% 分清亮哥与普通群成员
            if is_triggered:
                if user_id != self.admin_id:
                    raw = f"[来自 QQ: {user_id} 的群发言] {raw_cleaned}"
                else:
                    raw = f"[来自亮哥的群发言] {raw_cleaned}"

            # C. 疲劳累积清零：群聊发言的被动扣分疲劳值直接设为 0，防止被动累趴
            inc = 0.0
            await self.fatigue_manager.adjust_fatigue(group_id, inc, event)

        # ── 4. 安全拦截 ──
        if await self.security_manager.handle_security_interception(event, is_allowed, is_triggered):
            return

        # ── 5. 主人专属控制与特权唤醒 ──
        if user_id == self.admin_id:
            # 1. 强行唤醒打盹 (管理员特权：一开口即 100% 全局复活小萤，取消所有的打盹异步任务)
            if any(self._sleep_modes.values()):
                for key, task in list(self._active_sleep_tasks.items()):
                    if task and not task.done():
                        task.cancel()
                        logger.info(f"Admin message received. Cancelled background sleep task for {key}.")
                self._sleep_modes.clear()
                self._active_sleep_tasks.clear()
                self._fatigue_levels.clear()
                logger.info("Admin private message received. Waking up all sessions globally from sleep/nap.")
            
            # 2. 物理开关指令拦截
            if await self.security_manager.handle_admin_commands(msg_type, user_id, raw):
                return

        # ── 6. 非主人私聊冷冻期与全局暂停拦截 ──
        if self.security_manager.is_private_chat_paused(msg_type, user_id) or self._sleep_modes.get(session_key, False):
            logger.info(f"Private chat paused/sleeping. Silently ignoring message from {user_id}: {raw[:50]}")
            return


        # ── 6. 主人专属敏感指令物理卡片授权审批答复拦截 ──
        if session_key in self._pending_perms:
            evt_perm = self._pending_perms[session_key]
            is_approved = raw.lower() in ("y", "yes", "允许", "ok", "通过")
            evt_perm.set(is_approved)
            return

        # ── 7. 播客选题拦截 ──────────────────────────────────────────
        if self._waiting_podcast_topic.get(session_key):
            self._waiting_podcast_topic[session_key] = False
            choices = self._podcast_choices.get(session_key, [])
            
            selected_topic = raw.strip()
            if selected_topic in ("1", "2", "3") and len(choices) >= 3:
                selected_topic = choices[int(selected_topic) - 1]
                if ". " in selected_topic:
                    selected_topic = selected_topic.split(". ", 1)[1]
                elif "、" in selected_topic:
                    selected_topic = selected_topic.split("、", 1)[1]
                    
            await self.context.send_msg("private", self.admin_id, "", f"🎯 已锁定明早播客选题：【{selected_topic}】。\n正在为您融合本地笔记与网络参考资料，合成为约 2000 字的极客研究笔记并投喂云端，请稍等...")
            
            # 桥接 bot 实例触发异步生成
            bot = self.bot
            if bot and hasattr(bot, "_process_podcast_generation_async"):
                asyncio.create_task(bot._process_podcast_generation_async(session_key, selected_topic, self.admin_id))
            return

        # ── 7. 语音指令与 CSMA / CD 检测 ──
        test_style, test_text = parse_voice_test_command(raw)
        if test_style is not None:
            if test_text:
                await send_voice(self.context, msg_type, user_id, group_id, test_text, test_style, is_test=True)
            else:
                await self.context.send_msg(msg_type, user_id, group_id, 
                    "⚠️ 请输入要合成的文本，格式如：小萤语音测试：[委屈] 小萤好难过呀", skip_delay=True)
            return

        # 兼容测试套件：记录排队消息队列，维持单元测试高度向下兼容
        if self.bot and hasattr(self.bot, "_message_queues"):
            active_task = getattr(self.bot, "_current_tasks", {}).get(session_key)
            is_busy = False
            if active_task is not None:
                if isinstance(active_task, bool):
                    is_busy = active_task
                else:
                    is_busy = not active_task.done()

            if is_busy:
                is_preempt = any(kw in raw for kw in ["停", "别跑了", "取消", "刹车", "先别", "停下"])
                if is_preempt:
                    if not isinstance(active_task, bool):
                        active_task.cancel()
                    self._log_activity_dispatcher("系统调度", f"紧急强占中断当前任务: {session_key}")
                    interruption_note = (
                        f"[系统提示：{_ua}在刚才的任务中途发送了这条新命令。"
                        f"先简短确认停下上一个任务，然后切入新指令：\"{raw}\"]"
                    )
                    raw = interruption_note
                    getattr(self.bot, "_current_tasks", {}).pop(session_key, None)
                else:
                    if session_key not in self.bot._message_queues:
                        self.bot._message_queues[session_key] = []
                    self.bot._message_queues[session_key].append((event, raw))
                    return

        # 获取或惰性初始化 Agent 实例
        agent = self.context._agents.get(session_key)
        if agent is None:
            agent = self.context._factory(session_key)
            self.context._agents[session_key] = agent

        # 注册单调发言时间戳以供 CSMA/CD 检测
        this_msg_time = self.bus.register_message(session_key)

        # 封装超时熔断强力驱动，防止协程卡死导致通道永久静默
        async def run_with_timeout():
            try:
                await asyncio.wait_for(
                    self._execute_agent_run(agent, raw, session_key, msg_type, user_id, group_id, sender_name, this_msg_time),
                    timeout=90.0
                )
            except asyncio.TimeoutError:
                logger.error(f"⏳ [超时熔断] 会话 {session_key} 任务运行超过 90 秒，强行物理熔断！")
                await self.context.send_msg(msg_type, user_id, group_id, "⚠️ [系统提示] 小萤的大脑刚被卡住啦，本次任务已超时中断，亮哥可以重新对我说点别的哦～", skip_delay=True)
            except Exception as e:
                logger.error(f"Error in runner wrapper for {session_key}: {e}", exc_info=True)

        # 100% 同步瞬间抢占占位，封死微秒级竞态窗口
        if self.bot and hasattr(self.bot, "_current_tasks"):
            self.bot._current_tasks[session_key] = True

        task = asyncio.create_task(run_with_timeout())
        task.raw_prompt = raw
        if self.bot and hasattr(self.bot, "_current_tasks"):
            self.bot._current_tasks[session_key] = task

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
