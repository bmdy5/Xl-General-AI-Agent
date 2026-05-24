import os
import re
import time
import json
import asyncio
import logging
from datetime import datetime
from .presenter import StreamPresenter

logger = logging.getLogger("net_gateway.executor")

class _PermEvent:
    """主人专属敏感指令 QQ 物理卡片审批状态同步事件对象"""
    
    def __init__(self):
        self._event = asyncio.Event()
        self.result = False

    async def wait(self):
        await self._event.wait()

    def set(self, result: bool):
        self.result = result
        self._event.set()


class AgentExecutor:
    """网关 Agent ReAct 异步推理循环与 ReAct 工具沙箱调度器"""
    
    def __init__(self, context, dispatcher):
        self.context = context
        self.dispatcher = dispatcher
        self.bot = dispatcher.bot
        self.bus = dispatcher.bus
        self.admin_id = context.admin_id
        
        # 敏感卡片等待授权审批队列
        self._pending_perms = {}  # session_key -> _PermEvent

    async def execute_agent_run(self, agent, raw: str, session_key: str, msg_type: str, 
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

        presenter = StreamPresenter(self)

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
                    presenter.buf = ""
                    return

                if evt["type"] == "exploring_start":
                    self._log_activity_dispatcher("AI 计划/答复", "思考启动...", user_id=user_id)

                elif evt["type"] == "transition":
                    self._log_activity_dispatcher("AI 计划/答复", evt['content'], user_id=user_id)
                    if not presenter.sent_transition:
                        presenter.sent_transition = True
                        await self.context.send_msg(msg_type, user_id, group_id, evt['content'])

                elif evt["type"] == "text_delta":
                    await presenter.handle_delta(evt["content"], msg_type, user_id, group_id)

                elif evt["type"] == "tool_call" and evt.get("name"):
                    t_name = evt["name"]
                    t_args = evt.get("args", {})
                    
                    await presenter.flush_buffer(msg_type, user_id, group_id)
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
                    try:
                        ctx_tokens = agent.compressor.estimate_tokens(agent.messages) if agent.compressor else 0
                    except AttributeError:
                        ctx_tokens = 0
                    self._log_activity_dispatcher("系统调度", f"本次推理完成。大模型总共消耗约 {total_sent_tokens} Tokens，当前会话上下文预估: {ctx_tokens} Tokens", user_id=user_id)

            # ── 冲突检测 (Collision Detection) 第二阶段 ──
            if self.bus.is_collision(session_key, task_start_time):
                presenter.buf = ""
                return

            await presenter.flush_buffer(msg_type, user_id, group_id)

            # 私聊疲劳累加扣分机制（非管理员私聊），默认下调至 0.025 对标 DeepSeek-V4-Flash
            if msg_type == "private" and user_id != self.admin_id:
                fatigue_rate = float(os.getenv("QQ_FATIGUE_RATE", "0.025"))
                inc = presenter.total_sent_tokens * fatigue_rate
                await self.dispatcher.fatigue_manager.adjust_fatigue(session_key, inc, is_private=True, sender_name=sender_name)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in executor runner: {e}", exc_info=True)
            await self.context.send_msg(msg_type, user_id, group_id, "⚠️ [系统错误] 小萤的大脑有些错乱，没有听清亮哥的话，再说一次好不好呀～", skip_delay=True)
        finally:
            if self.bot:
                current_coro = asyncio.current_task()
                active_task = self.bot.get_active_task(session_key)
                
                # 只有当 current_tasks 中的活跃任务依然是当前协程时，才说明没有被更新的任务抢占
                is_current = active_task is current_coro or active_task is True
                
                if is_current:
                    self.bot.remove_active_task(session_key, current_coro)
                    
                    # 自动拉起并合并下一个排队任务，维持完美功能闭环与单元测试向下兼容
                    if self.bot.has_queued_messages(session_key):
                        queued_packs = []
                        while self.bot.has_queued_messages(session_key):
                            pack = self.bot.pop_queued_message(session_key)
                            if pack:
                                queued_packs.append(pack)
                        
                        if queued_packs:
                            # 提取并判断所有的发言人是否全是管理员亮哥
                            all_admin = True
                            for pack in queued_packs:
                                next_event, _ = pack
                                next_user_id = str(next_event.get("user_id", ""))
                                if next_user_id != str(self.admin_id):
                                    all_admin = False
                                    break
                            
                            last_event, _ = queued_packs[-1]
                            
                            if all_admin:
                                # 全是管理员亮哥连发，构造亮哥连发专属合并 Prompt
                                merged_raw = "\n".join(pack[1] for pack in queued_packs)
                                prompt = (
                                    f"[系统提示：亮哥在刚才小萤思考期间连发了 {len(queued_packs)} 条消息。"
                                    f"他可能很关心或者很急切哦！请在回复中展现出你的惊喜与高情商，"
                                    f"将这些话融合在一起一次性甜甜地回答他～ 连发消息如下：\n{merged_raw}]"
                                )
                            else:
                                # 包含其他人发言，构造群聊混杂 Prompt
                                lines = []
                                for pack in queued_packs:
                                    next_event, next_raw = pack
                                    next_user_id = str(next_event.get("user_id", ""))
                                    next_sender_info = next_event.get("sender", {}) or {}
                                    next_card = str(next_sender_info.get("card", "")).strip()
                                    next_nickname = str(next_sender_info.get("nickname", "")).strip()
                                    next_sender_name = next_card or next_nickname or next_user_id
                                    
                                    display_name = "亮哥" if next_user_id == str(self.admin_id) else next_sender_name
                                    lines.append(f"{display_name}：{next_raw}")
                                
                                merged_lines = "\n".join(lines)
                                prompt = (
                                    f"[系统提示：系统检测到在此期间有多人发言（含亮哥与他人），"
                                    f"请综合他们的发言意图，一次性给予综合答复。对亮哥要保持亲昵，对他人保持克制。"
                                    f"连发消息如下：\n{merged_lines}]"
                                )
                            
                            # 重新装配 event，并以最新的时间戳自动重新调用
                            if last_event.get("message_type") == "group":
                                self_id = str(last_event.get("self_id", "999999"))
                                prompt = f"[CQ:at,qq={self_id}] {prompt}"
                                
                            last_event["raw_message"] = prompt
                            if "message" in last_event:
                                last_event["message"] = prompt
                                
                            logger.info(f"🔄 [系统调度] 自动拉起下一个排队任务并合并，合并后Prompt: {prompt}")
                            asyncio.create_task(self.dispatcher.dispatch_event(last_event))

    def _count_tokens(self, text: str) -> int:
        """中英文混合 Token 科学算法"""
        if not text:
            return 0
        cjk = sum(1 for c in text if '一' <= c <= '鿿')
        en = len(text) - cjk
        return max(1, cjk // 2 + en // 4)

    def _load_persona(self) -> tuple:
        if self.bot:
            try:
                return self.bot._load_persona()
            except AttributeError:
                pass
            
        import json
        from pathlib import Path
        _persona_name = "小萤"
        _user_address = "亮哥"
        try:
            root_dir = Path(__file__).resolve().parents[2]
            pf = root_dir / "agent" / "resources" / "persona_profile.json"
            if pf.exists():
                prof = json.loads(pf.read_text(encoding="utf-8"))
                _persona_name = prof.get("name", "小萤")
                _user_address = prof.get("user_address", "亮哥")
        except Exception:
            pass
        return _persona_name, _user_address

    def _log_activity_dispatcher(self, category: str, content: str, user_id: str = None):
        if self.bot:
            try:
                self.bot._log_activity(category, content, user_id=user_id)
            except AttributeError:
                pass
