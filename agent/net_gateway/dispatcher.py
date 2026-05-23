import os
import re
import time
import json
import html
import random
import asyncio
import logging
from .tts import parse_voice_test_command, send_voice
from .bus import CSMAController

logger = logging.getLogger("net_gateway.dispatcher")

class MessageDispatcher:
    """QQ 消息分发路由器，负责消息解析、白名单拦截、疲劳打盹状态机与 ReAct 调用"""
    
    def __init__(self, context, bot=None):
        self.context = context
        self.bot = bot
        self.bus = CSMAController(context)
        
        # 共享状态属性，与 QQGateway 桥接兼容，保证单元测试完美运行
        self._private_chat_paused = False
        self._fatigue_levels = {}        # group_id/session_key -> float
        self._sleep_modes = {}           # group_id/session_key -> bool
        self._active_sleep_tasks = {}    # group_id/session_key -> asyncio.Task
        self._waiting_podcast_topic = {} # session_key -> bool
        self._podcast_choices = {}       # session_key -> list[str]
        self._last_message_times = {}    # group_id/session_key -> float
        self._pending_perms = {}         # session_key -> _PermEvent
        self._non_white_cache = {}       # user_id -> last_reply_time
        
        # 配置从环境变量同步
        self.admin_id = self.context.admin_id
        self.fatigue_sleep_seconds = float(os.getenv("QQ_FATIGUE_SLEEP_MINUTES", "15.0")) * 60.0

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

        is_allowed = False
        if user_id in WHITE_LIST:
            is_allowed = True
        elif msg_type == "group" and group_id in WHITE_GROUPS:
            is_allowed = True

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
            await self._adjust_fatigue(group_id, inc, event)

        # ── 4. 安全拦截 ──
        if not is_allowed:
            if msg_type == "private" or (msg_type == "group" and is_triggered):
                now = time.monotonic()
                last_reply = self._non_white_cache.get(user_id, 0.0)
                
                if now - last_reply >= 300.0:  # 5分钟冷却
                    self._non_white_cache[user_id] = now
                    reject_msg = "抱歉，我是亮哥的专属 AI 助手小萤，目前仅对主人开放私聊与管理服务哦。"
                    if msg_type == "group":
                        await self.context.send_msg("group", "", group_id, f"[CQ:at,qq={user_id}] {reject_msg}")
                    else:
                        await self.context.send_msg("private", user_id, "", reject_msg)
                    logger.warning(f"🛡️ [安全拦截] 拦截非白名单 QQ 用户 [{user_id}] 消息: {raw[:50]}")
            return

        # ── 4. 主人专属控制与特权唤醒 ──
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
            if msg_type == "private":
                if raw == "暂停私聊":
                    self._private_chat_paused = True
                    await self.context.send_msg("private", self.admin_id, "", "[系统提示] 已物理暂停非主人私聊，小萤将保持静默。")
                    return
                elif raw == "恢复私聊":
                    self._private_chat_paused = False
                    await self.context.send_msg("private", self.admin_id, "", "[系统提示] 已恢复私聊服务，非主人私聊将重新恢复交互与疲劳累加。")
                    return

        # ── 5. 非主人私聊冷冻期与全局暂停拦截 ──
        if msg_type == "private" and user_id != self.admin_id:
            if self._private_chat_paused or self._sleep_modes.get(session_key, False):
                logger.info(f"Private chat paused/sleeping. Silently ignoring message from {user_id}: {raw[:50]}")
                return

        # ── 6. 播客选题拦截 ──────────────────────────────────────────
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
        """流式处理 Agent 推理输出，处理总线冲突、流式 [SPLIT] 块分发与拟真打字延迟"""
        # 1. 载波冲突避免挂起退避与冲突判定
        if await self.bus.wait_for_carrier_sense(session_key, task_start_time):
            return

        now = time.monotonic()
        last_voice = self.context._last_voice_time
        time_diff = now - last_voice
        last_voice_str = f"{time_diff:.1f}秒前" if last_voice > 0.0 else "首次聊天（尚未发声）"
        
        state_prefix = (
            f"[系统通知：网关物理发声限制已全面解除，发声权限 100% 归还于你。你上一次发送语音是：{last_voice_str}。"
            f"请展现你的高情商与克制力，自主评估当前是否符合“惊喜、感动或亮哥明确请求”的黄金契景，"
            f"从而自主掌控是否使用 [语音:情绪] 发声。普通聊天绝不多发，少发、精发才能带给亮哥惊喜。]"
        )

        sent_transition = False
        buf = ""
        is_voice_reply = False
        voice_style = "知性"
        total_sent_tokens = 0

        try:
            # 核心下沉：core.py 顶部会在 coworker 违规次数超限时 yield 包含安全警告的 error 并 return
            async for evt in agent.run(
                raw,
                stream=True,
                state_prefix=state_prefix,
                real_sender_id=user_id,
                real_sender_name=sender_name,
                group_id=group_id
            ):
                # ── 冲突检测 (Collision Detection) 第一阶段 ──
                if self.bus.is_collision(session_key, task_start_time):
                    buf = ""
                    return

                if evt["type"] == "exploring_start":
                    self._log_activity_dispatcher("AI 计划/答复", "思考启动...", user_id=user_id)

                elif evt["type"] == "transition":
                    self._log_activity_dispatcher("AI 计划/答复", evt['content'], user_id=user_id)
                    if not sent_transition:
                        sent_transition = True
                        await self.context.send_msg(msg_type, user_id, group_id, evt['content'])

                elif evt["type"] == "text_delta":
                    if not sent_transition:
                        sent_transition = True
                    buf += evt["content"]

                    if not is_voice_reply:
                        # 识别是否是语音发声前缀（比如 [语音:乐] 或者 [乐]）
                        style_match = re.match(r'^\[([^\s\]]+)\]', buf.strip())
                        if style_match:
                            candidate_style = style_match.group(1).strip()
                            # 自动支持并兼容 [语音:傲娇] 格式，清洗剥离提取核心情绪
                            if candidate_style.startswith("语音:") or candidate_style.startswith("语音："):
                                candidate_style = candidate_style.split(":", 1)[-1].split("：", 1)[-1].strip()
                                
                            known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "小脾气", "元气", "温柔", "知性", "正常"}
                            if candidate_style in reversed(sorted(known_styles, key=len)):
                                is_voice_reply = True
                                voice_style = candidate_style
                                self.context._last_voice_time = time.monotonic()
                                logger.info(f"✅ AI自主触发语音合成，情绪: {voice_style}")

                    if is_voice_reply:
                        # 语音回复时不进行流式分句发送，全量缓存在 buf 中以保持语音连贯性
                        pass
                    else:
                        # 文本流式分句分段发送，保证极速拟真微交互
                        if "[SPLIT]" in buf:
                            parts = buf.split("[SPLIT]")
                            for part in parts[:-1]:
                                if part.strip():
                                    self._log_activity_dispatcher("AI 计划/答复", part.strip(), user_id=user_id)
                                    await self.context.send_chunk(msg_type, user_id, group_id, part.strip())
                                    total_sent_tokens += self._count_tokens(part.strip())
                            buf = parts[-1]
                        elif "\n\n" in buf and len(buf) > 40:
                            idx = buf.rfind("\n\n")
                            to_send = buf[:idx]
                            if to_send.strip():
                                self._log_activity_dispatcher("AI 计划/答复", to_send.strip(), user_id=user_id)
                                await self.context.send_chunk(msg_type, user_id, group_id, to_send.strip())
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
                                    self._log_activity_dispatcher("AI 计划/答复", to_send.strip(), user_id=user_id)
                                    await self.context.send_chunk(msg_type, user_id, group_id, to_send.strip())
                                    total_sent_tokens += self._count_tokens(to_send.strip())
                                buf = buf[idx+1:]

                elif evt["type"] == "tool_call" and evt.get("name"):
                    t_name = evt["name"]
                    t_args = evt.get("args", {})
                    
                    if buf.strip():
                        self._log_activity_dispatcher("AI 计划/答复", buf.strip(), user_id=user_id)
                        if is_voice_reply:
                            style_match = re.match(r'^\[([^\s\]]+)\](.*)', buf.strip(), re.DOTALL)
                            pure_text = style_match.group(2).strip() if style_match else buf.strip()
                            if pure_text:
                                await send_voice(self.context, msg_type, user_id, group_id, pure_text, voice_style)
                                total_sent_tokens += self._count_tokens(pure_text)
                        else:
                            await self.context.send_chunk(msg_type, user_id, group_id, buf.strip())
                            total_sent_tokens += self._count_tokens(buf.strip())
                        buf = ""
                    
                    self._log_activity_dispatcher("工具调用", f"{t_name} | 参数: {t_args}", user_id=user_id)

                elif evt["type"] == "tool_result":
                    res = evt.get("result", "")
                    t_name = evt.get("name", "tool")
                    self._log_activity_dispatcher("工具返回", f"{t_name} | 结果大小: {len(res)} 字节", user_id=user_id)
                    
                    # 错情异常判定
                    has_error = False
                    if "exit code:" in res:
                        m = re.search(r'exit code:\s*(\d+)', res)
                        if m and m.group(1) != "0":
                            has_error = True
                    elif res.strip().startswith("Error"):
                        has_error = True
                    
                    if has_error:
                        self._log_activity_dispatcher("系统异常", f"工具 {t_name} 执行失败: {res[:200]}", user_id=user_id)
                        await self.context.send_msg(msg_type, user_id, group_id, f"⚠️ [系统异常] 刚才小萤大脑在运行工具 {t_name} 时发生了错误。反馈如下：\n{res[:300]}", skip_delay=True)

                elif evt["type"] == "permission_request":
                    cat = evt.get("category", "write")
                    # 安全分权：如果是亮哥，则正常进行物理 QQ 审批卡片；如果是同事，直接底层拦截不进行弹窗打扰
                    if str(user_id) != str(self.admin_id):
                        self._log_activity_dispatcher("系统安全拦截", f"安全拦截非管理员 {user_id} 对工具 {evt.get('tool_name')} 的 {cat} 操作申请", user_id=user_id)
                        agent.deny_permission()
                        await self.context.send_msg(msg_type, user_id, group_id, "⚠️ [权限限制] 抱歉，为了系统安全，您在沙箱中无法授权执行此修改操作哦。", skip_delay=True)
                    else:
                        tool_list = [evt.get("tool_name", "?")]
                        self._log_activity_dispatcher("系统调度", f"主人触发权限审批拦截，申请工具: {tool_list}", user_id=user_id)
                        
                        await self.context.send_msg(msg_type, user_id, group_id, 
                            f"🔧 [主人专属审批授权]\n\n小萤正在尝试执行敏感的 {cat} 修改或命令动作。详情：\n{evt.get('message', '')}\n\n回复「允许」或「y」放行，回复其他取消该敏感操作。", skip_delay=True)
                        
                        evt_perm = _PermEvent()
                        self._pending_perms[session_key] = evt_perm
                        try:
                            # 亮哥有 120 秒时间来做物理 QQ 卡片放行
                            await asyncio.wait_for(evt_perm.wait(), timeout=120)
                            approved = evt_perm.result
                        except asyncio.TimeoutError:
                            approved = False
                        finally:
                            self._pending_perms.pop(session_key, None)
                            
                        if approved:
                            self._log_activity_dispatcher("系统调度", "主人物理 QQ 授权通过，批准放行敏感操作！", user_id=user_id)
                            agent.approve_permission()
                        else:
                            self._log_activity_dispatcher("系统调度", "主人物理 QQ 授权被拒绝或超时，安全取消操作！", user_id=user_id)
                            agent.deny_permission()
                            await self.context.send_msg(msg_type, user_id, group_id, "已取消该敏感指令的执行。", skip_delay=True)

                elif evt["type"] == "error":
                    self._log_activity_dispatcher("系统异常", f"Agent 报错: {evt['content']}", user_id=user_id)
                    await self.context.send_msg(msg_type, user_id, group_id, evt["content"], skip_delay=True)
                    return
                elif evt["type"] == "_done":
                    total_sent_tokens = getattr(agent, "_total_tokens", 0)
                    ctx_tokens = agent.compressor.estimate_tokens(agent.messages) if agent.compressor else 0
                    self._log_activity_dispatcher("系统调度", f"本次推理完成。大模型总共消耗约 {total_sent_tokens} Tokens，当前会话上下文预估: {ctx_tokens} Tokens", user_id=user_id)

            # ── 冲突检测 (Collision Detection) 第二阶段 ──
            if self.bus.is_collision(session_key, task_start_time):
                buf = ""
                return

            if buf.strip():
                self._log_activity_dispatcher("AI 计划/答复", buf.strip(), user_id=user_id)
                if is_voice_reply:
                    # 剥除发音前缀
                    style_match = re.match(r'^\[([^\s\]]+)\](.*)', buf.strip(), re.DOTALL)
                    pure_text = style_match.group(2).strip() if style_match else buf.strip()
                    if pure_text:
                        await send_voice(self.context, msg_type, user_id, group_id, pure_text, voice_style)
                        total_sent_tokens += self._count_tokens(pure_text)
                else:
                    await self.context.send_chunk(msg_type, user_id, group_id, buf.strip())
                    total_sent_tokens += self._count_tokens(buf.strip())

            # 私聊疲劳累加扣分机制（非管理员私聊），默认下调至 0.025 对标 DeepSeek-V4-Flash
            if msg_type == "private" and user_id != self.admin_id:
                fatigue_rate = float(os.getenv("QQ_FATIGUE_RATE", "0.025"))
                inc = total_sent_tokens * fatigue_rate
                await self._adjust_fatigue(session_key, inc, is_private=True, sender_name=sender_name)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in dispatcher runner: {e}", exc_info=True)
            await self.context.send_msg(msg_type, user_id, group_id, "⚠️ [系统错误] 小萤的大脑有些错乱，没有听清亮哥的话，再说一次好不好呀～", skip_delay=True)
        finally:
            if self.bot and hasattr(self.bot, "_current_tasks"):
                self.bot._current_tasks.pop(session_key, None)

            # 自动拉起下一个排队任务，维持完美功能闭环与单元测试向下兼容
            if self.bot and hasattr(self.bot, "_message_queues"):
                queue = self.bot._message_queues.get(session_key, [])
                if queue:
                    next_event, next_raw = queue.pop(0)
                    logger.info(f"🔄 [系统调度] 自动拉起下一个排队任务: {next_raw}")
                    asyncio.create_task(self.dispatch_event(next_event))

    async def _adjust_fatigue(self, group_id: str, inc: float, event: dict = None, is_private: bool = False, sender_name: str = ""):
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
                if self.bot and hasattr(self.bot, "_generate_private_fatigue_announcement"):
                    announcement = await self.bot._generate_private_fatigue_announcement(user_id)
                else:
                    announcement = await self._generate_private_fatigue_announcement(user_id, sender_name)
                await self.context.send_msg("private", user_id, "", announcement, skip_delay=True)
                self._log_activity_dispatcher("物理打盹", f"小萤在私聊 {user_id} 中宣告打盹: {announcement}")
                
                agent = self.context._agents.get(group_id)
                if agent is not None:
                    task = asyncio.create_task(self._private_sleep_and_dream_process(group_id, user_id, agent, sender_name))
                    self._active_sleep_tasks[group_id] = task
            else:
                if self.bot and hasattr(self.bot, "_generate_fatigue_announcement"):
                    announcement = await self.bot._generate_fatigue_announcement(group_id)
                else:
                    announcement = await self._generate_fatigue_announcement(group_id)
                await self.context.send_msg("group", "", group_id, announcement, skip_delay=True)
                self._log_activity_dispatcher("物理打盹", f"小萤在群 {group_id} 中宣告打盹: {announcement}")
                
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
            _pn, _ua = self._load_persona()
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
        try:
            snapshot = list(agent.messages)
            agent.messages = []
            if getattr(agent, "session", None):
                await agent.session.replace_all([])
            await asyncio.sleep(self.fatigue_sleep_seconds)
            
            agent.messages = snapshot
            try:
                from agent.evolution import trigger_deep_dream_evolution
                await trigger_deep_dream_evolution(agent)
            except Exception:
                pass
            finally:
                agent.messages = []
            self._fatigue_levels[session_key] = 0.0
            self._sleep_modes[session_key] = False
            self._active_sleep_tasks.pop(session_key, None)
            
            wake_msg = await self._generate_private_wake_announcement(user_id, sender_name)
            await self.context.send_msg("private", user_id, "", wake_msg, skip_delay=True)
        except Exception:
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
            _pn, _ua = self._load_persona()
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
            _pn, _ua = self._load_persona()
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
        try:
            snapshot = list(agent.messages)
            agent.messages = []
            if getattr(agent, "session", None):
                await agent.session.replace_all([])
            await asyncio.sleep(self.fatigue_sleep_seconds)
            
            agent.messages = snapshot
            try:
                from agent.evolution import trigger_deep_dream_evolution
                await trigger_deep_dream_evolution(agent)
            except Exception:
                pass
            finally:
                agent.messages = []
            self._fatigue_levels[group_id] = 0.0
            self._sleep_modes[group_id] = False
            
            wake = await self._generate_wake_announcement(group_id)
            await self.context.send_msg("group", "", group_id, wake, skip_delay=True)
        except Exception:
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
            _pn, _ua = self._load_persona()
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

    def _count_tokens(self, text: str) -> int:
        """中英文混合 Token 科学算法"""
        if not text:
            return 0
        cjk = sum(1 for c in text if '一' <= c <= '鿿')
        en = len(text) - cjk
        return max(1, cjk // 2 + en // 4)

    def _load_persona(self) -> tuple:
        """加载当前机器人名称和对主人称呼，优先从 bot 实例加载，以便于测试中 Mock 画像"""
        if self.bot and hasattr(self.bot, "_load_persona"):
            return self.bot._load_persona()
            
        import json
        from pathlib import Path
        _persona_name = "小萤"
        _user_address = "亮哥"
        try:
            pf = Path("/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/persona_profile.json")
            if pf.exists():
                prof = json.loads(pf.read_text(encoding="utf-8"))
                _persona_name = prof.get("name", "小萤")
                _user_address = prof.get("user_address", "亮哥")
        except Exception:
            pass
        return _persona_name, _user_address

    def _log_activity_dispatcher(self, category: str, content: str, user_id: str = None):
        """记录活动日志，支持基于触发者身份的物理流隔离分发"""
        # 桥接 bot 实例写日志，保证活动链条不断裂
        if self.bot and hasattr(self.bot, "_log_activity"):
            self.bot._log_activity(category, content, user_id=user_id)
