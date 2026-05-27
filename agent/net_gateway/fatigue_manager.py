import asyncio
import logging
import os
import random
import time

logger = logging.getLogger("net_gateway.fatigue")

class FatigueManager:
    """疲劳打盹与梦境反思管理器，负责用脑疲劳度状态维护、高情商打盹宣告模型交互及梦境净化自愈。"""
    
    def __init__(self, dispatcher):
        self.dispatcher = dispatcher
        self.context = dispatcher.context
        self.bot = dispatcher.bot
        self.admin_id = dispatcher.admin_id
        
        # 移出的核心疲劳打盹状态
        self._fatigue_levels = {}        # group_id/session_key -> float
        self._sleep_modes = {}           # group_id/session_key -> bool
        self._active_sleep_tasks = {}    # group_id/session_key -> asyncio.Task
        self._last_message_times = {}    # group_id/session_key -> float
        self.fatigue_sleep_seconds = float(os.getenv("QQ_FATIGUE_SLEEP_MINUTES", "15.0")) * 60.0

    async def adjust_fatigue(self, group_id: str, inc: float, event: dict = None, is_private: bool = False, sender_name: str = ""):
        """疲劳计算逻辑与高情商打盹宣告"""
        now = time.monotonic()
        last_time = self._last_message_times.get(group_id, now)
        self._last_message_times[group_id] = now
        time_passed = now - last_time
        
        decay = time_passed * (2.0 / 60.0)
        current = max(0.0, self._fatigue_levels.get(group_id, 0.0) - decay)
        
        new_fatigue = min(100.0, max(0.0, current + inc))
        self._fatigue_levels[group_id] = new_fatigue
        
        old_sleep = self._sleep_modes.get(group_id, False)
        
        if new_fatigue >= 100.0 and not old_sleep:
            self._sleep_modes[group_id] = True
            if is_private:
                user_id = group_id.replace("user_", "")
                announcement = await self._generate_private_fatigue_announcement(user_id, sender_name)
                await self.context.send_msg("private", user_id, "", announcement, skip_delay=True)
                self.dispatcher._log_activity_dispatcher("物理打盹", f"小萤在私聊 {user_id} 中宣告打盹: {announcement}")
                
                agent = self.context._agents.get(group_id)
                if agent is not None:
                    task = asyncio.create_task(self._private_sleep_and_dream_process(group_id, user_id, agent, sender_name))
                    self._active_sleep_tasks[group_id] = task
            else:
                announcement = await self._generate_fatigue_announcement(group_id)
                await self.context.send_msg("group", "", group_id, announcement, skip_delay=True)
                self.dispatcher._log_activity_dispatcher("物理打盹", f"小萤在群 {group_id} 中宣告打盹: {announcement}")
                
                session_key = f"group_{group_id}_{self.admin_id}"
                agent = self.context._agents.get(session_key)
                if agent is not None:
                    task = asyncio.create_task(self._sleep_and_dream_process(group_id, agent))
                    self._active_sleep_tasks[group_id] = task
            
        elif new_fatigue <= 20.0 and old_sleep:
            self._sleep_modes[group_id] = False

    async def _generate_private_fatigue_announcement(self, user_id: str, sender_name: str = "") -> str:
        """模型或兜底生成私聊用脑过度打盹宣告"""
        if not sender_name:
            sender_name = user_id
        fallbacks = [
            f"唔……（揉了揉太阳穴）小萤和{sender_name}聊得太久啦，脑细胞好像一下子烧光了呢。\n\n我的大脑网络正在发烫物理降温，我去充个电打个盹，晚点再找你聊哦～",
            f"哎呀……（晕乎乎）感觉算力严重超载啦，小萤的思绪都有点打结了。\n\n本美少女极客合伙人要先去打个盹、系统维护十五分钟啦。\n\n等我充满电复活，一定第一时间秒回你哈！"
        ]
        session_key = f"user_{user_id}"
        agent = self.context._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
            
        try:
            _pn, _ua = self.dispatcher._load_persona()
            prompt = (
                f"你现在是主人【亮哥】的专属 AI 助手【{_pn}】。你刚才在与用户【{sender_name}】（非亮哥）的私聊中由于聊得太频繁，大脑疲劳度达到 100% 极限，需要进行物理打盹降温休眠。\n"
                f"注意：当前对话的对方是【{sender_name}】（他/她并不是你的主人亮哥，只是一个普通同事/Bot/群友）。"
                f"【绝对禁止称呼对方为'亮哥'、'主人'或'老板'】！\n"
                f"请你自发、高情商地向【{sender_name}】发表一句话作为打盹宣告，告诉他/她你脑细胞烧光了，要稍微休息降温，等疲劳度消退再回复他/她。\n\n"
                f"规范：符合单气泡三段呼吸律，字数 40 到 100 字，语气幽默、活泼可爱且保持客观界限，带有动作描写（如：（揉了揉太阳穴）、（打了个哈欠）），绝不能出现任何与亮哥称呼相关的穿帮词。"
            )
            response = await asyncio.wait_for(
                agent.llm.chat(messages=[{"role": "user", "content": prompt}], model_override="deepseek/deepseek-v4-flash"),
                timeout=8.0
            )
            res = response.get("content", "").strip().replace("```", "")
            return res if len(res) > 10 else random.choice(fallbacks)
        except Exception:
            return random.choice(fallbacks)

    async def _private_sleep_and_dream_process(self, session_key: str, user_id: str, agent: object, sender_name: str = ""):
        """私聊异步做梦净化"""
        from agent.core.bootstrap import session_ctx
        session_ctx.set("System:Dream")
        try:
            # 1. 睡眠期间不主动清空 messages，仅安全等待（以防中途重启）
            await asyncio.sleep(self.fatigue_sleep_seconds)
            
            # 2. 睡眠结束，截取当前历史快照进行高并发增量做梦提炼
            snapshot = list(agent.messages)
            snapshot_len = len(snapshot)
            
            summary_card = ""
            try:
                from agent.evolution import trigger_deep_dream_evolution
                # 传入快照，只对快照内的老历史进行脑力蒸馏提炼
                summary_card = await trigger_deep_dream_evolution(agent, history_messages=snapshot)
            except Exception as e:
                logger.error(f"Failed to run trigger_deep_dream_evolution in private sleep: {e}")
            finally:
                # 3. 黄金快照增量清账与持久化同步
                if len(agent.messages) >= snapshot_len:
                    agent.messages = agent.messages[snapshot_len:]
                else:
                    agent.messages = []
                
                # 同步同步回 SQLite，清除已反思过的老消息缓存，只保留睡眠期间新流入的
                if hasattr(agent.memory, "save_active_session_async"):
                    agent.memory.save_active_session_async(session_key, agent.messages)
                    
            self._fatigue_levels[session_key] = 0.0
            self._sleep_modes[session_key] = False
            self._active_sleep_tasks.pop(session_key, None)
            
            # 4. 合成高情商唤醒宣告
            wake_prefix = await self._generate_private_wake_announcement(user_id, sender_name)
            
            # 如果有新提炼出 KI/Skill 的总结卡片，则将其合并展示，仪式感拉满
            if summary_card:
                wake_msg = f"{wake_prefix}\n\n{summary_card}"
            else:
                wake_msg = wake_prefix
                
            await self.context.send_msg("private", user_id, "", wake_msg, skip_delay=True)
        except Exception as e:
            logger.error(f"Error in private_sleep_and_dream_process: {e}")
            self._fatigue_levels[session_key] = 0.0
            self._sleep_modes[session_key] = False
            self._active_sleep_tasks.pop(session_key, None)

    async def _generate_private_wake_announcement(self, user_id: str, sender_name: str = "") -> str:
        """模型或兜底生成私聊醒来宣告"""
        if not sender_name:
            sender_name = user_id
        fallbacks = [
            f"哼哼～（伸了个大大的懒腰）小萤充满电上线啦！\n\n大脑冷却完毕，{sender_name}可以继续找小萤聊天啦！",
            f"呼啊……（揉揉眼睛）深度睡眠充完电，我的算力核心已经 100% 恢复满状态啦！"
        ]
        session_key = f"user_{user_id}"
        agent = self.context._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
        try:
            _pn, _ua = self.dispatcher._load_persona()
            prompt = (
                f"你现在是主人【亮哥】的专属 AI 助手【{_pn}】。你刚才由于用脑过度物理打盹了，现在充满电成功满血复苏。\n"
                f"注意：当前对话的对方是【{sender_name}】（他/她并不是你的主人亮哥）。【绝对禁止称呼对方为'亮哥'、'主人'或'老板'】！\n"
                f"请你自发、高情商地向【{sender_name}】发表一句话作为复苏宣告，宣告你的算力已经100%恢复满状态。\n\n"
                f"规范：符合单气泡三段呼吸律，字数 40 到 100 字，语气幽默活泼且克制客观，带有动作描写，绝不能出现任何与亮哥称呼相关的穿帮词。"
            )
            response = await asyncio.wait_for(
                agent.llm.chat(messages=[{"role": "user", "content": prompt}], model_override="deepseek/deepseek-v4-flash"),
                timeout=8.0
            )
            res = response.get("content", "").strip().replace("```", "")
            return res if len(res) > 10 else random.choice(fallbacks)
        except Exception:
            return random.choice(fallbacks)

    async def _generate_fatigue_announcement(self, group_id: str) -> str:
        """群聊打盹吐槽宣告"""
        fallbacks = [
            "唔……（揉了揉太阳穴）脑细胞好像一下子被大家烧光啦，小萤的大脑网络正在发烫物理降温。\n\n我去充个电打个盹，晚点再陪大家聊～"
        ]
        session_key = f"group_{group_id}_{self.admin_id}"
        agent = self.context._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
        try:
            _pn, _ua = self.dispatcher._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】。你刚才在群聊中频繁调用大模型，大脑疲劳度达到 100% 极限，需要进行物理打盹降温休眠。\n"
                f"请你自发、高情商地向群里发表一句幽默、可爱的打盹宣告，告诉大家你脑细胞烧光了需要稍微休眠降温。\n\n"
                f"规范：符合单气泡三段呼吸律，字数 40 到 100 字，语气活灵活现，带有动作描写。"
            )
            response = await asyncio.wait_for(
                agent.llm.chat(messages=[{"role": "user", "content": prompt}], model_override="deepseek/deepseek-v4-flash"),
                timeout=8.0
            )
            res = response.get("content", "").strip().replace("```", "")
            return res if len(res) > 10 else random.choice(fallbacks)
        except Exception:
            return random.choice(fallbacks)

    async def _sleep_and_dream_process(self, group_id: str, agent: object):
        """群聊做梦净化闭环"""
        from agent.core.bootstrap import session_ctx
        session_ctx.set("System:Dream")
        session_key = f"group_{group_id}_{self.admin_id}"
        try:
            # 1. 睡眠期间不主动清空 messages，仅安全等待
            await asyncio.sleep(self.fatigue_sleep_seconds)
            
            # 2. 睡眠结束，截取当前历史快照进行高并发增量做梦提炼
            snapshot = list(agent.messages)
            snapshot_len = len(snapshot)
            
            summary_card = ""
            try:
                from agent.evolution import trigger_deep_dream_evolution
                summary_card = await trigger_deep_dream_evolution(agent, history_messages=snapshot)
            except Exception as e:
                logger.error(f"Failed to run trigger_deep_dream_evolution in group sleep: {e}")
            finally:
                # 3. 黄金快照增量清账与持久化同步
                if len(agent.messages) >= snapshot_len:
                    agent.messages = agent.messages[snapshot_len:]
                else:
                    agent.messages = []
                
                # 同步回 SQLite，清除已反思过的老消息缓存，只保留睡眠期间新流入的
                if hasattr(agent.memory, "save_active_session_async"):
                    agent.memory.save_active_session_async(session_key, agent.messages)
                    
            self._fatigue_levels[group_id] = 0.0
            self._sleep_modes[group_id] = False
            
            # 4. 合成高情商唤醒宣告
            wake_prefix = await self._generate_wake_announcement(group_id)
            if summary_card:
                wake_msg = f"{wake_prefix}\n\n{summary_card}"
            else:
                wake_msg = wake_prefix
                
            await self.context.send_msg("group", "", group_id, wake_msg, skip_delay=True)
        except Exception as e:
            logger.error(f"Error in group_sleep_and_dream_process: {e}")
            self._fatigue_levels[group_id] = 0.0
            self._sleep_modes[group_id] = False

    async def _generate_wake_announcement(self, group_id: str) -> str:
        """群聊做梦复苏宣告"""
        fallbacks = [
            "哼哼～（伸了个大大的懒腰）小萤满血复活啦！\n\n大脑冷却完毕，随时准备接收大家的呼唤呀！"
        ]
        session_key = f"group_{group_id}_{self.admin_id}"
        agent = self.context._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
        try:
            _pn, _ua = self.dispatcher._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】。你刚才由于用脑过度物理打盹了，现在脑海群聊记忆完成净化并成功复苏。\n"
                f"请你发表一句幽默、可爱的苏醒复苏宣告。\n\n"
                f"规范：符合单气泡三段呼吸律，字数 40 到 100 字，语气活灵活现，带有动作描写。"
            )
            response = await asyncio.wait_for(
                agent.llm.chat(messages=[{"role": "user", "content": prompt}], model_override="deepseek/deepseek-v4-flash"),
                timeout=8.0
            )
            res = response.get("content", "").strip().replace("```", "")
            return res if len(res) > 10 else random.choice(fallbacks)
        except Exception:
            return random.choice(fallbacks)
