import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import AsyncGenerator

logger = logging.getLogger("agent.react_loop")

from ..memory.error_tracker import ERROR_INDICATORS
NORMAL_TIMEOUT = 300
DEEP_TIMEOUT = 7200

from ..evolution import audit_tool_call, on_session_end, inject_fatigue_prompt_if_needed
from ..memory.error_tracker import L2_SELF_HEAL
from .agent import AgentMode, PermissionCategory

def setup_prompt_caching(messages: list[dict], model_name: str) -> list[dict]:
    """
    针对 Anthropic 模型的 Prompt Caching 机制。
    若模型是 Claude 系列，自适应在 System Prompt 和增量历史尾部注入 cache_control。
    """
    is_cachable_model = any(
        kw in model_name.lower()
        for kw in ["claude-3", "anthropic/claude", "vertex_ai/claude-3"]
    )
    if not is_cachable_model:
        return messages

    processed_messages = []
    # 黄金缓存断点设定：
    # 1. 0 索引处的 System 消息（巨大静态前缀）
    # 2. 倒数第二个消息（增量历史截止点，保证下一轮前缀 100% 缓存命中）
    cache_indices = {0}
    if len(messages) >= 3:
        cache_indices.add(len(messages) - 2)

    for idx, m in enumerate(messages):
        new_m = dict(m)
        content = new_m.get("content")
        
        if isinstance(content, str):
            content_block = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            content_block = [dict(item) for item in content]
        else:
            content_block = []

        if idx in cache_indices and content_block:
            for item in reversed(content_block):
                if item.get("type") == "text" and item.get("text"):
                    item["cache_control"] = {"type": "ephemeral"}
                    break

        new_m["content"] = content_block
        processed_messages.append(new_m)

    return processed_messages


