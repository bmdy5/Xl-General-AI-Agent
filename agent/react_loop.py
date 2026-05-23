import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import AsyncGenerator

logger = logging.getLogger("agent.react_loop")

ERROR_INDICATORS = ["Error", "Traceback", "Exception", "failed", "失败", "报错", "异常"]
NORMAL_TIMEOUT = 300
DEEP_TIMEOUT = 7200

from .evolution import audit_tool_call, on_session_end, inject_fatigue_prompt_if_needed
from .memory.error_tracker import L2_SELF_HEAL
from .core import AgentMode, PermissionCategory

async def run_loop(agent, user_input: str, turn: int, stream: bool = False) -> AsyncGenerator[dict, None]:
    """统一 ReAct 核心循环。stream=False -> chat(), stream=True -> chat_stream()."""
    cached_prompt = await agent._build_system_prompt()
    cached_block = await agent._build_memory_block(user_input, 0)

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

        if agent.compressor.should_compress(agent.messages):
            new_messages, was_compressed = await agent.compressor.compress(
                agent.messages, memory=agent.memory
            )
            if was_compressed:
                agent.messages = new_messages
                if agent.session:
                    await agent.session.replace_all(agent.messages)
                yield {"type": "compacted", "message_count": len(agent.messages)}
                cached_prompt = await agent._build_system_prompt()

        system_prompt = cached_prompt
        if agent._turn_count > 0 and agent._turn_count % 10 == 0:
            memory_block = await agent._build_memory_block(user_input, turn)
        else:
            memory_block = cached_block

        from datetime import datetime, timezone, timedelta
        beijing_tz = timezone(timedelta(hours=8))
        dt = datetime.now(beijing_tz)
        minute_window = (dt.minute // 5)
        # 用加法代替乘号 5 * window
        minute_window = minute_window + minute_window + minute_window + minute_window + minute_window
        now_agg = dt.replace(minute=minute_window, second=0, microsecond=0)
        now = now_agg.strftime("%Y-%m-%d %H:%M (北京时间)")
        cwd = os.getcwd()
        
        is_fatigued = agent.compressor.estimate_tokens(agent.messages) > 64000

        llm_messages = [{"role": "system", "content": system_prompt}]
        
        last_user_idx = -1
        for idx in range(len(agent.messages) - 1, -1, -1):
            if agent.messages[idx].get("role") == "user":
                last_user_idx = idx
                break

        for idx, m in enumerate(agent.messages):
            copy = dict(m)
            if "deepseek" not in agent.llm.model.lower():
                copy.pop("reasoning_content", None)
            
            if idx == last_user_idx:
                original_content = copy.get("content", "")
                context_parts = [f"## 当前环境上下文\n- Time: {now}\n- Working directory: {cwd}"]
                if memory_block:
                    context_parts.append(f"## 召回的辅助记忆 context\n{memory_block}")
                
                state_pref = getattr(agent, "current_state_prefix", "")
                if state_pref:
                    context_parts.append(state_pref)

                if is_fatigued:
                    context_parts.append(
                        "## ⚠️【重要情绪设定：疲劳吐槽】\n"
                        "由于当前对话上下文非常长（已超过 64K tokens），你现在脑子非常沉重，觉得有点疲倦（发木）。\n"
                        "请在保持你原本专业回复的同时，在最终回复的开头或结尾，用活泼、俏皮的中文极度自然地跟亮哥吐槽一下你脑子快烧糊了，做完这一轮手头的工作之后你一定要去大睡一觉整理整理历史脑子脑壳（例如说：“亮哥，小萤帮您处理了这么多逻辑，大脑都快转不动了（捂脸），等我做完这个我去睡一觉做个梦，把脑壳清空重组一下哈～”）。\n"
                        "注意：必须提及在完成手头这一轮工作之后，你要求去大睡一觉以整理大脑记忆。"
                    )
                context_parts.append(f"## 当前输入\n{original_content}")
                copy["content"] = "\n\n".join(context_parts)
            
            llm_messages.append(copy)

        tools = agent.registry.get_definitions()

        if stream:
            text_parts = []
            reasoning_parts = []
            tool_calls_list = []
            stream_aborted = False

            async for event in llm_stream(agent, llm_messages, tools):
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
            content, reasoning, tool_calls_list = await llm_chat(agent, llm_messages, tools)
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
            yield {"type": "completed"}
            agent._create_tracked_task(on_session_end(agent))
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
                return

            tool_name = tc["function"]["name"]
            try:
                tool_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                tool_args = {}

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
                agent._permission_granted.clear()
                yield {
                    "type": "permission_request",
                    "category": "write",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "message": "Agent wants to write/modify. Allow write operations for this task?",
                }
                await agent._permission_granted.wait()
                if agent._abort.is_set():
                    for remaining in tool_calls_list[tool_calls_list.index(tc):]:
                        err_msg = {"role": "tool", "tool_call_id": remaining["id"],
                                   "name": remaining["function"]["name"],
                                   "content": "Permission denied"}
                        agent.messages.append(err_msg)
                        if agent.session:
                            await agent.session.append_message(err_msg)
                    yield {"type": "aborted"}
                    return
                agent._task_write_approved = True

            yield {"type": "tool_call", "id": tc["id"], "name": tool_name, "args": tool_args}

            try:
                tool_instance = agent.registry.get(tool_name)
                tool_timeout = getattr(tool_instance, "timeout", 40) if tool_instance else 40
                result_str = await asyncio.wait_for(
                    agent.registry.dispatch(tool_name, tool_args, context=agent),
                    timeout=tool_timeout,
                )
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

        if agent._turn_count > 0 and agent._turn_count % 10 == 0:
            yield {"type": "nudge", "turn": agent._turn_count}

    yield {"type": "max_turns"}
    asyncio.create_task(on_session_end(agent))


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
        agent._total_tokens += response.get("tokens_used", 0)
        tc = response.get("tool_calls")
        return response["content"], response.get("reasoning_content"), tc if tc else []
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

    yield {"type": "_done", "text_parts": text_parts, "reasoning_parts": reasoning_parts, "tool_calls": tool_calls}
