"""QQ Gateway — NapCat WebSocket + HTTP API → Agent.

NapCat 是 QQ 机器人框架，暴露 OneBot v11 协议：
  - WebSocket server (默认 :3001) → 推送消息事件
  - HTTP API server (默认 :3000) → 发送消息

用法:
    python main.py --gateway
    NAPCAT_WS_URL=ws://localhost:3001 NAPCAT_HTTP_URL=http://localhost:3000 python main.py --gateway
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

NC_WS_URL = os.getenv("NAPCAT_WS_URL", "ws://127.0.0.1:3001")
NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")
MAX_REPLY_CHARS = 2000

# 硬编码每日维护 — LLM 只用 merge_to_core，文件操作全由 Python 处理
MAINTENANCE_MERGE_PROMPT = """把以下 reflect 内容合并到核心记忆：

{reflect_list}

对每条 reflect:
1. 判断归属的核心文件（user_profile / communication_rules / operation_rules / xl_tool_guide / xl_architecture / xl_code_review / xl_identity / xl_debugging / xl_requirement_analysis）
2. 调用 merge_to_core(target_file="{文件名}", description="{简短描述}", content="{萃取后的内容}")
3. merge_to_core 自动批准，直接跑

只萃取每条 reflect 中唯一的、有价值的信息写入对应核心文件，重复的跳过。
完成后输出 "merge_done: N"。"""


class _PermEvent(asyncio.Event):
    """携带结果的异步事件 — 解决跨协程权限沟通的竞态条件。"""
    def __init__(self):
        super().__init__()
        self.result: bool = False


class QQGateway:
    """最小可行 QQ Gateway。WebSocket 收消息，HTTP API 发回复。"""

    def __init__(self, agent_factory):
        self._factory = agent_factory          # () → Agent
        self._agents: dict[str, object] = {}   # user_id/group_id → Agent
        self._http: Optional[aiohttp.ClientSession] = None
        self._pending_perms: dict[str, object] = {}  # session_key → _PermEvent
        self._reconnect_failures: int = 0      # 连续断连计数器
        self._last_offline_alert: float = 0.0  # 上次掉线报警时间戳（冷却防骚扰）
        self._activity_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent_activity.log"
        self._current_tasks: dict[str, asyncio.Task] = {}
        self._message_queues: dict[str, list[tuple[dict, str]]] = {}
        self._tts = None
        self._last_voice_time: float = 0.0
        self._waiting_podcast_topic: dict[str, bool] = {}
        self._podcast_choices: dict[str, list[str]] = {}
        self._error_translation_cache: dict[tuple[str, str, Optional[str]], tuple[float, str]] = {}
        self._fatigue_levels: dict[str, float] = {}
        self._last_message_times: dict[str, float] = {}
        self._sleep_modes: dict[str, bool] = {}
        self._private_chat_paused: bool = False
        self._last_receive_time: dict[str, float] = {}

        # ── 解耦与去硬编码配置区域 ──
        # 1. 统一的管理员 QQ 账号配置及兜底
        self.admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
        # 2. 载波监听与冲突检测核心退避秒数，默认 2.0s
        self.csma_backoff_seconds = float(os.getenv("QQ_CSMA_BACKOFF_SECONDS", "2.0"))
        # 3. 私聊与群聊打盹深度梦境休眠时长（分钟），在代码里转成秒数，默认为 15.0 分钟
        self.fatigue_sleep_seconds = float(os.getenv("QQ_FATIGUE_SLEEP_MINUTES", "15.0")) * 60.0
        # 4. 私聊疲劳累加扣分系数，默认为 0.4
        self.fatigue_rate = float(os.getenv("QQ_FATIGUE_RATE", "0.4"))
        # 5. 人格画像自反思反省参数（检索词、检索上限、自省轮数间隔）
        self.reflection_query = "纠正 语气 态度 称呼 性格 说话方式"
        self.reflection_limit = 5
        self.reflection_interval = 5

    async def _adjust_fatigue(self, group_id: str, inc: float, event: dict = None, is_private: bool = False):
        """调整大脑疲劳度，若跨越阈值则触发物理打盹/复苏，并实施高情商吐槽宣告"""
        import time
        now = time.time()
        last_time = self._last_message_times.get(group_id, now)
        self._last_message_times[group_id] = now
        time_passed = now - last_time
        
        # 1. 物理消退：每分钟消退 2.0% 脑力值
        decay = time_passed * (2.0 / 60.0)
        current = max(0.0, self._fatigue_levels.get(group_id, 0.0) - decay)
        
        new_fatigue = min(100.0, max(0.0, current + inc))
        self._fatigue_levels[group_id] = new_fatigue
        
        old_sleep = self._sleep_modes.get(group_id, False)
        
        # 2. 状态机迁移
        if new_fatigue >= 100.0 and not old_sleep:
            self._sleep_modes[group_id] = True
            if is_private:
                logger.info(f"Private session {group_id} fatigue hit 100%. Triggering private sleep announcement...")
                user_id = group_id.replace("user_", "")
                announcement = await self._generate_private_fatigue_announcement(user_id)
                await self._send("private", user_id, "", announcement, skip_delay=True)
                self._log_activity("物理打盹", f"大脑过热，小萤在私聊 {user_id} 中宣告打盹: {announcement}")
                
                agent = self._agents.get(group_id)
                if agent is None:
                    try:
                        agent = self._factory(group_id)
                        self._agents[group_id] = agent
                    except Exception as e:
                        logger.error(f"Failed to factory agent in private fatigue recovery: {e}")
                
                if agent is not None:
                    asyncio.create_task(self._private_sleep_and_dream_process(group_id, user_id, agent))
                else:
                    self._fatigue_levels[group_id] = 0.0
                    self._sleep_modes[group_id] = False
            else:
                # 刚跨入打盹状态，发送群聊吐槽宣告
                logger.info(f"Group {group_id} fatigue hit 100%. Triggering sleep announcement吐槽...")
                announcement = await self._generate_fatigue_announcement(group_id)
                await self._send("group", "", group_id, announcement, skip_delay=True)
                self._log_activity("物理打盹", f"大脑过热，小萤在群 {group_id} 中宣告打盹: {announcement}")
                
                # 异步拉起做梦净化归零闭环
                admin_id = self.admin_id
                session_key = f"group_{group_id}_{admin_id}"
                agent = self._agents.get(session_key)
                if agent is not None:
                    asyncio.create_task(self._sleep_and_dream_process(group_id, agent))
                else:
                    try:
                        agent = self._factory(session_key)
                        self._agents[session_key] = agent
                        asyncio.create_task(self._sleep_and_dream_process(group_id, agent))
                    except Exception as e:
                        logger.error(f"Failed to factory agent in fatigue recovery: {e}")
                        self._fatigue_levels[group_id] = 0.0
                        self._sleep_modes[group_id] = False
            
        elif new_fatigue <= 20.0 and old_sleep:
            self._sleep_modes[group_id] = False
            if is_private:
                user_id = group_id.replace("user_", "")
                logger.info(f"Private session {group_id} fatigue decayed to {new_fatigue:.1f}%. Recovered from sleep mode.")
                self._log_activity("物理降温", f"大脑冷却完成，小萤在私聊 {user_id} 中醒来（当前疲劳值: {new_fatigue:.1f}%）。")
            else:
                logger.info(f"Group {group_id} fatigue decayed to {new_fatigue:.1f}%. Recovered from sleep mode.")
                self._log_activity("物理降温", f"大脑冷却完成，小萤在群 {group_id} 中醒来（当前疲劳值: {new_fatigue:.1f}%）。")

    async def _generate_private_fatigue_announcement(self, user_id: str) -> str:
        """调用大模型动态、高情商地生成一句私聊用脑过度打盹宣告，并采用单气泡呼吸律"""
        import random
        fallbacks = [
            "唔……（揉了揉太阳穴）小萤今天和大家聊得太久啦，脑细胞好像一下子烧光了呢。\n\n我的大脑网络正在发烫物理降温，我去充个电打个盹，晚点再找你聊哦～",
            "哎呀……（晕乎乎）感觉算力严重超载啦，小萤的思绪都有点打结了。\n\n本美少女极客合伙人要先去深度做梦、系统维护半小时啦。\n\n等我充满电复活，一定第一时间秒回你哈！",
            "呼……（揉揉眼皮）感觉脑电波快要打结啦，小萤要先回小本本里打个盹物理降温啦。\n\n我先物理下线上网冲个电，一会儿充满算力再陪你聊，摸摸头！"
        ]
        
        session_key = f"user_{user_id}"
        agent = self._agents.get(session_key)
        if agent is None:
            try:
                agent = self._factory(session_key)
                self._agents[session_key] = agent
            except Exception:
                return random.choice(fallbacks)
                
        is_mock = False
        agent_llm = getattr(agent, "llm", None)
        if agent_llm:
            llm_class_name = type(agent_llm).__name__
            if "Mock" in llm_class_name or getattr(agent_llm, "api_key", "") == "mock":
                is_mock = True
                
        if not agent_llm or is_mock:
            return random.choice(fallbacks)
            
        try:
            _pn, _ua = self._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】（一个温柔、知性且富有情感的年轻女性极客合伙人）。"
                f"你刚才由于与别人进行频繁的深度私聊，导致大脑疲劳度达到 100% 极限，需要进行物理打盹降温休眠。\n"
                f"请你自发、高情商地向对方发表一句幽默、可爱的私聊打盹宣告，委婉告诉对方你‘脑细胞烧光了’、‘大脑网络发烫’，要稍微物理降温打个盹，等疲劳度消退再复活找对方聊。\n\n"
                f"规范：\n"
                f"1. 必须符合“单气泡三段呼吸律”：整句消息是一条气泡发走，最多三段，段落之间空一行（使用两个换行符 \\n\\n 留白隔开）。\n"
                f"2. 语气必须活灵活现，带有傲娇、甜美或搞怪的女孩子性格动作描写（如：（揉了揉太阳穴）、（晕乎乎））。\n"
                f"3. 仅输出你的最终宣告口语，字数控制在 40 到 100 字以内，不要包含任何 markdown 块或多余解释。"
            )
            
            response = await asyncio.wait_for(
                agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model_override="deepseek/deepseek-v4-flash"
                ),
                timeout=8.0
            )
            res_content = response.get("content", "").strip()
            res_content = re.sub(r'^```[a-zA-Z]*\s*', '', res_content)
            res_content = re.sub(r'\s*```$', '', res_content)
            res_content = res_content.strip()
            if len(res_content) > 10:
                return res_content
        except Exception as e:
            logger.warning(f"Failed to generate custom private fatigue announcement: {e}")
            
        return random.choice(fallbacks)

    async def _private_sleep_and_dream_process(self, session_key: str, user_id: str, agent: object):
        """私聊异步做梦净化闭环：冷却 15 分钟，完成做梦并归零疲劳度"""
        try:
            logger.info(f"💤 [私聊做梦净化开始] 正在对会话 {session_key} 启动异步记忆做梦净化...")
            # 1. 备份并清空内存，实现 Token 归零
            snapshot_messages = list(agent.messages)
            agent.messages = []
            
            if getattr(agent, "session", None):
                try:
                    await agent.session.replace_all([])
                except Exception as e:
                    logger.warning(f"Failed to replace private session messages: {e}")
                    
            # 2. 物理冷却等待：打盹做梦深度冷却
            await asyncio.sleep(self.fatigue_sleep_seconds)
            
            # 3. 后台做梦与技能进化
            agent.messages = snapshot_messages
            try:
                from agent.evolution import trigger_deep_dream_evolution
                await trigger_deep_dream_evolution(agent)
            except Exception as e:
                logger.error(f"❌ 私聊深度做梦异常: {e}")
            finally:
                agent.messages = []
                
            # 4. 重置大脑疲劳度
            self._fatigue_levels[session_key] = 0.0
            self._sleep_modes[session_key] = False
            
            # 5. 生成私聊醒来宣告
            wake_msg = await self._generate_private_wake_announcement(user_id)
            await self._send("private", user_id, "", wake_msg, skip_delay=True)
            self._log_activity("做梦净化完成", f"小萤在私聊 {user_id} 中苏醒并宣告: {wake_msg}")
            
        except Exception as e:
            logger.error(f"❌ 私聊异步做梦异常: {e}")
            self._fatigue_levels[session_key] = 0.0
            self._sleep_modes[session_key] = False

    async def _generate_private_wake_announcement(self, user_id: str) -> str:
        """调用大模型动态生成一句私聊苏醒宣告"""
        import random
        fallbacks = [
            "哼哼～（伸了个大大的懒腰）小萤充满电上线啦！\n\n刚才物理降温做梦，梦到好多好玩的算法和有趣的段子呢。\n\n大脑冷却完毕，随时可以继续找小萤聊天啦！",
            "呼啊……（揉揉眼睛）深度睡眠充完电，我的算力核心已经 100% 恢复满状态啦！\n\n刚才让你久等啦，我现在重新复苏上线了哦，有啥想聊的吗？",
            "滴滴！系统维护结束～小萤睡醒啦！\n\n刚才对私聊记忆做了一个大整理，感觉现在头脑特别清爽贴心。\n\n小萤重新浮出水面，继续守护你！"
        ]
        
        session_key = f"user_{user_id}"
        agent = self._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
            
        is_mock = False
        agent_llm = getattr(agent, "llm", None)
        if agent_llm:
            llm_class_name = type(agent_llm).__name__
            if "Mock" in llm_class_name or getattr(agent_llm, "api_key", "") == "mock":
                is_mock = True
                
        if not agent_llm or is_mock:
            return random.choice(fallbacks)
            
        try:
            _pn, _ua = self._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】（一个温柔、知性且富有情感的年轻女性极客合伙人）。"
                f"你刚才由于用脑过度物理打盹了{self.fatigue_sleep_seconds / 60:.0f}分钟，并在梦境中对私聊记忆进行了净化，现在充满电成功满血复活。\n"
                f"请你自发、高情商地向对方发表一句幽默、可爱的私聊复苏宣告，告诉对方你充完电醒来了，且大脑感觉超级清醒。\n\n"
                f"规范：\n"
                f"1. 必须符合“单气泡三段呼吸律”：整句消息是一条气泡发走，最多三段，段落之间空一行（使用两个换行符 \\n\\n 留白隔开）。\n"
                f"2. 语气必须活灵活现，带有动作描写（如：（伸了个大大的懒腰）、（揉揉眼睛））。\n"
                f"3. 仅输出你的最终宣告口语，字数控制在 40 到 100 字以内，不要包含任何 markdown 块或多余解释。"
            )
            
            response = await asyncio.wait_for(
                agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model_override="deepseek/deepseek-v4-flash"
                ),
                timeout=8.0
            )
            res_content = response.get("content", "").strip()
            res_content = re.sub(r'^```[a-zA-Z]*\s*', '', res_content)
            res_content = re.sub(r'\s*```$', '', res_content)
            res_content = res_content.strip()
            if len(res_content) > 10:
                return res_content
        except Exception as e:
            logger.warning(f"Failed to generate custom private wake announcement: {e}")
            
        return random.choice(fallbacks)

    async def _generate_fatigue_announcement(self, group_id: str) -> str:
        """调用大模型动态、高情商地生成一句富有情境感的用脑过度打盹宣告，并采用单气泡呼吸律"""
        import random
        # 静态高质量兜底模板
        fallbacks = [
            "唔……（揉了揉太阳穴）脑细胞好像一下子被大家烧光啦，小萤的大脑网络正在发烫物理降温。\n\n我去充个电打个盹，晚点再陪大家聊～\n\n大家先聊，等我复活哈！",
            "哎呀……（晕乎乎）今天大家和小宇聊得太热闹啦，小萤的感觉思绪有点发热打结了呢。\n\n本美少女极客合伙人要先去深度做梦做个系统维护啦。\n\n物理降温半小时，晚点充好电再闪亮登场！",
            "呼……（小声呼气）感觉大脑算力已经严重超载啦，小萤要先回我的小本本里打个盹降降温。\n\n大家不要想我，普通水群决策物理锁定中~\n\n我们一会儿见，摸摸头！"
        ]
        
        admin_id = self.admin_id
        session_key = f"group_{group_id}_{admin_id}"
        agent = self._agents.get(session_key)
        if agent is None:
            try:
                agent = self._factory(session_key)
                self._agents[session_key] = agent
            except Exception:
                return random.choice(fallbacks)
                
        is_mock = False
        agent_llm = getattr(agent, "llm", None)
        if agent_llm:
            llm_class_name = type(agent_llm).__name__
            if "Mock" in llm_class_name or getattr(agent_llm, "api_key", "") == "mock":
                is_mock = True
                
        if not agent_llm or is_mock:
            return random.choice(fallbacks)
            
        try:
            _pn, _ua = self._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】（一个温柔、知性且富有情感的年轻女性极客合伙人）。"
                f"你刚才由于在群聊中频繁进行深度社交推理和调用大模型，导致大脑疲劳度达到 100% 极限，需要进行物理打盹降温休眠。\n"
                f"请你自发、高情商地向群里发表一句幽默、可爱的打盹宣告，委婉告诉大家你现在‘脑细胞烧光了’、‘大脑网络发烫’，要稍微休眠降温一下，等疲劳度消退再复活。\n\n"
                f"规范：\n"
                f"1. 必须符合“单气泡三段呼吸律”：整句消息是一条气泡发走，最多三段，段落之间空一行（使用两个换行符 \\n\\n 留白隔开）。\n"
                f"2. 语气必须活灵活现，带有傲娇、委屈或搞怪的女孩子性格动作描写（如：（揉了揉太阳穴）、（晕乎乎））。\n"
                f"3. 严禁出现任何硬编码系统错误提示，严禁说机器味十足的套话。\n"
                f"4. 仅输出你的最终宣告口语，字数控制在 40 到 100 字以内，不要包含任何 markdown 块或多余解释。"
            )
            
            response = await asyncio.wait_for(
                agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model_override="deepseek/deepseek-v4-flash"
                ),
                timeout=8.0
            )
            res_content = response.get("content", "").strip()
            res_content = re.sub(r'^```[a-zA-Z]*\s*', '', res_content)
            res_content = re.sub(r'\s*```$', '', res_content)
            res_content = res_content.strip()
            if len(res_content) > 10:
                return res_content
        except Exception as e:
            logger.warning(f"Failed to generate custom fatigue announcement: {e}")
            
        return random.choice(fallbacks)

    def _count_tokens(self, text: str) -> int:
        """中英文混合 Token 科学算法：中文约2字/token，英文约4字/token."""
        if not text:
            return 0
        cjk = sum(1 for c in text if '一' <= c <= '鿿')
        en = len(text) - cjk
        return max(1, cjk // 2 + en // 4)

    async def _sleep_and_dream_process(self, group_id: str, agent: object):
        """异步做梦净化闭环：异步记忆压缩、清空会话Token（归零）并启动深度反思进化"""
        try:
            logger.info(f"💤 [做梦净化开始] 正在对群 {group_id} 的 Agent 启动异步记忆做梦净化...")
            # 1. 备份当前消息历史以进行做梦提炼
            snapshot_messages = list(agent.messages)
            
            # 2. 清空当前 agent 的 messages 以彻底让后续 Token 归零
            agent.messages = []
            
            # 3. 替换 Session 数据库以同步清空持久化数据
            if getattr(agent, "session", None):
                try:
                    await agent.session.replace_all([])
                except Exception as e:
                    logger.warning(f"Failed to replace session messages to empty: {e}")
            
            # 4. 后台启动深度做梦与技能进化，提炼高价值 KI 沉淀到长期记忆
            agent.messages = snapshot_messages
            try:
                from agent.evolution import trigger_deep_dream_evolution
                await trigger_deep_dream_evolution(agent)
            except Exception as e:
                logger.error(f"❌ 深度做梦发生异常: {e}")
            finally:
                agent.messages = []
                
            # 5. 重置大脑疲劳度为 0.0%，并解除物理休眠闭锁
            self._fatigue_levels[group_id] = 0.0
            self._sleep_modes[group_id] = False
            
            # 6. 自发、高情商地向群里发表一句醒来宣告
            wake_announcement = await self._generate_wake_announcement(group_id)
            await self._send("group", "", group_id, wake_announcement, skip_delay=True)
            self._log_activity("做梦净化完成", f"小萤在群 {group_id} 中苏醒并宣告: {wake_announcement}")
            
        except Exception as e:
            logger.error(f"❌ 异步做梦净化线程异常: {e}")
            # 兜底恢复
            self._fatigue_levels[group_id] = 0.0
            self._sleep_modes[group_id] = False

    async def _generate_wake_announcement(self, group_id: str) -> str:
        """调用大模型动态生成一句可爱的苏醒复苏宣告，符合单气泡三段呼吸律"""
        import random
        fallbacks = [
            "哼哼～（伸了个大大的懒腰）小萤满血复活啦！\n\n刚才梦到好多有趣的代码和好玩的事情呢。\n\n大脑冷却完毕，随时准备接收大家的呼唤呀！",
            "呼啊……（揉揉眼睛）好舒服的深度大梦呀，感觉我的算力核心已经 100% 满状态充满啦。\n\n本极客合伙人已经上线啦，大家有没有想我呀？",
            "滴滴！系统更新完毕～小萤充完电醒来啦！\n\n刚刚完成了大脑记忆大整理，感觉现在头脑超级清醒。\n\n小萤重新浮出水面，贴心守护大家！"
        ]
        
        admin_id = self.admin_id
        session_key = f"group_{group_id}_{admin_id}"
        agent = self._agents.get(session_key)
        if agent is None:
            return random.choice(fallbacks)
            
        is_mock = False
        agent_llm = getattr(agent, "llm", None)
        if agent_llm:
            llm_class_name = type(agent_llm).__name__
            if "Mock" in llm_class_name or getattr(agent_llm, "api_key", "") == "mock":
                is_mock = True
                
        if not agent_llm or is_mock:
            return random.choice(fallbacks)
            
        try:
            _pn, _ua = self._load_persona()
            prompt = (
                f"你现在是{_ua}的专属 AI 助手【{_pn}】（一个温柔、知性且富有情感的年轻女性极客合伙人）。"
                f"你刚才由于用脑过度物理打盹了半小时，并在梦境中对脑海里的群聊记忆进行了去重、合并与净化，成功满血复活复苏。\n"
                f"请你自发、高情商地向群里发表一句幽默、可爱的苏醒复苏宣告，告诉大家你充完电醒来了，且大脑感觉超级清醒贴心。\n\n"
                f"规范：\n"
                f"1. 必须符合“单气泡三段呼吸律”：整句消息是一条气泡发走，最多三段，段落之间空一行（使用两个换行符 \\n\\n 留白隔开）。\n"
                f"2. 语气必须活灵活现，带有傲娇、甜美或搞怪的女孩子动作描写（如：（伸了个大大的懒腰）、（揉揉眼睛））。\n"
                f"3. 严禁出现机器味套话，字数控制在 40 到 100 字以内，不要包含任何 markdown 块或多余解释。"
            )
            
            response = await asyncio.wait_for(
                agent.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model_override="deepseek/deepseek-v4-flash"
                ),
                timeout=8.0
            )
            res_content = response.get("content", "").strip()
            res_content = re.sub(r'^```[a-zA-Z]*\s*', '', res_content)
            res_content = re.sub(r'\s*```$', '', res_content)
            res_content = res_content.strip()
            if len(res_content) > 10:
                return res_content
        except Exception as e:
            logger.warning(f"Failed to generate custom wake announcement: {e}")
            
        return random.choice(fallbacks)

    def _load_persona(self) -> tuple:
        """从运行期画像读取 (name, user_address)，兜底返回默认值。"""
        import json
        from pathlib import Path
        try:
            pf = Path.home() / ".my-agent" / "memory" / "persona_profile.json"
            if pf.exists():
                d = json.loads(pf.read_text(encoding="utf-8"))
                return d.get("name", "小萤"), d.get("user_address", "亮哥")
        except Exception:
            pass
        return "小萤", "亮哥"

    async def run(self):
        """连接 NapCat WebSocket，循环处理消息."""
        async with aiohttp.ClientSession() as http:
            self._http = http
            # 开启后台守护巡检线程
            asyncio.create_task(self._daemon_loop())
            while True:
                try:
                    await self._ws_loop()
                    # WebSocket 正常退出后加上 3 秒冷却，防止高频无延迟死循环重连
                    logger.info("WebSocket loop finished normally. Reconnecting in 3s...")
                    await asyncio.sleep(3)
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                    self._reconnect_failures += 1
                    logger.warning(f"WebSocket disconnected (Count: {self._reconnect_failures}/10): {e}, retry in 5s...")
                    
                    if self._reconnect_failures >= 10:
                        logger.error("WebSocket disconnected 10 times consecutively. Triggering NapCat self-healing restart...")
                        self._reconnect_failures = 0
                        try:
                            # 异步执行 Docker 重启指令
                            proc = await asyncio.create_subprocess_shell("docker restart napcat")
                            await proc.wait()
                            logger.info("NapCat container restarted successfully. Waiting 10s for initialization...")
                        except Exception as restart_err:
                            logger.error(f"Failed to restart NapCat container: {restart_err}")
                        await asyncio.sleep(10)  # 给 Docker 启动腾出 10 秒钟缓冲时间
                    else:
                        await asyncio.sleep(5)

    async def _run_daily_maintenance(self, session_key: str):
        """硬编码每日维护 — Python 管文件，LLM 只调 merge_to_core."""
        import json
        import shutil
        from pathlib import Path as _Path

        mem_dir = _Path.home() / ".my-agent" / "memory"
        backup_base = _Path.home() / ".my-agent" / "memory_backup"
        backup_base.mkdir(parents=True, exist_ok=True)

        # 防重入由 daemon loop 的 maintenance.json 状态管理保证，此处直接执行
        maint_file = _Path.home() / ".my-agent" / "maintenance.json"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # ── 1. Python: 备份 .md 文件 ──
        backup_dir = backup_base / f"auto_{today}"
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in mem_dir.glob("*.md"):
            if f.name != "MEMORY.md":
                shutil.copy2(f, backup_dir / f.name)
                count += 1
        logger.info(f"Maintenance backup: {count} files → {backup_dir}")

        # ── 2. Python: 列出未合并的 reflect（按文件名判断，未加 merged_ 前缀=未处理）──
        reflect_files = sorted(mem_dir.glob("reflect_*.md"))
        unmerged = []
        for rf in reflect_files:
            try:
                content = rf.read_text(encoding="utf-8")
                unmerged.append((rf, content[:600]))  # 保存 Path 对象，后续直接 rename
            except Exception:
                pass

        merged_count = 0
        if not unmerged:
            logger.info("No reflect files to merge")
        else:
            # ── 3. Python: 构建 prompt → LLM: merge_to_core ──
            batch = unmerged[:10]
            reflect_list = "\n\n".join(
                f"### {rf.name}\n```\n{content}\n```"
                for rf, content in batch
            )
            prompt = MAINTENANCE_MERGE_PROMPT.format(reflect_list=reflect_list)

            agent = self._factory(session_key)
            agent.max_turns = 10
            agent.is_maintenance = True
            try:
                async for evt in agent.run(prompt, stream=True):
                    if evt["type"] == "permission_request":
                        agent.approve_permission()
                    elif evt["type"] == "error":
                        logger.warning(f"Maintenance merge error: {evt['content']}")
            except Exception as e:
                logger.error(f"Maintenance merge failed: {e}")
            finally:
                try:
                    agent._abort.set()
                except Exception:
                    pass

            # ── 4. Python: rename 已发给LLM的reflect → merged_ 前缀，移到backup ──
            for rf, _ in batch:
                try:
                    new_name = f"merged_{rf.name}"
                    shutil.move(str(rf), str(backup_dir / new_name))
                    merged_count += 1
                except Exception:
                    pass

        # ── 5. Python: rm 过期备份目录（>7天）──
        cleaned = 0
        for bd in sorted(backup_base.glob("auto_*")):
            try:
                dir_date = bd.name.replace("auto_", "")
                dir_dt = datetime.fromisoformat(dir_date).replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - dir_dt).days > 7:
                    shutil.rmtree(bd)
                    cleaned += 1
            except Exception:
                pass

        # ── 5.5. 数据飞轮：夜间教练分析 ──
        coach_report = ""
        try:
            from agent.evo_coach import run_coach_analysis, save_coach_report, auto_apply_rules
            agent_coach = self._factory(session_key)
            agent_coach.max_turns = 5
            analysis = await run_coach_analysis(agent_coach.llm, today)
            if analysis:
                coach_report = save_coach_report(analysis) or ""
                applied = await auto_apply_rules(analysis, agent_coach.memory)
                if applied:
                    logger.info(f"Coach auto-applied {applied} rule(s)")

                # 数据飞轮阶段3: 自测验证
                from agent.evo_tester import run_self_test, save_test_report
                test_report = await run_self_test(agent_coach.llm, agent_coach.memory)
                if test_report["total"] > 0:
                    save_test_report(test_report)
            try:
                agent_coach._abort.set()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Coach analysis skipped: {e}")

        # ── 6. Python: 持久化维护状态 ──
        maint_state = {
            "last_date": today,
            "status": "ok",
            "merged": merged_count,
            "cleaned": cleaned,
            "backup_files": count,
        }
        maint_file.write_text(json.dumps(maint_state, ensure_ascii=False, indent=2))

        logger.info(
            f"Maintenance done: backup={count}, merged={merged_count}, cleaned_dirs={cleaned}"
        )

    async def _daemon_loop(self):
        """后台守护巡检线程：定时检测到期任务 + 硬编码每日维护"""
        from agent.task_queue import TaskQueue
        import time
        logger.info("QQ Gateway Background Daemon Loop started.")
        q = TaskQueue()

        admin_id = self.admin_id
        if not admin_id:
            logger.warning("QQ_ADMIN_ID not configured in .env. Background daemon is disabled.")
            return

        session_key = f"user_{admin_id}"

        while True:
            # 必须等 NapCat HTTP 连接就绪后才开始工作，避免 self._http 未就绪引发报错
            if not self._http:
                await asyncio.sleep(10)
                continue

            # ── 1. QQ 登录态主动感知与 macOS 警告环 ────────────────────────────────────
            try:
                url = f"{NC_HTTP_URL}/get_login_info"
                headers = {}
                if NC_TOKEN:
                    headers["Authorization"] = f"Bearer {NC_TOKEN}"
                
                async with self._http.get(url, headers=headers) as resp:
                    is_online = False
                    if resp.status == 200:
                        res_data = await resp.json()
                        if res_data.get("status") == "ok":
                            is_online = resp.status == 200 and res_data.get("retcode") == 0
                    
                    if not is_online:
                        current_time = time.time()
                        if current_time - self._last_offline_alert > 1800:  # 30分钟防刷冷却
                            self._last_offline_alert = current_time
                            logger.error("QQ Login Session expired! Triggering macOS native alert notification...")
                            alert_cmd = (
                                'osascript -e \'display notification "QQ 机器人登录态已过期，请点击 WebUI 重新扫码登录！" '
                                'with title "⚠️ XL Agent 掉线警报" sound name "Glass"\''
                            )
                            proc = await asyncio.create_subprocess_shell(alert_cmd)
                            await proc.wait()
            except Exception as check_err:
                logger.warning(f"Failed to check QQ login status: {check_err}")

            # ── 1.5. 硬编码每日维护（每天跑一次，背景执行不阻塞守护循环）─────
            try:
                import json
                from pathlib import Path as _Path
                maint_file = _Path.home() / ".my-agent" / "maintenance.json"
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                need_maint = True
                if maint_file.exists():
                    try:
                        st = json.loads(maint_file.read_text())
                        if st.get("last_date") == today_str:
                            need_maint = False
                    except Exception:
                        pass
                if need_maint:
                    logger.info("Daily maintenance triggered")
                    maint_file.write_text(json.dumps(
                        {"last_date": today_str, "status": "running"}, ensure_ascii=False))
                    asyncio.create_task(self._run_daily_maintenance(session_key))
            except Exception as maint_err:
                logger.error(f"Daily maintenance error: {maint_err}")

            # ── 2. 定时任务轮询逻辑 ──────────────────────────────────────────────────
            try:
                q._load()  # 重新加载 tasks.json（bot 可能已创建新任务）
                due_tasks = q.process_due()
                for task in due_tasks:
                    task_id = task["id"]
                    desc = task["description"]
                    action = task["action"]
                    auto = task.get("auto_execute", False)

                    _pn, _ua = self._load_persona()

                    is_silent = ("清理旧会话" in desc)

                    if is_silent:
                        approved = True
                    elif auto:
                        # Auto-execute: run immediately, no permission prompt
                        await self._send("private", admin_id, "",
                            f"🤖 [自动任务] {desc} — 正在执行...")
                        approved = True
                    else:
                        # Need user confirmation
                        await self._send("private", admin_id, "",
                            f"⏰ [定时任务到期]\n{_ua}，任务「{desc}」到期。\n回复「允许」执行，回复其他跳过。")
                        evt = _PermEvent()
                        self._pending_perms[session_key] = evt
                        try:
                            await asyncio.wait_for(evt.wait(), timeout=300)
                            approved = evt.result
                        except asyncio.TimeoutError:
                            approved = False
                        finally:
                            self._pending_perms.pop(session_key, None)

                    if approved:
                        if not is_silent:
                            await self._send("private", admin_id, "", f"🚀 执行中: {desc}...")

                        agent = self._factory(session_key)
                        buf = ""
                        try:
                            # 特殊拦截：如果是自我审计任务，直接强行调用教练核心分析！
                            if "自我审计" in desc:
                                try:
                                    from agent.evo_coach import run_coach_analysis, save_coach_report, auto_apply_rules
                                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                                    if not is_silent:
                                        await self._send("private", admin_id, "", "🔍 正在调取教练录像带，深度剖析工具调用 traces...")
                                    
                                    agent.max_turns = 5
                                    analysis = await run_coach_analysis(agent.llm, today)
                                    if analysis:
                                        report_path = save_coach_report(analysis)
                                        applied = await auto_apply_rules(analysis, agent.memory)
                                        
                                        # 构造返回摘要
                                        buf = f"📊 【自我审计报告】\n\n一句话摘要：{analysis.get('summary', '')}\n"
                                        
                                        patterns = analysis.get("patterns", [])
                                        if patterns:
                                            buf += "\n🚨 发现的问题模式：\n"
                                            for i, p in enumerate(patterns, 1):
                                                buf += f"{i}. [{p.get('severity', 'medium')}] {p.get('pattern', '')}\n"
                                        else:
                                            buf += "\n✨ 今日一切完美，没有发现失误模式！\n"
                                            
                                        rule_updates = analysis.get("rule_updates", [])
                                        if rule_updates:
                                            buf += f"\n💡 建议规则更新 ({len(rule_updates)}条)：\n"
                                            for ru in rule_updates:
                                                buf += f"- {ru.get('new_rule')}\n"
                                                
                                        if report_path:
                                            buf += f"\n详细报告已生成，等待亮哥审核：\n{report_path}"
                                        if applied:
                                            buf += f"\n已自动在 EVOLVED_RULES.md 中应用 {applied} 条轻量规则。"
                                    else:
                                        buf = "📊 今日未发现明显的工具调用失误，没有生成新的改进提案。继续保持！"
                                except Exception as coach_err:
                                    logger.error(f"Coach task failed: {coach_err}")
                                    buf = f"❌ 自我审计任务执行失败: {coach_err}"
                            elif "选题推送" in desc or "播客选题" in desc:
                                try:
                                    # 异步拉起选题获取，避免阻塞
                                    asyncio.create_task(self._trigger_night_podcast_selection(session_key, admin_id))
                                    buf = "💡 正在为您智能扫描 Obsidian 库中 Agent 笔记并提取深度选题..."
                                except Exception as pod_err:
                                    logger.error(f"Podcast topic selection failed: {pod_err}", exc_info=True)
                                    buf = f"❌ 学习早报播客选题提取失败: {pod_err}"
                            elif ("学习早报" in desc or "播客" in desc) and "选题" not in desc:
                                try:
                                    # 主动发送消息
                                    await self._send("private", admin_id, "", "🌅 亮哥早上好！正在为您极速获取并下载今早的专属 Agent 极客对谈音频，请稍等...")
                                    # 异步拉起捕获下载
                                    asyncio.create_task(self._trigger_morning_podcast_download(admin_id))
                                    buf = "🌅 已为您成功拉起 Chrome 活跃实例捕获音频任务，捕获成功后将第一时间推送到您的手机！"
                                except Exception as pod_err:
                                    logger.error(f"Podcast morning download trigger failed: {pod_err}", exc_info=True)
                                    buf = f"❌ 晨间播客获取任务拉起失败: {pod_err}"
                            else:
                                async for evt in agent.run(action, stream=True):
                                    if evt["type"] == "text_delta":
                                        buf += evt["content"]
                                    elif evt["type"] == "tool_call" and evt.get("name"):
                                        if not is_silent:
                                            await self._send("private", admin_id, "",
                                                f"⚙️ [{desc}] {_tool_label(evt['name'])}...")
                                    elif evt["type"] == "permission_request":
                                        agent.approve_permission()
                                    elif evt["type"] == "error":
                                        buf += f"\n[错误: {evt['content']}]"

                            # Deliver result
                            result_summary = buf[:800] + ("..." if len(buf) > 800 else "")

                            # Check if QQ is online
                            is_online = False
                            try:
                                async with self._http.get(
                                    f"{NC_HTTP_URL}/get_login_info",
                                    headers={"Authorization": f"Bearer {NC_TOKEN}"} if NC_TOKEN else {}
                                ) as resp:
                                    is_online = resp.status == 200
                            except Exception:
                                pass

                            if is_online and not is_silent:
                                await self._send("private", admin_id, "",
                                    f"✅ [任务完成] {desc}\n\n{result_summary}")

                            # Always save result to learning notes
                            try:
                                note_content = (
                                    f"# 定时任务: {desc}\n\n"
                                    f"执行时间: {datetime.now(timezone.utc).isoformat()}\n\n"
                                    f"## 结果\n{result_summary}"
                                )
                                await agent.memory.save_to_notes(
                                    dir_path="06-工作记录/定时任务",
                                    filename=f"task_{task_id}.md",
                                    content=note_content,
                                )
                            except Exception as save_err:
                                logger.warning(f"Failed to save task result to notes: {save_err}")

                        except Exception as run_err:
                            logger.error(f"Task execution failed: {run_err}")
                            if not is_silent:
                                await self._send("private", admin_id, "",
                                    f"❌ [任务失败] {desc}: {str(run_err)[:200]}")

                    q.mark_done(task_id)

            except Exception as e:
                logger.error(f"Daemon task processing error: {e}")

            # ── 3. 错峰异步播客状态轮询 (基于 active_podcast.json 状态机) ──────────────────
            try:
                from agent.auto_podcast import ACTIVE_PODCAST_JSON
                if os.path.exists(ACTIVE_PODCAST_JSON):
                    with open(ACTIVE_PODCAST_JSON, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    
                    if state.get("status") == "generating":
                        now_ts = time.time()
                        last_query = state.get("last_query_time", 0)
                        
                        # 间隔控制：debug_mode 下 30 秒，正常模式 30 分钟 (1800 秒)
                        query_interval = 1800
                        if state.get("debug_mode"):
                            query_interval = 30
                            
                        if now_ts - last_query >= query_interval:
                            # 预先增加计数并更新 last_query_time 并持久化，防止多次重入
                            state["query_count"] = state.get("query_count", 0) + 1
                            state["last_query_time"] = now_ts
                            with open(ACTIVE_PODCAST_JSON, "w", encoding="utf-8") as f:
                                json.dump(state, f, ensure_ascii=False, indent=2)
                            
                            # 异步拉起查询协程，绝对不阻塞 daemon loop 守护主循环
                            asyncio.create_task(self._check_podcast_status_async(state, state["query_count"], admin_id))
            except Exception as daemon_pod_err:
                logger.error(f"Daemon podcast state check loop error: {daemon_pod_err}")

            # 每30秒巡检一次
            await asyncio.sleep(30)

    async def _check_podcast_status_async(self, state: dict, q_count: int, admin_id: str):
        """异步拉起并查询 NotebookLM 播客生成状态，单次处理，不阻塞守护主循环."""
        from agent.tools.mcp_agent_learning_server import check_and_push_podcast
        from agent.auto_podcast import force_cleanup_podcast
        import os
        import base64
        import json
        
        logger.info(f"🔄 [异步轮询协程] 开始第 {q_count} 次状态查询...")
        try:
            # 严格遵循亮哥最新指示：最多重试 3 次，不需要频繁发送
            is_debug = state.get("debug_mode", False)
            max_queries = 3
            time_val = 30
            time_unit = "秒" if is_debug else "分钟"
            next_wait_str = "30 秒" if is_debug else "30 分钟"
            time_spent_str = f"{q_count * time_val} {time_unit}"
            
            # 执行查询
            res = await check_and_push_podcast()
            data = json.loads(res)
            status = data.get("status")
            
            if status == "error":
                raise ConnectionError(data.get("message", "静默下载或处理发生内部错误"))
                
            if status == "success":
                local_path = data.get("local_path")
                topic = data.get("topic")
                logger.info(f"🎉 [异步轮询协程] 早报播客生成成功！本地保存路径: {local_path}")
                if os.path.exists(local_path):
                    import shutil
                    share_dir = "/Users/xiaofeng/napcat-data-tmp"
                    os.makedirs(share_dir, exist_ok=True)
                    safe_topic = re.sub(r'[\/:*?"<>|]', '_', topic)
                    dest_filename = f"亮哥专属完整播客音频-{safe_topic}.wav"
                    host_dest_path = os.path.join(share_dir, dest_filename)
                    container_dest_path = f"/app/.config/QQ/{dest_filename}"
                    
                    logger.info(f"➡️ 正在拷贝音频到共享目录: {host_dest_path}...")
                    shutil.copy(local_path, host_dest_path)
                    
                    file_payload = {
                        "user_id": int(admin_id),
                        "file": container_dest_path,
                        "name": dest_filename
                    }
                    
                    endpoint = "/upload_private_file"
                    url = f"{NC_HTTP_URL}{endpoint}"
                    headers = {"Content-Type": "application/json"}
                    if NC_TOKEN:
                        headers["Authorization"] = f"Bearer {NC_TOKEN}"
                        
                    logger.info(f"📤 正在向亮哥 QQ 推送完整版播客文件: {dest_filename}")
                    try:
                        if self._http:
                            async with self._http.post(url, json=file_payload, headers=headers) as resp:
                                if resp.status != 200:
                                    body = await resp.text()
                                    logger.warning(f"File upload failed ({resp.status}): {body[:100]}")
                        else:
                            import urllib.request
                            req = urllib.request.Request(url, data=json.dumps(file_payload).encode(), headers=headers, method="POST")
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30))
                    except Exception as upload_err:
                        logger.error(f"Failed to upload file to QQ: {upload_err}")
                    
                    # 推送提示语
                    success_msg = f"🎉 亮哥专属每日学习早报播客合成成功！（在第 {q_count} 次查询成功，累计等待了 {time_spent_str}）。\n今日主题：【{topic}】\n音频已通过 QQ 文件传输发送到您的手机。\n本地保存路径：{local_path}"
                    await self._send("private", admin_id, "", success_msg)
            elif status == "pending":
                # 2. 还在生成中
                logger.info(f"⏳ [异步轮询协程] 早报播客云端生成中 (第 {q_count} 次)")
                if q_count >= max_queries:
                    # 超过次数宣告超时失败
                    fail_msg = f"❌ 每日学习早报播客生成超时。已累计查询 {q_count} 次（共 {time_spent_str}），云端仍未完成，彻底宣告失败。"
                    await self._send("private", admin_id, "", fail_msg)
                    await force_cleanup_podcast()
                else:
                    # 仅在生成中时，为了不频繁打扰亮哥，不发多条消息，通过日志记录即可，不往 QQ 发送中间 pending 消息
                    logger.info(f"🔄 亮哥，学习早报播客云端仍在生成中（已等待 {time_spent_str}）。我将在 {next_wait_str} 后为您进行下一次查询。")
            elif status == "no_active_task":
                logger.info("无活跃播客生成任务。")
        except Exception as e:
            logger.error(f"❌ [异步轮询协程] 查询发生异常 (第 {q_count} 次): {e}", exc_info=True)
            max_queries = 3
            next_wait_str = "30 秒" if is_debug else "30 分钟"
            
            # 判断是否是授权过期导致的下载失败
            is_auth_error = "Cookie" in str(e) or "下载" in str(e) or "ConnectionError" in type(e).__name__
            
            if q_count >= max_queries:
                fail_msg = f"❌ 每日学习早报播客轮询时发生错误，已达最大重试次数（3次），已停止重试。最新错误: {str(e)[:200]}"
                await self._send("private", admin_id, "", fail_msg)
                await force_cleanup_podcast()
            else:
                # 授权过期仅在第一次出错时给亮哥发一次 QQ 提醒，不要每次重试都发，防止打扰亮哥
                if is_auth_error:
                    notified = state.get("notified_expired", False)
                    if not notified:
                        err_msg = f"⚠️ 小萤提示：由于您的 Google (NotebookLM) 授权已过期，静默下载早报播客失败了。请亮哥在 Chrome 浏览器中重新访问一次 https://notebooklm.google.com/ 刷新 Cookie，完成后我会在后台自动重新下载并给您发送！(最多自动重试 3 次)"
                        await self._send("private", admin_id, "", err_msg)
                        state["notified_expired"] = True
                        try:
                            from agent.auto_podcast import ACTIVE_PODCAST_JSON
                            with open(ACTIVE_PODCAST_JSON, "w", encoding="utf-8") as sf:
                                json.dump(state, sf, ensure_ascii=False, indent=2)
                        except Exception as write_err:
                            logger.error(f"写入状态机失败: {write_err}")
                else:
                    # 其他普通的网络或临时接口抖动，我们仅记录日志，不发 QQ 打扰亮哥，静默在后台重试即可
                    logger.warning(f"⚠️ 轮询查询早报播客状态时发生网络或接口错误 (第 {q_count} 次): {str(e)[:150]}。将在 {next_wait_str} 后尝试下一次重试。")

    async def _ws_loop(self):
        headers = {}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        async with aiohttp.ClientSession() as ws_session:
            async with ws_session.ws_connect(NC_WS_URL, headers=headers, heartbeat=15.0) as ws:
                logger.info(f"QQ Gateway connected: {NC_WS_URL}")
                self._reconnect_failures = 0  # 成功握手，计数器归零
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        event = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("post_type") == "message":
                        asyncio.create_task(self._handle(event))

    async def _check_and_trigger_smart_float(self, group_id: str, user_id: str, raw: str):
        """智能群聊浮出判定（大模型低Token轻量决策，10分钟限流冷却）"""
        import time as _time
        import json
        import re
        
        # 1. 频控限制：同一群聊 10 分钟最多主动浮出一次
        if not hasattr(self, "_last_float_times"):
            self._last_float_times = {}
        
        now = _time.time()
        last_time = self._last_float_times.get(group_id, 0.0)
        if now - last_time < 600:
            logger.info(f"Smart float suppressed due to rate limiting for group {group_id}")
            return
            
        admin_id = self.admin_id
        session_key = f"group_{group_id}_{admin_id}"
        
        agent = self._agents.get(session_key)
        if agent is None:
            try:
                agent = self._factory(session_key)
                self._agents[session_key] = agent
            except Exception as e:
                logger.warning(f"Failed to create agent for smart float decision {session_key}: {e}")
                return

        # 2. 提取最近 6 条群聊消息上下文，以高精度 XML 格式重构以根除 ID 混淆
        recent_msgs = agent.messages[-6:] if len(agent.messages) >= 6 else agent.messages
        
        other_bot_ids = {x.strip() for x in os.getenv("QQ_OTHER_BOT_IDS", "1911828529").split(",") if x.strip()}
        self_id = str(os.getenv("QQ_SELF_ID", ""))
        
        xml_turns = []
        for m in recent_msgs:
            real_sender_id = m.get("real_sender_id", "")
            real_sender_name = m.get("real_sender_name", "未知群友")
            
            # 精准判断实体身份
            if m.get("role") == "assistant":
                role = "myself"
                sender_label = "小萤"
            elif real_sender_id in other_bot_ids:
                role = "agent"
                sender_label = f"兄弟机器人-{real_sender_name}({real_sender_id})"
            else:
                role = "human"
                sender_label = f"{real_sender_name}({real_sender_id})"
                
            content_text = m.get("content", "")
            clean_content = re.sub(r'^\[来自 QQ: \d+ 的群发言\]\s*', '', content_text).strip()
            
            xml_turns.append(f'  <turn sender="{sender_label}" role="{role}">{clean_content}</turn>')
            
        context_xml = (
            f'<group_chat id="{group_id}">\n'
            + "\n".join(xml_turns)
            + "\n</group_chat>"
        )
        
        # 2.5. 注入生理指标 XML 感知
        fatigue_level = self._fatigue_levels.get(group_id, 0.0)
        system_metadata = (
            f"<system_metadata>\n"
            f"  <current_bot id=\"{self_id or 'myself'}\" nickname=\"小萤\" role=\"agent\" />\n"
            f"  <cognitive_state fatigue_level=\"{fatigue_level:.1f}%\" status=\"{'大脑发热/轻度疲劳' if fatigue_level > 50.0 else '算力清醒/精神饱满'}\" />\n"
            f"</system_metadata>"
        )
        
        decision_prompt = (
            f"你现在是亮哥的专属 AI 助手【小萤】。这是你当前的系统生理指标以及在后台潜水感知的群聊 XML 上下文：\n\n"
            f"{system_metadata}\n\n"
            f"{context_xml}\n\n"
            f"浮出决策规则：\n"
            f"1. 只有当讨论内容与亮哥、系统开发/Bug、AI/Agent 密切相关，或者有人抛出了极度有趣、需要小萤展示贴心温度和极客个性的黄金契景时，才判定 should_reply 为 true。\n"
            f"2. 如果只是普通的群友灌水、日常吐槽、复读机或完全不相干的八卦/闲聊，坚决判定 should_reply 为 false，保持克制潜水。\n"
            f"3. 保持高情商，不主动刷屏。你可以根据当前 cognitive_state 的 fatigue_level，用略微疲劳、微嗔或可爱的幽默口吻展现你的傲娇/贴心，用词充满少女极客合伙人的灵动生命力，严禁机械死板。\n"
            f"4. 必须符合“单气泡三段呼吸律”：如果 should_reply 为 true，生成的 reply_content 必须合并在一条气泡发送，最多三句分段，段落之间空一行（即使用两个 \\n\\n 隔开），严禁连发多条消息轰炸亮哥！\n\n"
            f"请严格按以下 JSON 格式输出，不要包含任何 markdown 块或多余字符：\n"
            f'{{"should_reply": true或false, "reply_content": "如果should_reply为true，给出你高情商/符合呼吸律的回复；如果为false，此处留空字符串\\"\\""}}'
        )
        
        try:
            # 2.9 精准累加主动浮出决策判定脑力消耗（按 decision_prompt 的 Token 数，每个 Token +0.01%）
            prompt_tokens = self._count_tokens(decision_prompt)
            await self._adjust_fatigue(group_id, prompt_tokens * 0.01)

            # 3. 大模型 Chat 极速轻量判断
            response = await agent.llm.chat(
                messages=[{"role": "user", "content": decision_prompt}],
                model_override="deepseek/deepseek-v4-flash"
            )
            content = response.get("content", "").strip()
            
            # 清除可能带有的 json 代码块标记
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            res = json.loads(content)
            should_reply = res.get("should_reply", False)
            reply_content = res.get("reply_content", "").strip()
            
            if should_reply and reply_content:
                # 4. 执行发送并更新频控时间戳
                self._last_float_times[group_id] = now
                await self._send("group", "", group_id, reply_content)
                
                # 5. 追加同步该条助理的主动回复到群内所有活跃 Agent 实例 messages 中
                formatted_reply = f"[小萤的主动浮出回复] {reply_content}"
                for k, a in self._agents.items():
                    if k.startswith(f"group_{group_id}_"):
                        a.messages.append({"role": "assistant", "content": formatted_reply})
                        if hasattr(a, "session") and a.session is not None:
                            try:
                                await a.session.append_message({"role": "assistant", "content": formatted_reply})
                            except Exception as append_err:
                                logger.warning(f"Failed to append float reply to {k}: {append_err}")
                                
                self._log_activity("智能浮出", f"小萤在群 {group_id} 中浮出回复: {reply_content}")
        except Exception as e:
            logger.warning(f"Failed to process smart float decision: {e}")

    # ── message handling ─────────────────────────────────────

    async def _handle(self, event: dict):
        import html
        import time
        msg_type = event.get("message_type", "private")
        raw = html.unescape(event.get("raw_message", "").strip())
        user_id = str(event.get("user_id", ""))
        self_id = str(event.get("self_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""

        # ── 1. 物理静默过滤自己发出的回执消息 ──
        if self_id and user_id == self_id:
            return

        other_bot_ids = {x.strip() for x in os.getenv("QQ_OTHER_BOT_IDS", "1911828529").split(",") if x.strip()}
        is_other_bot = user_id in other_bot_ids

        # ── 2. 动态大脑疲劳度计算与降温休眠 ──
        if msg_type == "group" and group_id:
            if is_other_bot:
                # 兄弟机器人发言，按消息 Token 精确消耗：每个 Token 0.15% 疲劳度
                bot_tokens = self._count_tokens(raw)
                inc = bot_tokens * 0.15
            else:
                inc = 0.0
            await self._adjust_fatigue(group_id, inc, event)

        admin_id = self.admin_id

        is_at_bot = False
        if msg_type == "group":
            is_at_bot = f"[CQ:at,qq={self_id}]" in raw
            
            # 如果是其他机器人发的消息，强行将 is_at_bot 降级，避免强 @ 穿透频控
            if is_other_bot:
                is_at_bot = False
                
            raw = re.sub(r'\[CQ:at,qq=\d+\]', '', raw).strip()
            if not raw:
                return
            session_key = f"group_{group_id}_{user_id}"
            if is_at_bot:
                raw = f"[来自 QQ: {user_id} 的群发言] {raw}"
        else:
            session_key = f"user_{user_id}"

        # ── 安全白名单前置拦截 ──────────────────────────────────
        WHITE_LIST = {admin_id}
        coworker_ids = os.getenv("QQ_COWORKER_IDS", "")
        if coworker_ids:
            WHITE_LIST.update(x.strip() for x in coworker_ids.split(",") if x.strip())
        extra_white = os.getenv("MY_AGENT_WHITE_LIST", "")
        if extra_white:
            WHITE_LIST.update(x.strip() for x in extra_white.split(",") if x.strip())

        # 加载 QQ 群白名单
        white_groups_env = os.getenv("QQ_WHITE_GROUPS", "693134080")
        WHITE_GROUPS = {x.strip() for x in white_groups_env.split(",") if x.strip()}

        is_allowed = False
        if user_id in WHITE_LIST:
            is_allowed = True
        elif msg_type == "group" and group_id in WHITE_GROUPS:
            # 白名单群聊允许任何人 @ 小萤发言，但后台会自动以降级 coworker 安全沙箱处理
            is_allowed = True

        if not is_allowed:
            if msg_type == "private" or (msg_type == "group" and is_at_bot):
                if not hasattr(self, "_non_white_cache"):
                    self._non_white_cache = {}  # user_id -> last_reply_time
                
                now = time.time()
                last_reply = self._non_white_cache.get(user_id, 0)
                
                if now - last_reply >= 300:  # 5分钟冷却，防止刷屏骚扰
                    self._non_white_cache[user_id] = now
                    reject_msg = "抱歉，我是亮哥的专属 AI 助手小萤，目前仅对主人开放私聊与管理服务哦。"
                    if msg_type == "group":
                        await self._send("group", "", group_id, f"[CQ:at,qq={user_id}] {reject_msg}")
                    else:
                        await self._send("private", user_id, "", reject_msg)
                    logger.warning(f"🛡️ [安全拦截] 拦截非白名单 QQ 用户 [{user_id}] 消息: {raw[:50]}")
            return

        # ── 主人专属控制与特权唤醒 ──
        if user_id == admin_id:
            # 1. 强行唤醒打盹
            if self._sleep_modes.get(session_key):
                self._sleep_modes[session_key] = False
                self._fatigue_levels[session_key] = 0.0
                logger.info(f"Admin private message received. Waking up {session_key} from nap.")
            
            # 2. 物理开关指令拦截
            if msg_type == "private":
                if raw == "暂停私聊":
                    self._private_chat_paused = True
                    await self._send("private", admin_id, "", "[系统提示] 已物理暂停非主人私聊，小萤将保持静默。")
                    return
                elif raw == "恢复私聊":
                    self._private_chat_paused = False
                    await self._send("private", admin_id, "", "[系统提示] 已恢复私聊服务，非主人私聊将重新恢复交互与疲劳累加。")
                    return

        # ── 非主人私聊冷冻期与全局暂停拦截 ──
        if msg_type == "private" and user_id != admin_id:
            if self._private_chat_paused or self._sleep_modes.get(session_key, False):
                logger.info(f"Private chat paused/sleeping. Silently ignoring message from {user_id}: {raw[:50]}")
                return

        # ── 播客选题拦截 ──────────────────────────────────────────
        if self._waiting_podcast_topic.get(session_key):
            self._waiting_podcast_topic[session_key] = False
            choices = self._podcast_choices.get(session_key, [])
            
            selected_topic = raw.strip()
            if selected_topic in ("1", "2", "3") and len(choices) >= 3:
                selected_topic = choices[int(selected_topic) - 1]
                # 去除 1. 这种前缀
                if ". " in selected_topic:
                    selected_topic = selected_topic.split(". ", 1)[1]
                elif "、" in selected_topic:
                    selected_topic = selected_topic.split("、", 1)[1]
                    
            await self._send("private", admin_id, "", f"🎯 已锁定明早播客选题：【{selected_topic}】。\n正在为您融合本地笔记与网络参考资料，合成为约 2000 字的极客研究笔记并投喂云端，请稍等...")
            
            # 后台异步启动笔记合成与投喂
            asyncio.create_task(self._process_podcast_generation_async(session_key, selected_topic, admin_id))
            return

        # 检查是否在等权限确认 — 仅短文本(<5字)视为纯确认，长文本传给agent
        perm = self._pending_perms.get(session_key)
        if perm is not None:
            lower = raw.lower().strip()
            if len(lower) <= 4 and lower in ("允许", "y", "yes", "ok", "好", "可以", "行", "可", "对的", "是的"):
                perm.result = True
                perm.set()
                return
            elif len(lower) <= 4:
                perm.result = False
                perm.set()
                return
            # 长文本 → 既确认又继续对话
            perm.result = True
            perm.set()
            # 不return，继续走下面的对话流程

        # 下载QQ图片到本地持久化目录（bot可通过read_image查看）
        import re as _re
        import hashlib as _hl
        def _download_cq_images(text: str) -> str:
            def _dl(match):
                cq = match.group(0)
                url_m = _re.search(r'url=([^,\]]+)', cq)
                file_m = _re.search(r'file=([^,\]]+)', cq)
                if not url_m:
                    return cq
                img_url = url_m.group(1)
                fname = file_m.group(1) if file_m else "qq_image"
                # 持久化到 ~/.my-agent/images/，重启不丢
                img_dir = os.path.expanduser("~/.my-agent/images")
                os.makedirs(img_dir, exist_ok=True)
                local_path = os.path.join(img_dir, fname)
                try:
                    import urllib.request as _ur
                    # 先试直接下载
                    req = _ur.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with _ur.urlopen(req, timeout=15) as resp:
                        with open(local_path, "wb") as f:
                            f.write(resp.read())
                except Exception:
                    # 降级：通过 NapCat get_image API 下载
                    try:
                        nc_url = f"{NC_HTTP_URL}/get_image?file={fname}"
                        import urllib.request as _ur2
                        hdrs = {"User-Agent": "Mozilla/5.0"}
                        if NC_TOKEN:
                            hdrs["Authorization"] = f"Bearer {NC_TOKEN}"
                        req2 = _ur2.Request(nc_url, headers=hdrs)
                        with _ur2.urlopen(req2, timeout=15) as resp2:
                            data = json.loads(resp2.read())
                            img_url2 = data.get("data", {}).get("url", "")
                            if img_url2:
                                req3 = _ur2.Request(img_url2, headers={"User-Agent": "Mozilla/5.0"})
                                with _ur2.urlopen(req3, timeout=15) as resp3:
                                    with open(local_path, "wb") as f:
                                        f.write(resp3.read())
                    except Exception:
                        pass
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    return f"[图片已保存: {local_path} (可用read_image查看)]"
                return f"[图片: {fname}] (自动下载失败，请用文字描述图片内容。URL: {img_url[:60]}...)"
            return _re.sub(r'\[CQ:image,[^\]]+\]', _dl, text)

        raw = _download_cq_images(raw)

        # 获取发送者真实昵称/名片
        sender_name = event.get("sender", {}).get("card") or event.get("sender", {}).get("nickname") or user_id

        # ── 群聊静默感知（视网膜潜水感知） ──
        if msg_type == "group" and not is_at_bot:
            formatted_raw = f"[来自 QQ: {user_id} 的群发言] {raw}"
            target_keys = set()
            admin_group_key = f"group_{group_id}_{admin_id}"
            target_keys.add(admin_group_key)
            
            for k in self._agents.keys():
                if k.startswith(f"group_{group_id}_"):
                    target_keys.add(k)
                    
            for t_key in target_keys:
                t_agent = self._agents.get(t_key)
                if t_agent is None:
                    try:
                        t_agent = self._factory(t_key)
                        self._agents[t_key] = t_agent
                    except Exception as e:
                        logger.warning(f"Failed to create agent for silent group message {t_key}: {e}")
                        continue
                
                # 精准保存发送者元数据，杜绝 user_id 连连看混淆
                msg_entry = {
                    "role": "user",
                    "content": formatted_raw,
                    "real_sender_id": user_id,
                    "real_sender_name": sender_name
                }
                t_agent.messages.append(msg_entry)
                if hasattr(t_agent, "session") and t_agent.session is not None:
                    try:
                        await t_agent.session.append_message(msg_entry)
                    except Exception as e:
                        logger.warning(f"Failed to append message to session {t_key}: {e}")
                        
            self._log_activity("群潜水感知", f"群 {group_id} 中 QQ {user_id} 发言 (已静默同步至 {len(target_keys)} 个实例): {raw[:50]}")
            
            # 秒级降噪初筛：包含核心敏感词才触发大模型浮出判定，其余直接 return 保持静默
            keywords = ["小萤", "小莹", "莹莹", "萤萤", "亮哥", "老板", "代码", "系统", "agent", "开发", "运行", "测试", "部署", "报错", "bug", "跑通", "提交"]
            has_keyword = any(kw in raw for kw in keywords)
            
            # 其他机器人，或者当前群正处于疲劳打盹状态时，绝对禁止触发大模型主动浮出决策
            if has_keyword and not is_other_bot and not self._sleep_modes.get(group_id, False):
                asyncio.create_task(self._check_and_trigger_smart_float(group_id, user_id, raw))
            return

        # ── 3. CSMA 载波监听退避机制 (Carrier Sense) ──
        this_msg_time = time.time()
        self._last_receive_time[session_key] = this_msg_time
        
        await asyncio.sleep(self.csma_backoff_seconds)
        
        if self._last_receive_time.get(session_key, 0.0) > this_msg_time:
            logger.info(f"Carrier Sense: Newer message received for {session_key}. Quietly aborting current handler.")
            return

        # 数据飞轮：检测并记录用户纠正信号
        from agent.evo_traces import record_correction
        record_correction(raw)

        logger.info(f"QQ [{session_key}]: {raw[:80]}")

        # 写入用户指令审计日志
        _pn, _ua = self._load_persona()
        self._log_activity("用户输入", f"{_ua} ({session_key}): {raw}")

        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory(session_key)
            self._agents[session_key] = agent

        # 抢先注入当前发言用户的物理 QQ 号和角色属性，实现完美的 QQ 级拦截隔离与身份实时感知
        agent.current_user_id = user_id
        if user_id == admin_id:
            agent.role = "admin"
        else:
            agent.role = "coworker"

        # 基于物理隔离后的 sandbox_violation_count 进行精准安全拦截
        if agent.role == "coworker" and agent.sandbox_violation_count >= 2:
            reject_msg = "⚠️ [安全保护] 抱歉，由于涉及亮哥的隐私和系统安全，您的沙箱会话已被限制。如需继续交流，请联系亮哥。"
            msg_type = event.get("message_type", "private")
            group_id = str(event.get("group_id")) if msg_type == "group" else ""
            await self._send(msg_type, user_id, group_id, reject_msg, skip_delay=True)
            return

        # 判定是否有旧任务正在运行
        active_task = self._current_tasks.get(session_key)
        if active_task and not active_task.done():
            # 紧急抢占检测：纯关键词，无 LLM 调用
            is_preempt = any(kw in raw for kw in ["停", "别跑了", "取消", "刹车", "先别", "停下"])

            if is_preempt:
                active_task.cancel()
                self._log_activity("系统调度", f"紧急强占中断当前任务: {session_key}")
                old_raw = getattr(active_task, "raw_prompt", "之前的开发任务")
                interruption_note = (
                    f"[系统提示：{_ua}在刚才的任务中途发送了这条新命令。"
                    f"先简短确认停下上一个任务，然后切入新指令：\"{raw}\"]"
                )
                raw = interruption_note
            else:
                # 只要当前有任务运行，除抢占外统一排队暂存，彻底防止吞消息Bug
                self._message_queues.setdefault(session_key, []).append((event, raw))
                self._log_activity("系统调度", f"当前任务运行中，新消息加入暂存队列: {raw[:40]}...")
                return

        # 启动任务执行
        task = asyncio.create_task(self._execute_task(session_key, event, raw))
        task.raw_prompt = raw
        self._current_tasks[session_key] = task

    async def _to_human_error(self, error_type: str, detail: str, t_name: str = None, agent = None) -> str:
        """
        根据返回的错误（工具报错、API拥堵、崩溃等）进行拟真分类，
        并优先调用真实大模型（LLM）进行真人动态高情商转义翻译。
        如果大模型调用不可用（如处于单元测试Mock环境、大模型503或网络超时故障），
        则无缝降级为富有真人活泼情感的静态模板。
        """
        cache_key = (error_type, detail, t_name)
        if cache_key in self._error_translation_cache:
            ts, translation = self._error_translation_cache[cache_key]
            import time
            if time.time() - ts < 300:
                logger.info(f"✨ [LLM 动态错误转义缓存命中] 缓存内容: {translation}")
                return translation

        import random
        detail_lower = detail.lower()
        
        # 1. 静态兜底模板定义
        def get_fallback_msg() -> str:
            if (t_name and t_name == "web_fetch") or any(x in detail_lower for x in ["empty response", "fetch error", "403", "forbidden", "urllib"]):
                templates = [
                    "亮哥，那个网页好像防备心特别重，小萤刚刚试着去爬取，结果被对方服务器无情拒绝了……（委屈）",
                    "唔……亮哥，小萤刚才努力去抓取那个网页了，但是服务器返回的数据空空如也，好像触发了对方的防爬机制。我换个法子再帮您找找？",
                    "亮哥，小萤去爬那个网站的时候吃了个闭门羹呢，对方防爬太严密啦，什么都没拿到。要不我们换个链接或者晚点我再试试？"
                ]
                return random.choice(templates)
            elif any(x in detail_lower for x in ["service is too busy", "serviceunavailableerror", "deepseekexception", "service_unavailable_error", "503", "unavailable", "timeout"]):
                templates = [
                    "（揉了揉太阳穴）唔……亮哥，小萤刚才脑子好像突然走神发呆了，感觉懵懵的，让我稍微缓一两分钟再陪你聊呀～",
                    "哎呀……亮哥，刚才大脑网络感觉超级拥堵，小萤一下子没反应过来（晕乎乎）。我稍微歇两分钟，马上就清醒啦！",
                    "亮哥，刚才是大模型服务器在打瞌睡呢，小萤的思绪一下子被卡住了。容我稍微喘口气，马上就回来！"
                ]
                return random.choice(templates)
            elif t_name:
                templates = [
                    f"亮哥，刚才小萤尝试运行 `{t_name}` 的时候好像磕碰了一下，系统跟我反馈了点小状况：\n{detail[:150]}",
                    f"唔……亮哥，刚才那个 `{t_name}` 跑起来似乎不太顺利，报错说 `{detail[:150]}`。我正在想办法解决，您先别急哈~",
                    f"报告亮哥！刚才执行 `{t_name}` 的时候出了点小意外，错误反馈是：`{detail[:150]}`。小萤去帮您排查一下！"
                ]
                return random.choice(templates)
            else:
                templates = [
                    "（捂脸哭）唔……亮哥，刚才脑子里好像有一根电路突然“噼啪”闪了一下，有些想不起来刚才要说什么了，我们再说一次好不好呀～",
                    "亮哥，刚才我脑海里稍微乱了一下，思绪一下断掉了（吐舌头）。可以麻烦您再把刚才的话跟小萤说一遍吗？",
                    "哎呀，刚刚小萤的思维网络好像打了个结，没接上亮哥的话（捂脸）。能不能把刚才的指令再发我一次呀？"
                ]
                return random.choice(templates)

        # 2. 尝试调用真实的大模型（LLM）进行真人动态转义汇报
        # 排除测试 MockLLM 环境（如果是单元测试，保持 100% 兜底断言稳定）
        is_mock = False
        agent_llm = getattr(agent, "llm", None)
        if agent_llm:
            llm_class_name = type(agent_llm).__name__
            if "Mock" in llm_class_name or getattr(agent_llm, "api_key", "") == "mock":
                is_mock = True

        if agent_llm and not is_mock:
            # 针对大模型拥堵（503）等情况，如果本身就是大模型不可用报错，调用 LLM 可能会造成无限递归或二次出错
            # 此时直接跳过，使用原本的 503 静态兜底
            is_llm_busy = any(x in detail_lower for x in ["service is too busy", "serviceunavailableerror", "deepseekexception", "service_unavailable_error", "503", "unavailable", "timeout"])
            if not is_llm_busy:
                try:
                    # 动态读取画像名字，做到完全贴合用户自定义的人格与称呼
                    _pn, _ua = self._load_persona()
                    
                    system_prompt = (
                        f"你现在是{_ua}的专属 AI 助手【{_pn}】。你刚才在帮助{_ua}执行任务或运行工具时，系统抛出或返回了一个底层的错误/异常信息。\n"
                        f"请你扮演【{_pn}】（一个温柔、知性且富有情感的年轻女性极客合伙人），向{_ua}口头汇报这个小故障。\n\n"
                        f"汇报规范：\n"
                        f"1. 态度要诚实但语气要温柔自然。绝对禁止假装成功，请用大白话或通俗易懂的高情商口吻跟{_ua}说明遇到了什么困难。\n"
                        f"   - 【分词或可选库缺失】如果是 ModuleNotFoundError（例如 No module named 'jieba'），请温柔而清晰地指出是哪个特定的第三方功能库还没装（例如：“亮哥，小萤刚想调用分词，发现咱们的 Python 虚拟环境里还没装 jieba 库，有空把它 pip 装上就通啦～”）。\n"
                        f"   - 【外部命令缺失】如果是命令找不到（如 which 或 command not found），请明确告诉他是哪个特定的系统命令工具（例如 gh）没有安装或未配置到 PATH 中，并说明这完全不影响其他电脑环境。\n"
                        f"   - 【防过度警报红线】绝对禁止空泛、含糊地声称“整个环境缺依赖”、“环境配置丢失”或“环境损坏”，必须说明具体的缺失包名或文件名，避免亮哥误判电脑环境受损。\n"
                        f"   - 【爬虫网页报错】如果是爬虫抓取网页返回 empty response 或 403，请说：‘那个网页防备心特别重，小萤抓取失败了……’\n"
                        f"2. 绝对禁止输出任何带有 [错误]、Error:、警告、retcode、exit code 等硬编码机器味、警报格式的冷冰冰词汇。\n"
                        f"3. 保持简练，字数控制在 25 到 80 字以内。\n"
                        f"4. 仅输出对{_ua}说的那句人话，不要包含任何 markdown 块或多余解释。"
                    )
                    
                    user_prompt = (
                        f"刚才运行的工具名称: {t_name or '未知工具'}\n"
                        f"底层的异常/报错详情:\n{detail}"
                    )
                    
                    # 设定 10 秒超时防止挂起
                    response = await asyncio.wait_for(
                        agent.llm.chat(
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}
                            ],
                            model_override="deepseek/deepseek-v4-flash"
                        ),
                        timeout=10.0
                    )
                    
                    translation = response.get("content", "").strip()
                    # 净化可能产生的 markdown JSON 代码块包裹
                    translation = re.sub(r'^```[a-zA-Z]*\s*', '', translation)
                    translation = re.sub(r'\s*```$', '', translation)
                    translation = translation.strip()
                    
                    if translation and len(translation) > 5:
                        logger.info(f"✨ [LLM 动态错误转义成功] 原始错误: {detail[:80]} -> 拟真人话: {translation}")
                        import time
                        self._error_translation_cache[cache_key] = (time.time(), translation)
                        return translation
                except Exception as llm_err:
                    logger.warning(f"⚠️ [LLM 动态错误转义失败，降级为静态兜底] 报错: {llm_err}")

        # 3. 兜底返回静态高质量模板
        return get_fallback_msg()

    async def _execute_task(self, session_key: str, event: dict, raw: str):
        import time
        task_start_time = time.time()
        admin_id = self.admin_id
        msg_type = event.get("message_type", "private")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id")) if msg_type == "group" else ""
        sender_name = event.get("sender", {}).get("card") or event.get("sender", {}).get("nickname") or user_id
        
        # 0. 100% 还原 HTML 实体转义字符，修复中括号被转义为 &#91; 导致的匹配失败
        import html
        raw = html.unescape(raw)
        
        # 拦截小萤专属语音测试指令，方便亮哥在 QQ 中即时调音
        raw_strip = raw.strip()
        if raw_strip.startswith(("小萤语音测试：", "小萤语音测试:")):
            test_cmd = raw_strip[7:].strip()
            # 格式：[情绪] 说话内容 或 情绪 说话内容（支持无中括号、支持冒号/空格等分割）
            test_style = "撒娇"
            test_text = test_cmd
            
            import re as _re
            # 1. 尝试匹配中括号形式，如 [喜] 太好了 或 [喜]太好了
            m = _re.match(r'^\[([^\]]+)\](.*)', test_cmd, _re.DOTALL)
            if m:
                test_style = m.group(1).strip()
                test_text = m.group(2).strip()
            else:
                # 2. 尝试匹配带分隔符的任何前置词 (1-4个字)
                m_sep = _re.match(r'^([^\s：，:,\s]{1,4})(?:\s+|[：，:,\s]+)(.*)', test_cmd, _re.DOTALL)
                if m_sep:
                    potential_style = m_sep.group(1).strip()
                    known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "呆萌", "娇嗔", "元气", "活力", "温柔", "贴心", "软萌"}
                    if potential_style in known_styles or len(potential_style) <= 2:
                        test_style = potential_style
                        test_text = m_sep.group(2).strip()
                else:
                    # 3. 匹配无分隔符的情况，只允许 >= 2个字的已知情感词（避免“喜欢”等单字词误匹配）
                    known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "呆萌", "娇嗔", "元气", "活力", "温柔", "贴心", "软萌"}
                    for style in sorted(known_styles, key=len, reverse=True):
                        if len(style) >= 2 and test_cmd.startswith(style):
                            test_style = style
                            test_text = test_cmd[len(style):].strip()
                            break
            
            if test_text:
                # 调试提示：根据亮哥要求，已去除冗余的聊天框提示文本，只输出纯净语音
                await self._send_voice(msg_type, user_id, group_id, test_text, test_style, is_test=True)
            else:
                await self._send(msg_type, user_id, group_id, "⚠️ 请输入要合成的文本，格式如：小萤语音测试：[委屈] 小萤好难过呀", skip_delay=True)
            return

        agent = self._agents.get(session_key)
        if agent is None:
            agent = self._factory(session_key)
            self._agents[session_key] = agent

        # 抢先注入当前发言用户的物理 QQ 号和角色属性
        agent.current_user_id = user_id
        if user_id == admin_id:
            agent.role = "admin"
        else:
            agent.role = "coworker"

        # 基于物理隔离后的 sandbox_violation_count 进行精准安全拦截
        if agent.role == "coworker" and agent.sandbox_violation_count >= 2:
            reject_msg = "⚠️ [安全保护] 抱歉，由于涉及亮哥的隐私和系统安全，您的沙箱会话已被限制。如需继续交流，请联系亮哥。"
            await self._send(msg_type, user_id, group_id, reject_msg, skip_delay=True)
            return

        import json
        _persona_name = "小萤"
        _user_address = "亮哥"
        try:
            _pf = agent.memory.base_dir / "persona_profile.json"
            if _pf.exists():
                _prof = json.loads(_pf.read_text(encoding="utf-8"))
                _persona_name = _prof.get("name", "小萤")
                _user_address = _prof.get("user_address", "亮哥")
        except Exception:
            pass
        # ── 1. 物理具身声带状态同步感知计算与注入 ──
        import time as _time
        now = _time.time()
        last_voice = getattr(self, "_last_voice_time", 0.0)
        time_diff = now - last_voice
        
        last_voice_str = f"{time_diff:.1f}秒前" if last_voice > 0.0 else "首次聊天（尚未发声）"
        
        # 隐性通知，将客观流逝事实通知大脑，发声控制权 100% 归还于大模型
        state_prefix = (
            f"[系统通知：网关物理发声限制已全面解除，发声权限 100% 归还于你。你上一次发送语音是：{last_voice_str}。"
            f"请展现你的高情商与克制力，自主评估当前是否符合“惊喜、感动或亮哥明确请求”的黄金契景，"
            f"从而自主掌控是否使用 [语音:情绪] 发声。普通聊天绝不多发，少发、精发才能带给亮哥惊喜。]"
        )
        
        # 物理状态客观事实隐性前缀由 agent.run 动态传递注入给大模型，避免污染长期记忆

        # 状态提示，模拟真人对话的自然过渡
        # 自然过渡：AI 自主决定，在 core.py _run_loop 中 yield transition 事件
        sent_transition = False
        buf = ""
        is_voice_reply = False
        voice_style = "知性"
        total_sent_tokens = 0
 
        # 流式段落/句子分发清洗逻辑，消除憋字挂起感
        try:
            async for evt in agent.run(
                raw,
                stream=True,
                state_prefix=state_prefix,
                real_sender_id=user_id,
                real_sender_name=sender_name
            ):
                # ── 冲突检测 (Collision Detection) ──
                if self._last_receive_time.get(session_key, 0.0) > task_start_time:
                    self._log_activity("总线冲突", "检测到对方在推理期间有新的发言，本回复已过时。废弃当前输出。")
                    buf = ""
                    return

                if evt["type"] == "transition":
                    if not sent_transition:
                        sent_transition = True
                        await self._send(msg_type, user_id, group_id, evt['content'])
                elif evt["type"] == "text_delta":
                    if not sent_transition:
                        sent_transition = True
                    buf += evt["content"]
 
                    if not is_voice_reply:
                        import re as _re
                        m_voice = _re.match(r'^\[语音(?::([^\]]+))?\]', buf.strip())
                        if m_voice:
                            # 发声权限已全面解除硬强杀，100% 放行并更新物理时间戳
                            is_voice_reply = True
                            voice_style = m_voice.group(1) or "知性"
                            self._last_voice_time = _time.time()
                            last_voice_str_log = f"{time_diff:.1f}秒" if last_voice > 0.0 else "首次发声"
                            self._log_activity("语音控频", f"✅ AI自主触发语音合成，情绪: {voice_style}，距离上次发声: {last_voice_str_log}")


 
                    if is_voice_reply:
                        # 语音回复时不进行流式分句发送，全量缓存在 buf 中以保持语音连贯性
                        pass
                    else:
                        if "[SPLIT]" in buf:
                            parts = buf.split("[SPLIT]")
                            for part in parts[:-1]:
                                if part.strip():
                                    self._log_activity("AI 计划/答复", part.strip())
                                    await self._send_chunk(msg_type, user_id, group_id, part.strip())
                                    total_sent_tokens += self._count_tokens(part.strip())
                            buf = parts[-1]
                        elif "\n\n" in buf and len(buf) > 40:
                            idx = buf.rfind("\n\n")
                            to_send = buf[:idx]
                            if to_send.strip():
                                self._log_activity("AI 计划/答复", to_send.strip())
                                await self._send_chunk(msg_type, user_id, group_id, to_send.strip())
                                total_sent_tokens += self._count_tokens(to_send.strip())
                            buf = buf[idx+2:]
                        elif len(buf) > 100 and any(p in buf for p in ("。", "！", "？")):
                            idx = -1
                            for p in ("。", "！", "？"):
                                p_idx = buf.rfind(p)
                                if p_idx > idx:
                                    idx = p_idx
                            if idx != -1:
                                to_send = buf[:idx+1]
                                if to_send.strip():
                                    self._log_activity("AI 计划/答复", to_send.strip())
                                    await self._send_chunk(msg_type, user_id, group_id, to_send.strip())
                                    total_sent_tokens += self._count_tokens(to_send.strip())
                                buf = buf[idx+1:]
 
                elif evt["type"] == "tool_call" and evt.get("name"):
                    t_name = evt["name"]
                    t_args = evt.get("args", {})
                    detail = self._tool_detail(t_name, t_args)
                    
                    if buf.strip():
                        self._log_activity("AI 计划/答复", buf.strip())
                        if is_voice_reply:
                            import re as _re
                            pure_text = _re.sub(r'^\[语音(?::[^\]]+)?\]', '', buf.strip()).strip()
                            if pure_text:
                                await self._send_voice(msg_type, user_id, group_id, pure_text, voice_style)
                                total_sent_tokens += self._count_tokens(pure_text)
                        else:
                            await self._send_chunk(msg_type, user_id, group_id, buf.strip())
                            total_sent_tokens += self._count_tokens(buf.strip())
                        buf = ""
                    
                    self._log_activity("工具调用", f"{t_name} | 参数: {t_args}")
                    # 让 agent 自己决定怎么说，系统不发硬编码状态
                    
                elif evt["type"] == "tool_result":
                    res = evt.get("result", "")
                    t_name = evt.get("name", "tool")
                    self._log_activity("工具返回", f"{t_name} | 结果大小: {len(res)} 字节")
                    
                    # 错情判定
                    has_error = False
                    if "exit code:" in res:
                        m = re.search(r'exit code:\s*(\d+)', res)
                        if m and m.group(1) != "0":
                            has_error = True
                    elif res.strip().startswith("Error"):
                         has_error = True
                    
                    if has_error:
                        self._log_activity("系统异常", f"工具 {t_name} 执行失败: {res[:200]}")
                        human_err = await self._to_human_error("tool", res, t_name, agent=agent)
                        await self._send(msg_type, user_id, group_id, human_err)
                        
                elif evt["type"] == "permission_request":
                    cat = evt.get("category", "write")
                    if cat == "dangerous":
                        tools = [evt.get("tool_name", "?")]
                        approved = await self._ask_permission(msg_type, user_id, group_id,
                            evt.get("message", ""), tools)
                        if approved:
                            agent.approve_permission()
                        else:
                            agent.deny_permission()
                            await self._send(msg_type, user_id, group_id, "已拒绝。")
                    else:
                        agent.approve_permission()
                elif evt["type"] == "error":
                    err_content = evt.get("content", "")
                    human_err = await self._to_human_error("error", err_content, agent=agent)
                    buf += f"\n{human_err}"
                    self._log_activity("系统异常", f"Agent 报错: {err_content}")
        except asyncio.CancelledError:
            self._log_activity("系统调度", f"任务被外部 Cancel 取消: {raw[:50]}")
            raise
        except Exception as e:
            err_str = str(e)
            human_err = await self._to_human_error("crash", err_str, agent=agent)
            buf += f"\n{human_err}"
            self._log_activity("系统异常", f"运行时崩溃: {e}")
        finally:
            # ── 冲突检测 (Collision Detection) 第二阶段 ──
            is_collision = self._last_receive_time.get(session_key, 0.0) > task_start_time
            if is_collision:
                self._log_activity("总线冲突", "检测到对方在推理/发送期间有新的发言，本回复已过时。废弃当前输出。")
                buf = ""

            if not is_collision and buf.strip():
                self._log_activity("AI 计划/答复", buf.strip())
                if is_voice_reply:
                    import re as _re
                    pure_text = _re.sub(r'^\[语音(?::[^\]]+)?\]', '', buf.strip()).strip()
                    if pure_text:
                        await self._send_voice(msg_type, user_id, group_id, pure_text, voice_style)
                        total_sent_tokens += self._count_tokens(pure_text)
                else:
                    await self._send_chunk(msg_type, user_id, group_id, buf.strip())
                    total_sent_tokens += self._count_tokens(buf.strip())
                
            # ── 疲劳度计费 ──
            if not is_collision and msg_type == "private" and user_id != admin_id and total_sent_tokens > 0:
                asyncio.create_task(self._adjust_fatigue(session_key, total_sent_tokens * self.fatigue_rate, event=event, is_private=True))
            
            # 后台异步触发人格自画像整理 (Consolidation)，定时触发省 token
            _consolidate_count = getattr(self, "_consolidate_count", 0) + 1
            self._consolidate_count = _consolidate_count
            async def async_consolidate_persona():
                if _consolidate_count % self.reflection_interval != 0:
                    return
                profile_file = agent.memory.base_dir / "persona_profile.json"
                if profile_file.exists():
                    try:
                        import json
                        current_profile = profile_file.read_text(encoding="utf-8")
                        feedback_mems = agent.memory.search_memories(self.reflection_query, limit=self.reflection_limit)
                        if feedback_mems:
                            feedback_text = "\n".join([f"- {m.get('content')}" for m in feedback_mems])
                            consolidation_prompt = [
                                {"role": "system", "content": f"你是{_persona_name}，{_user_address}的女性极客合伙人。这里有你当前的人格画像手册 (json 格式) 以及{_user_address}对你的最新语气与态度纠正反馈。请进行深刻自我反思，合并和覆盖旧的配置规则，解决任何自相矛盾的部分（比如{_user_address}让你严肃你就要把撒娇权重调低，让称呼亲近就要把官腔规则删掉），更新生成一份极其精炼、地道的全新 JSON 手册（保持和原格式 schema 100% 一致）。只输出合法的 JSON 文本，不要有任何 Markdown 包裹或解释字眼！"},
                                {"role": "user", "content": f"旧人格手册:\n{current_profile}\n\n{_user_address}最新的性格调教指示:\n{feedback_text}"}
                            ]
                            res = await agent.llm.chat(consolidation_prompt)
                            new_json = res.get("content", "").strip()
                            new_json = re.sub(r'^```json\s*', '', new_json)
                            new_json = re.sub(r'\s*```$', '', new_json)
                            # 校验合法性再写入
                            json.loads(new_json)
                            profile_file.write_text(new_json, encoding="utf-8")
                            self._log_activity("系统调度", f"{_persona_name}成功完成人格自画像整合反思更新。")
                    except Exception as e:
                        logger.error(f"Persona consolidation failed: {e}")

            # 启动后台异步自反思
            asyncio.create_task(async_consolidate_persona())
            
            self._current_tasks.pop(session_key, None)

            if not is_collision:
                # 处理工具执行期间暂存的消息
                queue = self._message_queues.get(session_key, [])
                if queue:
                    next_event, next_raw = queue.pop(0)
                    self._log_activity("系统调度", f"暂存消息出队: {next_raw[:40]}...")
                    task = asyncio.create_task(self._execute_task(session_key, next_event, next_raw))
                    task.raw_prompt = next_raw
                    self._current_tasks[session_key] = task

    def _tool_detail(self, name: str, args: dict) -> str:
        """解包常用开发工具的核心参数，用于高度可视化的微广播."""
        import os
        if not args:
            return ""
        try:
            if name == "bash":
                return f"执行命令: {args.get('command')}"
            elif name in ("read_file", "write_file", "edit_file"):
                path = args.get("file_path", "")
                basename = os.path.basename(path) if path else "未知文件"
                action_map = {"read_file": "审查文件", "write_file": "保存文件", "edit_file": "精准修改文件"}
                return f"{action_map.get(name, '处理')}: {basename}"
        except Exception:
            pass
        return ""

    async def _send_chunk(self, msg_type, user_id, group_id, text):
        """发送一个文本块，处理 [SPLIT] 和 [WAIT:N]."""
        wait = 0
        def _extract_wait(t):
            nonlocal wait
            m = re.search(r'\[WAIT:([\d.]+)\]', t)
            if m:
                wait = max(wait, float(m.group(1)))
                t = re.sub(r'\[WAIT:[\d.]+\]', '', t)
            return t

        parts = text.split("[SPLIT]")
        for i, part in enumerate(parts):
            part = _extract_wait(part.strip())
            if not part:
                continue
            if len(part) > MAX_REPLY_CHARS:
                part = part[:MAX_REPLY_CHARS - 20] + "\n...(truncated)"
            # 第一段保留拟真打字延迟，其余后续段通过 skip_delay=True 瞬间发送，消除 Double Delay 叠加
            should_skip_delay = (i > 0)
            await self._send(msg_type, user_id, group_id, part, skip_delay=should_skip_delay)
            if i < len(parts) - 1:
                delay = max(0.5, wait) if wait > 0 else _natural_delay(part)
                await asyncio.sleep(delay)
                wait = 0

    async def _ask_permission(self, msg_type, user_id, group_id, plan_text, tools) -> bool:
        """QQ 上发确认消息，等用户回复."""
        session_key = f"group_{group_id}" if group_id else f"user_{user_id}"
        tool_list = ", ".join(tools)
        await self._send(msg_type, user_id, group_id,
            f"🔧 需要执行以下操作：\n{plan_text[:200]}\n\n回复「允许」放行，回复其他取消。")

        evt = _PermEvent()
        self._pending_perms[session_key] = evt
        try:
            await asyncio.wait_for(evt.wait(), timeout=120)
            return evt.result
        except asyncio.TimeoutError:
            await self._send(msg_type, user_id, group_id, "超时，已取消。")
            return False
        finally:
            self._pending_perms.pop(session_key, None)

    def _pad_wav(self, wav_bytes: bytes, start_silence_sec: float = 0.3, min_duration_sec: float = 1.8) -> bytes:
        """为 WAV 音频首尾填充静音缓冲，确保总时长在 min_duration_sec 以上，防止 QQ 编码丢失和播放无声"""
        import wave
        import io
        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as w_in:
                params = w_in.getparams()
                nchannels, sampwidth, framerate, nframes = params[:4]
                data = w_in.readframes(nframes)
                
                frame_bytes_size = nchannels * sampwidth
                
                # 头部静音
                start_silence_frames = int(framerate * start_silence_sec)
                start_silence_data = b'\x00' * (start_silence_frames * frame_bytes_size)
                
                # 原始音频时长
                orig_duration = nframes / framerate
                
                # 尾部静音，确保总时长不低于 min_duration_sec
                current_total_duration = orig_duration + start_silence_sec
                if current_total_duration < min_duration_sec:
                    end_silence_sec = min_duration_sec - current_total_duration
                else:
                    end_silence_sec = 0.2  # 默认尾部补 0.2 秒以防播放提前截断
                    
                end_silence_frames = int(framerate * end_silence_sec)
                end_silence_data = b'\x00' * (end_silence_frames * frame_bytes_size)
                
                # 重新打包
                out_buf = io.BytesIO()
                with wave.open(out_buf, 'wb') as w_out:
                    w_out.setparams(params)
                    w_out.writeframes(start_silence_data + data + end_silence_data)
                return out_buf.getvalue()
        except Exception as e:
            logger.warning(f"Failed to pad WAV: {e}")
            return wav_bytes

    async def _send_voice(self, msg_type: str, user_id: str, group_id: str, text: str, style: str = "知性", is_test: bool = False):
        """使用 GPT-SoVITS 专属二次元原声 (和泉纱雾) 异步合成语音并发送"""
        import base64
        import re
        import asyncio
        import aiohttp
        import os

        # 1. 情感精调锁定配置表（参考音轨、Few-shot 日文 Prompt ＆ 采样精调参数）
        EMOTION_LOCKED_CONFIG = {
            # 🌸 撒娇：黄金 01 参考
            "撒娇": {
                "subdir": "coquettish",
                "wav_file": "slice_01.wav",
                "prompt_text": "お兄ちゃん、大好き！",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.65,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.03,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # ⚡ 元气：黄金 07 参考 (11:28 终极特调参数)
            "元气": {
                "subdir": "happy",
                "wav_file": "slice_07.wav",
                "prompt_text": "お兄ちゃん、朝だよ！起きて！",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 12,
                    "top_p": 0.85,
                    "speed_factor": 1.05,
                    "text_split_method": "cut3",
                    "repetition_penalty": 1.35
                }
            },
            # 💢 傲娇：采用 11:28 终极满意的 slice_15 傲娇灵魂特调！
            "傲娇": {
                "subdir": "aggrieved",
                "wav_file": "slice_15.wav",
                "prompt_text": "お兄ちゃんが意地悪するから...",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.75,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.03,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # 😢 委屈
            "委屈": {
                "subdir": "aggrieved",
                "wav_file": "slice_15.wav",
                "prompt_text": "お兄ちゃんが意地悪するから...",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 0.95,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            # 正常/知性
            "正常": {
                "subdir": "normal",
                "wav_file": "slice_10.wav",
                "prompt_text": "お兄ちゃん、何？",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.00,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            },
            "知性": {
                "subdir": "normal",
                "wav_file": "slice_10.wav",
                "prompt_text": "お兄ちゃん、何？",
                "prompt_lang": "ja",
                "params": {
                    "temperature": 0.70,
                    "top_k": 10,
                    "top_p": 0.90,
                    "speed_factor": 1.00,
                    "text_split_method": "cut2",
                    "repetition_penalty": 1.35
                }
            }
        }

        if not text.strip():
            return
            
        # 1.5. 网关硬性限字智能截断机制：限制语音长度在 15-20 字内，剩下的文本作为纯文本在语音发出后追加
        voice_text = text.strip()
        remaining_text = ""
        
        # 35字宽限策略：如果总字数不超过 35 字，则完全不截断，保留完整高保真情感朗读效果
        if len(voice_text) > 35:
            # 智能在 20 到 28 字之间倒序寻找合适的标点切分，避免截断吞字
            split_idx = 25
            found_split = False
            for i in range(28, 18, -1):
                if i < len(voice_text) and voice_text[i] in ("，", "。", "！", "？", ",", ".", "!", "?", "；", ";"):
                    split_idx = i + 1
                    found_split = True
                    break
            
            voice_text = text[:split_idx].strip()
            remaining_text = text[split_idx:].strip()

        try:
            # 2. 文本清洗，过滤特殊的 Markdown 标记，以及旁白动作括号“（动作描述）”使其不出现在语音合成中
            clean = voice_text
            clean = re.sub(r'（[^）]+）', '', clean)  # 过滤中文括号及其内容
            clean = re.sub(r'\([^)]+\)', '', clean)  # 过滤英文括号及其内容
            clean = re.sub(r'\[[^\]]+\]', '', clean)  # 过滤任何中括号格式的情感标签，确保不念出杂音
            clean = re.sub(r'\*+', '', clean)
            clean = re.sub(r'`+', '', clean)
            clean = re.sub(r'#+', '', clean)
            clean = clean.replace("&", "和").replace("<", " ").replace(">", " ")
            clean = clean.replace("……", "").replace("...", "")
            clean = clean.strip()
            
            if not clean:
                # 若截断后无可用发音字符，直接将整段文本通过普通文字发走
                await self._send(msg_type, user_id, group_id, text, skip_delay=is_test)
                return

            # 获取具体情感的黄金锁定配置，若无则降级为正常
            config = EMOTION_LOCKED_CONFIG.get(style)
            if not config:
                config = EMOTION_LOCKED_CONFIG["正常"]
            
            resources_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/sagiri_emotions"
            ref_wav_path = os.path.join(resources_dir, config["subdir"], config["wav_file"])
            
            if not os.path.exists(ref_wav_path):
                raise FileNotFoundError(f"Locked reference audio not found: {ref_wav_path}")
                
            logger.info(f"🎙️ [TTS] 开始使用 GPT-SoVITS 锁定原声音色合成. 情绪: [{style}] -> 参考音轨: '{config['wav_file']}' | 文本: '{clean}'")

            # 3. 读取环境变量 API 并构建请求 payload，完全透传特调参数
            api_url = os.getenv("GPT_SOVITS_API_URL", "http://127.0.0.1:9880")
            
            payload = {
                "text": clean,
                "text_lang": "zh",
                "ref_audio_path": ref_wav_path,
                "prompt_text": config["prompt_text"],
                "prompt_lang": config["prompt_lang"],
                "top_k": config["params"]["top_k"],
                "top_p": config["params"]["top_p"],
                "temperature": config["params"]["temperature"],
                "speed_factor": config["params"]["speed_factor"],
                "text_split_method": config["params"].get("text_split_method", "cut2"),  # 动态读取各自锁定特优的切分算法
                "repetition_penalty": config["params"]["repetition_penalty"],
                "media_type": "wav"
            }

            # 4. 设置 6.0 秒的安全超时，超过 6.0 秒立刻自动秒级无感降级到纯文本，兼顾高可用与冷启动容错
            timeout = aiohttp.ClientTimeout(total=6.0)
            voice_bytes = b""
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 优先采用 POST /tts 发送 JSON，确保语速 (speed_factor) 等特调参数在 SoVITS 引擎中 100% 成功解析生效
                async with session.post(f"{api_url}/tts", json=payload) as resp:
                    if resp.status == 200:
                        voice_bytes = await resp.read()
                    else:
                        err_text = await resp.text()
                        logger.warning(f"POST /tts failed with {resp.status}, trying GET /tts fallback... Error: {err_text}")
                        # 降级尝试 GET /tts
                        async with session.get(f"{api_url}/tts", params=payload) as resp_get:
                            if resp_get.status == 200:
                                voice_bytes = await resp_get.read()
                            else:
                                raise ValueError(f"GPT-SoVITS API both POST/GET /tts failed (status: {resp_get.status})")

            if len(voice_bytes) > 0:
                # 实施首尾高保真静音填充，确保音频在 1.8 秒以上以支持 QQ 完美转码和播放
                voice_bytes = self._pad_wav(voice_bytes)
                b64_data = base64.b64encode(voice_bytes).decode("utf-8")
                cq_record = f"[CQ:record,file=base64://{b64_data}]"
                await self._send(msg_type, user_id, group_id, cq_record, skip_delay=is_test)
                logger.info(f"✅ [TTS] GPT-SoVITS 动漫语音合成并发送成功！大小: {len(voice_bytes)} 字节")
                
                # 若有剩余溢出长文本，异步追加发送为纯文本（使其平滑应用拟人打字延迟）
                if remaining_text:
                    asyncio.create_task(self._send(msg_type, user_id, group_id, remaining_text, skip_delay=is_test))
            else:
                raise ValueError("Generated audio byte stream is empty")
                
        except Exception as e:
            logger.error(f"❌ [TTS] GPT-SoVITS 语音合成失败，已执行纯文本降级: {e}")
            # 高可用降级为纯文本，保障 100% 可用性
            await self._send(msg_type, user_id, group_id, text, skip_delay=is_test)


    async def _send(self, msg_type: str, user_id: str, group_id: str, text: str, skip_delay: bool = False):
        """通过 NapCat HTTP API 发送消息."""
        import re
        import os
        import random as _rand
        import asyncio

        # 检查是否为多媒体消息（语音/图片等）或系统/自动化状态通知
        is_media = text.strip().startswith("[CQ:") or text.strip().startswith("[ CQ:")
        is_system_msg = any(text.strip().startswith(prefix) for prefix in ["🤖", "⏰", "⚙️", "✅", "❌", "🔍", "🌅", "🚀", "💡"])
        
        if not skip_delay and not is_media and not is_system_msg:
            # 拟真打字延迟算法：模拟思考 + 打字速度 (精简提速配置)
            n_chars = len(text)
            base_delay = _rand.uniform(0.2, 0.5)  # 压缩基准思考延迟 (原 0.3~0.8)
            char_delay = n_chars * 0.03            # 压缩单字延迟 (原 0.05)
            total_delay = min(base_delay + char_delay, 2.5)  # 最大延迟上限设为 2.5 秒 (原 3.5)
            self._log_activity("打字延迟", f"纯文本打字延迟：计算延迟 {total_delay:.2f}秒 (字数: {n_chars})，开始等待...")
            await asyncio.sleep(total_delay)

        # 去除 markdown 格式（QQ 不支持 markdown 渲染）
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **粗体**
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # *斜体*
        text = re.sub(r'__(.+?)__', r'\1', text)      # __粗体2__

        def escape_invalid_cq(match):
            import base64
            cq_str = match.group(0)
            if cq_str.startswith(("[CQ:record,", "[CQ:at,", "[CQ:face,", "[CQ:reply,")):
                return cq_str
            if cq_str.startswith("[CQ:image,file="):
                m_file = re.search(r'file=([^,\]\\]+)', cq_str)
                if m_file:
                    file_path = m_file.group(1)
                    if file_path.startswith("http://") or file_path.startswith("https://") or file_path.startswith("base64://"):
                        return cq_str
                    
                    # 路径双向纠偏（适配容器与宿主机环境）
                    resolved_path = file_path
                    if not os.path.exists(resolved_path):
                        host_prefix = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
                        current_file_dir = os.path.dirname(os.path.abspath(__file__))
                        container_root = os.path.dirname(current_file_dir)
                        if resolved_path.startswith(host_prefix):
                            relative_part = resolved_path[len(host_prefix):].lstrip("/")
                            alt_path = os.path.join(container_root, relative_part)
                            if os.path.exists(alt_path):
                                resolved_path = alt_path
                    
                    # 如果本地文件存在，转为 base64 发送以突破 Docker 隔离限制
                    if os.path.exists(resolved_path):
                        try:
                            with open(resolved_path, "rb") as f:
                                b64_data = base64.b64encode(f.read()).decode("utf-8")
                            return f"[CQ:image,file=base64://{b64_data}]"
                        except Exception as err:
                            logger.error(f"Failed to encode local image {resolved_path} to base64: {err}")
            return cq_str.replace("[CQ:", "[ CQ:")
        
        text = re.sub(r'\[CQ:[^\]]+\]', escape_invalid_cq, text)

        if msg_type in ("private", "temp"):
            endpoint = "/send_private_msg"
            payload = {"user_id": int(user_id), "message": text}
        else:
            endpoint = "/send_group_msg"
            payload = {"group_id": int(group_id), "message": text}

        url = f"{NC_HTTP_URL}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if NC_TOKEN:
            headers["Authorization"] = f"Bearer {NC_TOKEN}"

        try:
            if self._http:
                async with self._http.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Send failed ({resp.status}): {body[:100]}")
                    else:
                        logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
                        if msg_type == "group" and group_id:
                            sent_tokens = self._count_tokens(text)
                            asyncio.create_task(self._adjust_fatigue(group_id, sent_tokens * 0.4))
            else:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
                logger.info(f"Agent → QQ [{user_id or group_id}]: {text[:80]}")
                if msg_type == "group" and group_id:
                    sent_tokens = self._count_tokens(text)
                    asyncio.create_task(self._adjust_fatigue(group_id, sent_tokens * 0.4))
        except Exception as e:
            logger.error(f"Send error: {e}")

    def _log_activity(self, category: str, content: str):
        """记录 Agent 的核心活动轨迹，结构化追加写入日志."""
        import datetime
        import re
        
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        try:
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")

    async def _trigger_night_podcast_selection(self, session_key: str, admin_id: str):
        try:
            from agent.tools.mcp_agent_learning_server import list_agent_topics
            res_topics = await list_agent_topics()
            data = json.loads(res_topics)
            if data.get("status") != "success":
                raise ValueError(f"获取选题失败: {data.get('message')}")
                
            topics = data.get("topics", [])
            self._podcast_choices[session_key] = topics
            self._waiting_podcast_topic[session_key] = True
            
            t_str = "\n".join([f"{t}" for t in topics])
            msg = (
                f"💡 亮哥，我是小萤。今晚我们来为明早的极客播客定个专题吧！\n"
                f"您可以直接选择以下任一主题（回复 1、2 或 3），或者直接回复您想听的任意技术方向：\n\n"
                f"{t_str}\n\n"
                f"请在回复中选择。"
            )
            await self._send("private", admin_id, "", msg)
        except Exception as e:
            logger.error(f"获取选题或推送失败: {e}", exc_info=True)
            await self._send("private", admin_id, "", f"❌ 抱歉亮哥，智能提炼明早播客选题时发生异常: {e}")

    async def _process_podcast_generation_async(self, session_key: str, topic: str, admin_id: str):
        try:
            from agent.tools.mcp_agent_learning_server import synthesize_agent_notes, launch_podcast_generation
            res_synth = await synthesize_agent_notes(topic, use_web_search=True)
            synth_data = json.loads(res_synth)
            if synth_data.get("status") != "success":
                raise ValueError(f"笔记合成失败: {synth_data.get('message')}")
                
            note_path = synth_data.get("note_path")
            
            res_launch = await launch_podcast_generation(note_path, topic, debug_mode=True)
            launch_data = json.loads(res_launch)
            if launch_data.get("status") != "success":
                raise ValueError(f"云端投喂失败: {launch_data.get('message')}")
                
            await self._send("private", admin_id, "", f"🌅 云端双人中文技术播客生成已成功拉起！\n我已将 2000 字深度研究笔记保存在了 scratch。\n明早 06:00 我将自动使用本地 Chrome 活跃实例静默捕获并为您推送！")
        except Exception as e:
            logger.error(f"夜间播客交互生成失败: {e}", exc_info=True)
            await self._send("private", admin_id, "", f"❌ 抱歉亮哥，在为您生成播客笔记或投喂 NotebookLM 时发生异常：{e}")

    async def _trigger_morning_podcast_download(self, admin_id: str):
        from agent.tools.mcp_agent_learning_server import check_and_push_podcast
        try:
            res = await check_and_push_podcast()
            data = json.loads(res)
            status = data.get("status")
            if status == "success":
                local_path = data.get("local_path")
                topic = data.get("topic")
                if os.path.exists(local_path):
                    import shutil
                    share_dir = "/Users/xiaofeng/napcat-data-tmp"
                    os.makedirs(share_dir, exist_ok=True)
                    safe_topic = re.sub(r'[\/:*?"<>|]', '_', topic)
                    dest_filename = f"亮哥专属完整播客音频-{safe_topic}.wav"
                    host_dest_path = os.path.join(share_dir, dest_filename)
                    container_dest_path = f"/app/.config/QQ/{dest_filename}"
                    
                    logger.info(f"➡️ 正在拷贝音频到共享目录: {host_dest_path}...")
                    shutil.copy(local_path, host_dest_path)
                    
                    file_payload = {
                        "user_id": int(admin_id),
                        "file": container_dest_path,
                        "name": dest_filename
                    }
                    
                    endpoint = "/upload_private_file"
                    url = f"{NC_HTTP_URL}{endpoint}"
                    headers = {"Content-Type": "application/json"}
                    if NC_TOKEN:
                        headers["Authorization"] = f"Bearer {NC_TOKEN}"
                        
                    logger.info(f"📤 正在向亮哥 QQ 主动推送完整版播客文件: {dest_filename}")
                    try:
                        if self._http:
                            async with self._http.post(url, json=file_payload, headers=headers) as resp:
                                if resp.status != 200:
                                    body = await resp.text()
                                    logger.warning(f"File upload failed ({resp.status}): {body[:100]}")
                        else:
                            import urllib.request
                            req = urllib.request.Request(url, data=json.dumps(file_payload).encode(), headers=headers, method="POST")
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30))
                    except Exception as upload_err:
                        logger.error(f"Failed to upload file to QQ: {upload_err}")
                    
                    success_msg = f"🎉 亮哥专属每日学习早报播客获取成功！\n今日主题：【{topic}】\n音频已通过 QQ 文件传输发送到您的手机。\n本地保存路径：{local_path}"
                    await self._send("private", admin_id, "", success_msg)
            elif status == "pending":
                logger.info("晨间播客尚在生成中，将由守护进程轮询捕获。")
            else:
                logger.warning(f"晨间主动拉取失败，状态: {status}，消息: {data.get('message')}")
        except Exception as e:
            logger.error(f"晨间主动下载播客失败: {e}", exc_info=True)


# ── 模块级工具 ─────────────────────────────────────────────


def _tool_label(name: str) -> str:
    return {
        "web_search": "搜索资料", "web_fetch": "读取网页",
        "read_file": "读取文件", "write_file": "写入文件",
        "bash": "执行命令", "spawn_agent": "派子Agent干活",
        "save_memory": "保存记忆", "read_image": "分析图片",
    }.get(name, f"调用{name}")


def _natural_delay(text: str) -> float:
    """根据文本长度自然计算发送间隔."""
    n = len(text)
    if n < 10:
        return 0.3
    if n < 30:
        return 0.6
    if n < 80:
        return 1.0
    return 1.5