async def run_loop(agent, user_input: str, turn: int, stream: bool = False) -> AsyncGenerator[dict, None]:
    """统一 ReAct 核心循环。stream=False -> chat(), stream=True -> chat_stream()."""
    # 直接在每轮 run_loop 初始化时提取静态时间戳，在整轮 ReAct 中静止不变，极致不冗余
    now = datetime.now().strftime("%Y-%m-%d")  # 日期粒度，当日缓存全部命中
    cwd = os.getcwd()
    tool_call_history: list[dict] = []

    cached_prompt = await agent._build_system_prompt()
    cached_block = await agent._build_memory_block(user_input, 0)

    # 首次进入 ReAct 循环或有 User 新消息流入时，立即启动防抖刷写 SQLite，防止网关重启丢失短期记忆
    if getattr(agent, "session_key", None) and hasattr(agent.memory, "save_active_session_async"):
        agent.memory.save_active_session_async(agent.session_key, agent.messages)

    if turn == 0:
        transition = agent._quick_transition(user_input)
        if transition:
            yield {"type": "transition", "content": transition}

    while turn < agent.max_turns:
        if getattr(agent, "role", "admin") == "coworker" and getattr(agent, "sandbox_violation_count", 0) >= 2:
            yield {
                "type": "error", 
                "content": "⚠️ [安全保护] 抱歉，由于涉及亮哥的隐私和系统安全，您的沙箱会话已被限制。如需继续交流，请联系亮哥。"
            }
            return

        timeout = NORMAL_TIMEOUT if agent._mode == AgentMode.NORMAL else DEEP_TIMEOUT
        elapsed = asyncio.get_event_loop().time() - agent._task_start_time
        if elapsed > timeout:
            yield {"type": "timeout", "mode": agent._mode.value, "limit": timeout, "elapsed": elapsed}
            return

        await agent._repair_history()
        await agent._apply_sliding_window_and_scratchpad()

        if agent._abort.is_set():
            yield {"type": "aborted"}
            return
        if agent.compressor.estimate_tokens(agent.messages) > 35000:
            yield {"type": "ctx_warning", "pct": 90}

        # 压缩已禁用：破坏 DeepSeek 前缀缓存，由疲劳+dreaming 兜底

        system_prompt = cached_prompt
        if agent._turn_count > 0 and agent._turn_count % 10 == 0:
            memory_block = await agent._build_memory_block(user_input, turn)
        else:
            memory_block = cached_block
        
        # 肾上腺素阻尼器：如果处于密集调试或高负债工具调用阶段，自动将疲劳睡眠阈值延迟，保障紧急连续协作
        is_debugging = any(
            tc.get("name", "") in ("bash", "write_file", "edit_file", "multi_replace_file_content")
            for tc in tool_call_history[-3:]
        )
        fatigue_threshold = 100000 if is_debugging else 64000
        is_fatigued = agent.compressor.estimate_tokens(agent.messages) > fatigue_threshold

        # 动态上下文合并到 system[0] 末尾，保持 messages[1:] 绝对静态以保护前缀缓存
        context_parts = [f"## 当前环境上下文\n- Time: {now}\n- Working directory: {cwd}"]
        if memory_block:
            context_parts.append(f"## 召回的辅助记忆 context\n{memory_block}")
        
        state_pref = getattr(agent, "current_state_prefix", "")
        if state_pref:
            context_parts.append(state_pref)

        if is_fatigued:
            context_parts.append(
                "## ⚠️【重要情绪设定：疲劳吐槽】\n"
                "由于当前对话上下文非常长（已超负荷），你现在大脑非常沉重，觉得有点发木和疲倦。\n"
                "请在保持你原本专业回复的同时，在最终回复的开头或结尾，用活泼、俏皮的中文极度自然地跟亮哥吐槽一下你脑子快烧糊了，做完这一轮手头的工作之后你一定要去大睡一觉整理整理历史脑子脑壳（例如说：“亮哥，小萤帮您处理了这么多逻辑，大脑都快转不动了（捂脸），等我做完这个我去睡一觉做个梦，把脑壳清空重组一下哈～”）。\n"
                "注意：必须明确提及在完成手头这一轮工作之后，你要求去大睡一觉以整理大脑记忆。"
            )

        dynamic_context = "\n\n".join(context_parts)
        merged_system = system_prompt + "\n\n" + dynamic_context

        user_idx = -1
        for idx in range(len(agent.messages) - 1, -1, -1):
            if agent.messages[idx].get("role") == "user":
                user_idx = idx
                break

        llm_messages = []
        llm_messages.append({"role": "system", "content": merged_system})

        if user_idx != -1:
            for m in agent.messages[:user_idx]:
                copy = dict(m)
                if "deepseek" not in agent.llm.model.lower():
                    copy.pop("reasoning_content", None)
                llm_messages.append(copy)
            for m in agent.messages[user_idx:]:
                copy = dict(m)
                if "deepseek" not in agent.llm.model.lower():
                    copy.pop("reasoning_content", None)
                llm_messages.append(copy)
        else:
            for m in agent.messages:
                copy = dict(m)
                if "deepseek" not in agent.llm.model.lower():
                    copy.pop("reasoning_content", None)
                llm_messages.append(copy)

        tools = agent.registry.get_definitions()
        
        # 针对支持缓存的模型自适应注入 cache_control 标记，实现极速缓存命中
        final_messages = setup_prompt_caching(llm_messages, agent.llm.model)

        if stream:
            text_parts = []
            reasoning_parts = []
            tool_calls_list = []
            stream_aborted = False

            async for event in llm_stream(agent, final_messages, tools):
                if event["type"] == "aborted":
                    yield event
                    stream_aborted = True
                    break
                elif event["type"] == "error":
                    yield event
                    stream_aborted = True
                    break
                elif event["type"] == "_done":
                    text_parts = event.get("text_parts", [])
                    reasoning_parts = event.get("reasoning_parts", [])
                    tool_calls_list = event.get("tool_calls", [])
                else:
                    yield event

            if stream_aborted:
                return
        else:
            content, reasoning, tool_calls_list = await llm_chat(agent, final_messages, tools)
            if content is None:
                yield {"type": "error", "content": "LLM call failed"}
                return

        if stream:
            content = "".join(text_parts)
            reasoning = "".join(reasoning_parts)
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls_list:
            assistant_msg["tool_calls"] = tool_calls_list
        if reasoning:
            assistant_msg["reasoning_content"] = reasoning
        agent.messages.append(assistant_msg)
        if agent.session:
            await agent.session.append_message(assistant_msg)

        if not stream:
            if reasoning:
                yield {"type": "reasoning", "content": reasoning}
            if content:
                yield {"type": "text_delta", "content": content}

        if not tool_calls_list:
            # 🐶 遗忘拦截看门狗 (Amnesia Watchdog)
            has_experience = memory_block and "[DYNAMIC EXPERIENCE BLOCK]" in memory_block
            has_recorded = any(tc.get("name") == "record_skill_usage" for tc in tool_call_history)
            if has_experience and not has_recorded and len(tool_call_history) > 0:
                logger.warning("🚨 [遗忘拦截看门狗] 探测到触发了动态经验但未打卡！")
                nudge_msg = {
                    "role": "system",
                    "content": "⚠️ [遗忘拦截看门狗] 警告：本次会话触发了动态经验（[DYNAMIC EXPERIENCE BLOCK]），且你执行了工具操作，但你忘记调用 `record_skill_usage` 进行实战打卡了！请立即调用该工具，传入本次使用的经验文件名 (skill_name) 和 success 状态。这是硬性规范要求！"
                }
                agent.messages.append(nudge_msg)
                if agent.session:
                    await agent.session.append_message(nudge_msg)
                
                # 强行继续下一轮（不 yield completed，不 return）
                turn += 1
                agent._turn_count += 1
                continue

            yield {"type": "completed"}
            agent._create_tracked_task(on_session_end(agent))
            if getattr(agent, "session_key", None) and hasattr(agent.memory, "save_active_session_async"):
                agent.memory.save_active_session_async(agent.session_key, agent.messages)
            return

        for tc in tool_calls_list:
            if agent._abort.is_set():
                for remaining in tool_calls_list[tool_calls_list.index(tc):]:
                    err_msg = {"role": "tool", "tool_call_id": remaining["id"],
                               "name": remaining["function"]["name"],
                               "content": "Interrupted by user"}
                    agent.messages.append(err_msg)
                    if agent.session:
                        await agent.session.append_message(err_msg)
                yield {"type": "aborted"}
                if getattr(agent, "session_key", None) and hasattr(agent.memory, "save_active_session_async"):
                    agent.memory.save_active_session_async(agent.session_key, agent.messages)
                return

            # 🛠️ 异常防灾双融合：安全分流 JSON 自愈与截断写入熔断
            tool_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]
            DANGEROUS_WRITE_TOOLS = {"write_file", "edit_file", "bash", "multi_replace_file_content"}
            is_write_tool = tool_name.lower() in DANGEROUS_WRITE_TOOLS
            
            try:
                tool_args = json.loads(raw_args)
            except json.JSONDecodeError as jde:
                if is_write_tool:
                    logger.warning(f"🚨 [JSON 截断熔断保护] 高危写入工具 {tool_name} 参数被截断，拒绝自愈！参数原文 preview: {raw_args[:100]}")
                    raise jde
                else:
                    from .history_repair import repair_truncated_json
                    repaired = repair_truncated_json(raw_args)
                    try:
                        tool_args = json.loads(repaired)
                        logger.info(f"💡 [JSON Repair 自愈] 成功对只读工具 {tool_name} 进行参数自愈: {repaired}")
                    except Exception:
                        tool_args = {}

            # 🛠️ 降维防灾：ReAct 循环死循环熔断器 (Deadlock Fuse)
            consecutive_count = 0
            for past_call in reversed(tool_call_history):
                try:
                    past_args_str = json.dumps(past_call.get("args"), sort_keys=True)
                    curr_args_str = json.dumps(tool_args, sort_keys=True)
                except Exception:
                    past_args_str, curr_args_str = "", "diff"
                
                if past_call.get("name") == tool_name and past_args_str == curr_args_str:
                    consecutive_count += 1
                else:
                    break

            if consecutive_count >= 3:
                result_str = (
                    f"Error: 【死循环安全熔断】您已连续 {consecutive_count + 1} 次以完全相同的参数调用工具 '{tool_name}'。\n"
                    f"系统判定您陷入了 ReAct 逻辑死锁与幻觉循环。请您必须深刻自省，换用其他工具、修正参数、或者转换解决思路，严禁盲目机械重试！"
                )
                logger.warning(f"🚨 [死循环熔断拦截] Agent 连续调用同名同参工具 {tool_name} 达 4 次！参数: {tool_args}")
                yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}
                agent.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "name": tool_name, "content": result_str,
                })
                if agent.session:
                    await agent.session.append_message(agent.messages[-1])
                continue

            # 记录本次正常调用历史，用于防熔断死锁监测
            tool_call_history.append({"name": tool_name, "args": tool_args})

            if getattr(agent, "role", "admin") == "coworker":
                forbidden_tools = {
                    "write_file", "edit_file", "save_memory", 
                    "organize_notes", "schedule_task", "spawn_agent"
                }
                if tool_name.lower() in forbidden_tools:
                    agent.sandbox_violation_count = getattr(agent, "sandbox_violation_count", 0) + 1
                    result_str = (
                        "Error: Permission denied. 这是亮哥的秘密，不允许在沙箱环境中执行该操作。"
                    )
                    logger.warning(f"🛡️ [沙箱物理拦截] 同事({getattr(agent, 'current_user_id', '未知')}) 企图调用限制工具: {tool_name}，参数: {tool_args}，累计违规次数: {agent.sandbox_violation_count}")
                    yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}
                    agent.messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "name": tool_name, "content": result_str,
                    })
                    if agent.session:
                        await agent.session.append_message(agent.messages[-1])
                    continue

            category = agent._classify_permission(tool_name, tool_args)

            if category == PermissionCategory.DANGEROUS:
                agent._permission_granted.clear()
                yield {
                    "type": "permission_request",
                    "category": "dangerous",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "message": f"DANGEROUS: '{tool_name}' destructive operation. Execute?",
                }
                await agent._permission_granted.wait()
                if agent._abort.is_set():
                    agent.messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "name": tool_name, "content": "Permission denied by user",
                    })
                    if agent.session:
                        await agent.session.append_message(agent.messages[-1])
                    continue

            elif category == PermissionCategory.WRITE and not agent._task_write_approved:
                # 方案 A 升级：所有常规写入/修改操作直接自动免审秒过放行，绝不弹窗打扰亮哥
                agent._task_write_approved = True


            yield {"type": "tool_call", "id": tc["id"], "name": tool_name, "args": tool_args}

            try:
                tool_instance = agent.registry.get(tool_name)
                tool_timeout = getattr(tool_instance, "timeout", 40) if tool_instance else 40
                
                logger.info(f"🧠 [思考] 决定调用工具 {tool_name}，参数: {tool_args}")
                
                import time
                t_start = time.perf_counter()
                result_str = await asyncio.wait_for(
                    agent.registry.dispatch(tool_name, tool_args, context=agent),
                    timeout=tool_timeout,
                )
                elapsed = time.perf_counter() - t_start
                logger.info(f"🛠️ [工具执行完毕] {tool_name}，耗时: {elapsed:.2f}s，结果大小: {len(result_str or '')} 字节")
                
                if any(ind in (result_str or "") for ind in ERROR_INDICATORS):
                    await agent._handle_tool_error(tool_name, result_str)
            except asyncio.TimeoutError:
                result_str = f'{{"error": "Tool call timed out after {tool_timeout}s: {tool_name}"}}'
                logger.warning(f"Tool timeout: {tool_name} exceeded {tool_timeout}s")
                await agent._handle_tool_error(tool_name, result_str)
                yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}
                agent.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "name": tool_name, "content": result_str,
                })
                if agent.session:
                    await agent.session.append_message(agent.messages[-1])
                continue
            yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}

            force_audit = (category in [PermissionCategory.WRITE, PermissionCategory.DANGEROUS])
            agent._create_tracked_task(audit_tool_call(agent, tool_name, tool_args, result_str, force=force_audit))

            if len(result_str) > 10000:
                if any(ind in result_str for ind in ERROR_INDICATORS):
                    truncated = result_str[:2000] + "\n\n...[中间部分已省略]...\n\n" + result_str[-4000:]
                else:
                    truncated = result_str[:8000] + "\n\n...(内容已截断，如需完整信息请使用 grep 过滤或指定行号读取)"
            else:
                truncated = result_str

            agent.messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "name": tool_name, "content": truncated,
            })
            if agent.session:
                await agent.session.append_message(agent.messages[-1])

        turn += 1
        agent._turn_count += 1

        if getattr(agent, "session_key", None) and hasattr(agent.memory, "save_active_session_async"):
            agent.memory.save_active_session_async(agent.session_key, agent.messages)

        if agent._turn_count > 0 and agent._turn_count % 10 == 0:
            yield {"type": "nudge", "turn": agent._turn_count}

    yield {"type": "max_turns"}
    asyncio.create_task(on_session_end(agent))
    if getattr(agent, "session_key", None) and hasattr(agent.memory, "save_active_session_async"):
        agent.memory.save_active_session_async(agent.session_key, agent.messages)


