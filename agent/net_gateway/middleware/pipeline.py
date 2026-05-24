import os
import re
import asyncio
import logging
from .base import EventMiddleware
from ..tts import parse_voice_test_command, send_voice

logger = logging.getLogger("net_gateway.middleware.pipeline")

class SelfReceiptFilterMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        self_id = context.get("self_id")
        user_id = context.get("user_id")
        if self_id and user_id == self_id:
            return True
        return False

class AuditLoggingMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        group_id = context.get("group_id")
        msg_type = context.get("msg_type")
        user_id = context.get("user_id")
        raw = context.get("raw")
        
        _pn, _ua = dispatcher._load_persona()
        _skey = f"group_{group_id}" if msg_type == "group" else f"user_{user_id}"
        dispatcher._log_activity_dispatcher("用户输入", f"{_ua} ({_skey}): {raw}", user_id=user_id)
        return False

class SecurityWhiteListMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        user_id = context.get("user_id")
        msg_type = context.get("msg_type")
        group_id = context.get("group_id")
        
        other_bot_ids = {x.strip() for x in os.getenv("QQ_OTHER_BOT_IDS", "1911828529").split(",") if x.strip()}
        is_other_bot = user_id in other_bot_ids
        context["is_other_bot"] = is_other_bot
        
        is_allowed = dispatcher.security_manager.is_allowed(user_id, msg_type, group_id)
        context["is_allowed"] = is_allowed
        return False

class GroupMessageFilterMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        msg_type = context.get("msg_type")
        group_id = context.get("group_id")
        user_id = context.get("user_id")
        self_id = context.get("self_id")
        raw = context.get("raw")
        sender_name = context.get("sender_name")
        is_other_bot = context.get("is_other_bot")
        is_allowed = context.get("is_allowed")
        session_key = context.get("session_key")
        
        is_triggered = True
        
        if msg_type == "group" and group_id:
            is_at_bot = f"[CQ:at,qq={self_id}]" in raw
            if is_other_bot:
                is_at_bot = False
            
            raw_cleaned = re.sub(r'\[CQ:at,qq=\d+\]', '', raw).strip()
            if not raw_cleaned:
                return True
            
            context["raw_cleaned"] = raw_cleaned
            
            is_triggered = is_at_bot or (dispatcher._is_truly_calling_me(raw_cleaned) and not is_other_bot)
            context["is_triggered"] = is_triggered
            
            if is_allowed and not is_triggered:
                agent = dispatcher.context._agents.get(session_key)
                if agent is None:
                    agent = dispatcher.context._factory(session_key)
                    dispatcher.context._agents[session_key] = agent
                
                user_msg = {"role": "user", "content": f"[{sender_name}]: {raw_cleaned}"}
                agent.messages.append(user_msg)
                
                if len(agent.messages) > 100:
                    agent.messages = [agent.messages[0]] + agent.messages[-50:]
                
                if agent.session:
                    await agent.session.replace_all(agent.messages)
                return True
            
            if is_triggered:
                if user_id != dispatcher.admin_id:
                    raw = f"[来自 QQ: {user_id} 的群发言] {raw_cleaned}"
                else:
                    raw = f"[来自亮哥的群发言] {raw_cleaned}"
                context["raw"] = raw
                
            inc = 0.0
            await dispatcher.fatigue_manager.adjust_fatigue(group_id, inc, event)
            
        return False

class SecurityInterceptionMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        is_allowed = context.get("is_allowed")
        is_triggered = context.get("is_triggered")
        if await dispatcher.security_manager.handle_security_interception(event, is_allowed, is_triggered):
            return True
        return False

class AdminCommandMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        user_id = context.get("user_id")
        msg_type = context.get("msg_type")
        raw = context.get("raw")
        session_key = context.get("session_key")
        
        if user_id == dispatcher.admin_id:
            if any(dispatcher._sleep_modes.values()):
                for key, task in list(dispatcher._active_sleep_tasks.items()):
                    if task and not task.done():
                        task.cancel()
                        logger.info(f"Admin message received. Cancelled background sleep task for {key}.")
                dispatcher._sleep_modes.clear()
                dispatcher._active_sleep_tasks.clear()
                dispatcher._fatigue_levels.clear()
                logger.info("Admin private message received. Waking up all sessions globally from sleep/nap.")
            
            if await dispatcher.security_manager.handle_admin_commands(msg_type, user_id, raw):
                return True
        return False

class SleepFreezeMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        msg_type = context.get("msg_type")
        user_id = context.get("user_id")
        session_key = context.get("session_key")
        raw = context.get("raw")
        
        if dispatcher.security_manager.is_private_chat_paused(msg_type, user_id) or dispatcher._sleep_modes.get(session_key, False):
            logger.info(f"Private chat paused/sleeping. Silently ignoring message from {user_id}: {raw[:50]}")
            return True
        return False

class PendingPermissionMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        session_key = context.get("session_key")
        raw = context.get("raw")
        
        if session_key in dispatcher._pending_perms:
            evt_perm = dispatcher._pending_perms[session_key]
            is_approved = raw.lower() in ("y", "yes", "允许", "ok", "通过")
            evt_perm.set(is_approved)
            return True
        return False

class PodcastTopicMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        session_key = context.get("session_key")
        raw = context.get("raw")
        
        if dispatcher._waiting_podcast_topic.get(session_key):
            dispatcher._waiting_podcast_topic[session_key] = False
            choices = dispatcher._podcast_choices.get(session_key, [])
            
            selected_topic = raw.strip()
            if selected_topic in ("1", "2", "3") and len(choices) >= 3:
                selected_topic = choices[int(selected_topic) - 1]
                if ". " in selected_topic:
                    selected_topic = selected_topic.split(". ", 1)[1]
                elif "、" in selected_topic:
                    selected_topic = selected_topic.split("、", 1)[1]
                    
            await dispatcher.context.send_msg("private", dispatcher.admin_id, "", f"🎯 已锁定明早播客选题：【{selected_topic}】。\n正在为您融合本地笔记与网络参考资料，合成为约 2000 字的极客研究笔记并投喂云端，请稍等...")
            
            bot = dispatcher.bot
            if bot:
                try:
                    asyncio.create_task(bot._process_podcast_generation_async(session_key, selected_topic, dispatcher.admin_id))
                except AttributeError:
                    pass
            return True
        return False

class VoiceCommandMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        raw = context.get("raw")
        msg_type = context.get("msg_type")
        user_id = context.get("user_id")
        group_id = context.get("group_id")
        
        test_style, test_text = parse_voice_test_command(raw)
        if test_style is not None:
            if test_text:
                await send_voice(dispatcher.context, msg_type, user_id, group_id, test_text, test_style, is_test=True)
            else:
                await dispatcher.context.send_msg(msg_type, user_id, group_id, 
                    "⚠️ 请输入要合成的文本，格式如：小萤语音测试：[委屈] 小萤好难过呀", skip_delay=True)
            return True
        return False

class TaskDispatcherMiddleware(EventMiddleware):
    async def process(self, dispatcher, event: dict, context: dict) -> bool:
        raw = context.get("raw")
        session_key = context.get("session_key")
        msg_type = context.get("msg_type")
        user_id = context.get("user_id")
        group_id = context.get("group_id")
        sender_name = context.get("sender_name")
        
        _pn, _ua = dispatcher._load_persona()
        
        if dispatcher.bot:
            active_task = dispatcher.bot.get_active_task(session_key)
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
                    dispatcher._log_activity_dispatcher("系统调度", f"紧急强占中断当前任务: {session_key}")
                    interruption_note = (
                        f"[系统提示：{_ua}在刚才的任务中途发送了这条新命令。"
                        f"先简短确认停下上一个任务，然后切入新指令：\"{raw}\"]"
                    )
                    raw = interruption_note
                    context["raw"] = raw
                    dispatcher.bot.remove_active_task(session_key, active_task)
                else:
                    dispatcher.bus.register_message(session_key)
                    dispatcher.bot.enqueue_message(session_key, event, raw)
                    return True

        agent = dispatcher.context._agents.get(session_key)
        if agent is None:
            agent = dispatcher.context._factory(session_key)
            dispatcher.context._agents[session_key] = agent

        this_msg_time = dispatcher.bus.register_message(session_key)
        context["this_msg_time"] = this_msg_time

        async def run_with_timeout():
            try:
                await asyncio.wait_for(
                    dispatcher._execute_agent_run(agent, raw, session_key, msg_type, user_id, group_id, sender_name, this_msg_time),
                    timeout=90.0
                )
            except asyncio.TimeoutError:
                logger.error(f"⏳ [超时熔断] 会话 {session_key} 任务运行超过 90 秒，强行物理熔断！")
                await dispatcher.context.send_msg(msg_type, user_id, group_id, "⚠️ [系统提示] 小萤的大脑刚被卡住啦，本次任务已超时中断，亮哥可以重新对我说点别的哦～", skip_delay=True)
            except Exception as e:
                logger.error(f"Error in runner wrapper for {session_key}: {e}", exc_info=True)

        if dispatcher.bot:
            dispatcher.bot.set_active_task(session_key, True)

        task = asyncio.create_task(run_with_timeout())
        task.raw_prompt = raw
        if dispatcher.bot:
            dispatcher.bot.set_active_task(session_key, task)
            
        return True