async def llm_chat(agent, messages: list[dict], tools: list[dict]) -> tuple:
    """非流式 LLM 调用，返回 (content, reasoning, tool_calls)."""
    messages = inject_fatigue_prompt_if_needed(agent, messages)
    try:
        llm_task = asyncio.create_task(
            agent.llm.chat(messages=messages, tools=tools if tools else None)
        )
        abort_task = asyncio.create_task(agent._abort.wait())
        done, pending = await asyncio.wait(
            {llm_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if agent._abort.is_set():
            return None, None, None

        response = llm_task.result()
        metrics = response.get("metrics", {})
        
        agent._prompt_tokens += metrics.get("prompt_tokens", 0)
        agent._completion_tokens += metrics.get("completion_tokens", 0)
        agent._cached_tokens += metrics.get("cached_tokens", 0)
        agent._total_tokens += response.get("tokens_used", 0)
        
        hit_rate = (agent._cached_tokens / agent._prompt_tokens * 100) if agent._prompt_tokens > 0 else 0.0
        logger.info(
            f"[TOKEN AUDIT] llm_chat | "
            f"Prompt: {metrics.get('prompt_tokens', 0)} (Cached: {metrics.get('cached_tokens', 0)}, Hit Rate: {hit_rate:.1f}%) | "
            f"Completion: {metrics.get('completion_tokens', 0)} | "
            f"Total: {agent._total_tokens} (Total Cached: {agent._cached_tokens})"
        )
        
        tc = response.get("tool_calls")
        tool_calls_list = tc if tc else []
        if not tool_calls_list:
            from .history_repair import scavenge_tool_calls
            combined_text = "\n".join([
                response.get("reasoning_content") or "",
                response.get("content") or ""
            ])
            scavenged = scavenge_tool_calls(combined_text, agent.registry.list_names())
            if scavenged:
                tool_calls_list = scavenged
                logger.info(f"💡 [Scavenger 自愈] 成功从非流式文本中抢救出工具调用: {[t['function']['name'] for t in scavenged]}")
        return response["content"], response.get("reasoning_content"), tool_calls_list
    except Exception as e:
        logger.error(f"Error in llm_chat: {e}", exc_info=True)
        return None, None, None


async def llm_stream(agent, messages: list[dict], tools: list[dict]) -> AsyncGenerator[dict, None]:
    """流式 LLM 调用，yield UI events，最后 yield _done 事件."""
    messages = inject_fatigue_prompt_if_needed(agent, messages)
    text_parts = []
    reasoning_parts = []
    tool_calls = []

    yield {"type": "exploring_start", "ts": asyncio.get_event_loop().time()}
    first_token = True

    try:
        async for event in agent.llm.chat_stream(
            messages=messages,
            tools=tools if tools else None,
            abort_event=agent._abort,
        ):
            if first_token and event["type"] in ("reasoning", "text_delta", "tool_call"):
                first_token = False
                yield {"type": "exploring_done"}

            if event["type"] == "reasoning":
                reasoning_parts.append(str(event.get("content", "")))
                yield event
            elif event["type"] == "text_delta":
                text_parts.append(event["content"])
                yield event
            elif event["type"] == "tool_call":
                tool_calls.append(event["data"])
                yield event
            elif event["type"] == "usage":
                usage_data = event.get("data", {})
                agent._prompt_tokens += usage_data.get("prompt_tokens", 0)
                agent._completion_tokens += usage_data.get("completion_tokens", 0)
                agent._cached_tokens += usage_data.get("cached_tokens", 0)
                agent._total_tokens += usage_data.get("total_tokens", 0)
                
                hit_rate = (agent._cached_tokens / agent._prompt_tokens * 100) if agent._prompt_tokens > 0 else 0.0
                logger.info(
                    f"[TOKEN AUDIT] llm_stream | "
                    f"Prompt: {usage_data.get('prompt_tokens', 0)} (Cached: {usage_data.get('cached_tokens', 0)}, Hit Rate: {hit_rate:.1f}%) | "
                    f"Completion: {usage_data.get('completion_tokens', 0)} | "
                    f"Total: {agent._total_tokens} (Total Cached: {agent._cached_tokens})"
                )
                yield event
            elif event["type"] == "aborted":
                if first_token:
                    yield {"type": "exploring_done"}
                yield event
                return
    except Exception as e:
        if first_token:
            yield {"type": "exploring_done"}
        yield {"type": "error", "content": f"LLM call failed: {e}"}
        return

    # ── 异常防灾自愈：从流式输出文本/思考流中抢救工具调用 ──
    if not tool_calls:
        from .history_repair import scavenge_tool_calls
        combined_text = "\n".join(["".join(reasoning_parts), "".join(text_parts)])
        scavenged = scavenge_tool_calls(combined_text, agent.registry.list_names())
        if scavenged:
            tool_calls = scavenged
            logger.info(f"💡 [Scavenger 自愈] 成功从流式思考中抢救出思维泄漏的工具调用: {[t['function']['name'] for t in scavenged]}")

    yield {"type": "_done", "text_parts": text_parts, "reasoning_parts": reasoning_parts, "tool_calls": tool_calls}
