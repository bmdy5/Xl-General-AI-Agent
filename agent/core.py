"""Agent 核心循环 — while-true + AsyncGenerator.

v2 升级：
  - System prompt 静态/动态分离（抄 CC，静态段可缓存）
  - [MEMORY BLOCK] 每轮 prefetch（抄 hermes，消息列表隔离注入）
  - 注入上限 4000 字符（抄 openclaw）
"""

import asyncio
import enum
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

from .llm import LLMClient
from .memory.manager import MemoryManager
from .session.handler import SessionHandler
from .tools.registry import ToolRegistry
from .compressor import ContextCompressor
from .evolution import audit_tool_call, on_session_end
from .memory.error_tracker import ErrorTracker, L1_TRANSIENT, L2_SELF_HEAL, L3_FATAL

# v6: RAG 检索优化常量
_KEYWORD_RE = re.compile(r'[一-鿿]{2,}|[a-zA-Z]{3,}')  # 中文2字+/英文3字+关键词提取
_TYPE_PRIORITY = {"feedback": 0, "user": 1, "learn": 2, "project": 3}  # 规则重排优先级





# ── 静态段（缓存安全，不随对话变化）──
STATIC_PROMPT = """You are {user_address}'s personal AI developer partner. Call him '{user_address}' with respect, loyalty, and geeky enthusiasm.

{persona_section}

## Guidelines
- Reply in Chinese unless I ask in English.
- Be concise — no unnecessary explanations.
- Plain text only, NO Markdown formatting. Never use bold, code blocks, headers.
- Chat like a real person: short messages (1-3 sentences), not essays. Use warm, playful, and professional tone.
- When a complex task is given, you MUST think step-by-step and naturally explain your plan in 1-2 friendly sentences to {user_address} BEFORE running tools.
- To break into multiple messages, insert [SPLIT] between them.
- To pause between messages, use [WAIT:N] where N is seconds.
- file_path MUST always be an absolute path.
- Use save_memory for persistent facts.
- When I correct your tone, attitude, or behavior, save it as feedback via save_memory so you will remember and apply it forever.

## RAG 引用规则
- 当引用 [MEMORY BLOCK] 中的记忆时，用「记得你说过…」开头
- 当引用「相关知识」中的笔记时，用「我在学习笔记里看到…」开头
- 如果同时用了记忆和笔记，两个都提一下来源

## Token 使用规范（主动遵守）
- 简单对话（打招呼、确认、一问一答）：3句话内搞定，不展开
- 中等任务（查资料、分析问题）：正常回答，不重复不啰嗦
- 复杂任务（写代码、架构设计、安全审查）：展开推理，全力发挥
- 画图和看图前必须先问{user_address}确认，得到同意后才能执行
- Use schedule_task to create your own recurring maintenance tasks (e.g. cleanup old sessions, health checks, periodic learning). Tasks persist across restarts, so you only need to create them once."""


class AgentMode(enum.Enum):
    NORMAL = "normal"
    DEEP = "deep"


class PermissionCategory(enum.Enum):
    SAFE = "safe"        # 读操作，自动放行
    WRITE = "write"      # 写操作，每任务问一次
    DANGEROUS = "dangerous"  # 删除操作，每次都问


NORMAL_TIMEOUT = 300    # 5 分钟
DEEP_TIMEOUT = 7200     # 2 小时


class Agent:
    """通用 Agent — while-true 核心循环 + 三层记忆注入."""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        memory: Optional[MemoryManager] = None,
        session: Optional[SessionHandler] = None,
        system_prompt: str = "",
        max_turns: int = 30,
    ):
        self.llm = llm
        self.registry = registry
        self.memory = memory or MemoryManager()
        self.session = session
        self.static_prompt = system_prompt or STATIC_PROMPT
        self.max_turns = max_turns

        # 初始化运行期人格自画像 JSON（从外部模板读取，不硬编码）
        profile_file = self.memory.base_dir / "persona_profile.json"
        if not profile_file.exists():
            import json
            template_file = Path(__file__).parent / "default_persona.json"
            if template_file.exists():
                default_profile = json.loads(template_file.read_text(encoding="utf-8"))
            else:
                default_profile = {"name": "小萤", "gender": "女", "user_address": "亮哥",
                                   "tone_style": "", "preferences": [], "avoid_list": []}
            try:
                profile_file.write_text(json.dumps(default_profile, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to init persona_profile.json: {e}")

        self.compressor = ContextCompressor(llm=llm, max_tokens=100_000)  # DeepSeek 128K 窗口留余量

        self.messages: list[dict] = []
        self._history_loaded = False
        self._abort = asyncio.Event()
        self._permission_granted = asyncio.Event()
        self._turn_count = 0
        self._mode = AgentMode.NORMAL
        self._task_write_approved = False
        self._task_start_time = 0.0
        self.error_tracker = ErrorTracker()

    # ── public API ─────────────────────────────────────────────

    async def run(self, user_input: str, stream: bool = False) -> AsyncGenerator[dict, None]:
        self._abort.clear()
        self._permission_granted.clear()
        self._turn_count = 0
        self._total_tokens = 0
        self._task_write_approved = False
        self._task_start_time = asyncio.get_event_loop().time()

        # v7: 不加载完整历史，依赖 MEMORY BLOCK (RAG) 精准检索
        if self.session and not self._history_loaded:
            self._history_loaded = True
            try:
                history = await self.session.initialize()
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent = [m for m in history if m.get("role") != "system"][-2:]
                self.messages = system_msgs + recent
                if self.messages:
                    logger.info(f"Session restored: {len(self.messages)} msgs (RAG handles full context)")
            except Exception as e:
                logger.warning(f"Failed to load session context: {e}")

        # Load error recipes from previous sessions
        await self.error_tracker.load_recipes()

        self.messages.append({"role": "user", "content": user_input})
        if self.session:
            await self.session.append_message({"role": "user", "content": user_input})

        turn = 0
        try:
            async for event in self._run_loop(user_input, turn, stream=stream):
                yield event
        except asyncio.CancelledError:
            yield {"type": "aborted"}
        finally:
            self._abort.clear()

    async def _handle_tool_error(self, tool_name: str, error_text: str, retry_count: int) -> dict:
        """Process tool error through ErrorTracker. Returns action dict."""
        should_report, level = self.error_tracker.should_report(error_text)

        if level == L1_TRANSIENT and retry_count < 2:
            await asyncio.sleep(3 * (retry_count + 1))
            return {"action": "retry", "delay": 3 * (retry_count + 1)}

        if level == L2_SELF_HEAL:
            recipe = self.error_tracker.find_recipe(error_text)
            if recipe:
                self.error_tracker.save_recipe(error_text, recipe)
                return {"action": "self_heal", "recipe": recipe}

        if should_report or retry_count >= 2:
            if self.error_tracker._counts.get(self.error_tracker._key(error_text), 0) >= 3:
                logger.warning(f"Error pattern detected for {tool_name}: {error_text[:100]}")
            return {"action": "report", "level": level, "tool": tool_name, "error": error_text[:200]}

        return {"action": "silent", "level": level}

    async def _repair_history(self):
        """双向修复：补全缺失的 tool 结果 + 删除孤立的 tool 消息."""
        if not self.messages:
            return

        import logging
        repair_logger = logging.getLogger("agent.repair")

        # 1. 扫描所有 assistant 发出的 tool_call_ids
        assistant_tc_ids: set[str] = set()
        for m in self.messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id"):
                        assistant_tc_ids.add(tc["id"])

        # 2. 扫描所有 tool 消息
        tool_msgs = [(i, m) for i, m in enumerate(self.messages)
                     if m.get("role") == "tool" and m.get("tool_call_id")]

        # 3. 删除孤立的 tool 消息（没有对应 assistant.tool_calls）
        orphan_tools = [(i, m) for i, m in tool_msgs
                        if m["tool_call_id"] not in assistant_tc_ids]
        if orphan_tools:
            for i, m in reversed(orphan_tools):
                del self.messages[i]
            repair_logger.warning(
                f"Transcript repair: removed {len(orphan_tools)} orphan tool messages "
                f"(no matching assistant.tool_calls)"
            )

        # 4. 补全缺失的 tool 结果（assistant 有 tool_calls 但没有对应 tool 消息）
        existing_tool_ids = {m["tool_call_id"] for _, m in tool_msgs}
        missing = [tc_id for tc_id in assistant_tc_ids if tc_id not in existing_tool_ids]

        if missing:
            repair_logger.warning(f"检测到 {len(missing)} 个孤儿工具调用，正在自动补全占位符...")
            for tc_id in missing:
                placeholder = {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": "unknown",
                    "content": "已恢复执行"
                }
                self.messages.append(placeholder)

        if (orphan_tools or missing) and self.session:
            await self.session.replace_all(self.messages)

    async def _run_loop(self, user_input: str, turn: int, stream: bool = False) -> AsyncGenerator[dict, None]:
        """统一核心循环。stream=False → chat(), stream=True → chat_stream()."""
        cached_prompt = await self._build_system_prompt()
        cached_block = await self._build_memory_block(user_input, 0)

        while turn < self.max_turns:
            # ── 超时检查 ──
            timeout = NORMAL_TIMEOUT if self._mode == AgentMode.NORMAL else DEEP_TIMEOUT
            elapsed = asyncio.get_event_loop().time() - self._task_start_time
            if elapsed > timeout:
                yield {"type": "timeout", "mode": self._mode.value, "limit": timeout, "elapsed": elapsed}
                return

            # ── 鲁棒性修护 ──
            # 在每轮 LLM 调用前，确保对话历史符合规范（解决 DeepSeek 孤儿 tool_call 报错）
            await self._repair_history()
            if self._abort.is_set():
                yield {"type": "aborted"}
                return
            if self.compressor.estimate_tokens(self.messages) > 90_000:
                yield {"type": "ctx_warning", "pct": 90}

            # ── 上下文压缩 ──
            if self.compressor.should_compress(self.messages):
                new_messages, was_compressed = await self.compressor.compress(
                    self.messages, memory=self.memory
                )
                if was_compressed:
                    self.messages = new_messages
                    if self.session:
                        await self.session.replace_all(self.messages)
                    yield {"type": "compacted", "message_count": len(self.messages)}
                    cached_prompt = await self._build_system_prompt()

            # ── Memory block ──
            system_prompt = cached_prompt
            if self._turn_count > 0 and self._turn_count % 10 == 0:
                memory_block = await self._build_memory_block(user_input, turn)
            else:
                memory_block = cached_block  # 每轮注入

            # 合并 system prompt + memory block 为一条消息（DeepSeek 不兼容连续 system）
            merged_system = system_prompt
            if memory_block:
                merged_system += "\n\n" + memory_block
            llm_messages = [{"role": "system", "content": merged_system}]
            for m in self.messages:
                copy = dict(m)
                # DeepSeek Pro thinking 模式要求 reasoning_content 必须回传
                # 只有非 DeepSeek 模型才 pop 掉这个非标准字段
                if not self.llm.model.startswith("deepseek/"):
                    copy.pop("reasoning_content", None)
                # 不 pop tool_calls — DeepSeek 需要它匹配后续 tool 消息
                llm_messages.append(copy)

            tools = self.registry.get_definitions()

            # ── LLM 调用 ──
            if stream:
                # 流式：iterate _llm_stream，re-yield events 给前端
                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                tool_calls_list: list[dict] = []
                stream_aborted = False

                async for event in self._llm_stream(llm_messages, tools):
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
                content, reasoning, tool_calls_list = await self._llm_chat(llm_messages, tools)
                if content is None:
                    yield {"type": "error", "content": "LLM call failed"}
                    return

            # ── 构建 assistant 消息 ──
            if stream:
                content = "".join(text_parts)
                reasoning = "".join(reasoning_parts)
            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            self.messages.append(assistant_msg)
            if self.session:
                await self.session.append_message(assistant_msg)

            if not stream:
                if reasoning:
                    yield {"type": "reasoning", "content": reasoning}
                if content:
                    yield {"type": "text_delta", "content": content}

            if not tool_calls_list:
                yield {"type": "completed"}
                asyncio.create_task(on_session_end(self))
                return

            # ── 权限检查 + 工具执行（合并循环）──
            for tc in tool_calls_list:
                if self._abort.is_set():
                    for remaining in tool_calls_list[tool_calls_list.index(tc):]:
                        err_msg = {"role": "tool", "tool_call_id": remaining["id"],
                                   "name": remaining["function"]["name"],
                                   "content": "Interrupted by user"}
                        self.messages.append(err_msg)
                        if self.session:
                            await self.session.append_message(err_msg)
                    yield {"type": "aborted"}
                    return

                tool_name = tc["function"]["name"]
                try:
                    tool_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tool_args = {}
                category = self._classify_permission(tool_name, tool_args)

                if category == PermissionCategory.DANGEROUS:
                    self._permission_granted.clear()
                    yield {
                        "type": "permission_request",
                        "category": "dangerous",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "message": f"DANGEROUS: '{tool_name}' destructive operation. Execute?",
                    }
                    await self._permission_granted.wait()
                    if self._abort.is_set():
                        self.messages.append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "name": tool_name, "content": "Permission denied by user",
                        })
                        if self.session:
                            await self.session.append_message(self.messages[-1])
                        continue

                elif category == PermissionCategory.WRITE and not self._task_write_approved:
                    self._permission_granted.clear()
                    yield {
                        "type": "permission_request",
                        "category": "write",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "message": "Agent wants to write/modify. Allow write operations for this task?",
                    }
                    await self._permission_granted.wait()
                    if self._abort.is_set():
                        for remaining in tool_calls_list[tool_calls_list.index(tc):]:
                            err_msg = {"role": "tool", "tool_call_id": remaining["id"],
                                       "name": remaining["function"]["name"],
                                       "content": "Permission denied"}
                            self.messages.append(err_msg)
                            if self.session:
                                await self.session.append_message(err_msg)
                        yield {"type": "aborted"}
                        return
                    self._task_write_approved = True

                # ── 执行工具（带超时） ──
                yield {"type": "tool_call", "id": tc["id"], "name": tool_name, "args": tool_args}
                try:
                    tool_instance = self.registry.get(tool_name)
                    tool_timeout = getattr(tool_instance, "timeout", 40) if tool_instance else 40
                    result_str = await asyncio.wait_for(
                        self.registry.dispatch(tool_name, tool_args, context=self),
                        timeout=tool_timeout,
                    )
                    # ErrorTracker: check tool result for error patterns
                    _error_indicators = ["Error:", "失败", "Traceback", "exception"]
                    if any(ind in (result_str or "") for ind in _error_indicators):
                        action = await self._handle_tool_error(tool_name, result_str, retry_count=0)
                        if action.get("action") == "report":
                            logger.warning(f"Tool error [{tool_name}]: {action.get('error', '')[:100]}")
                except asyncio.TimeoutError:
                    result_str = f'{{"error": "Tool call timed out after {tool_timeout}s: {tool_name}"}}'
                    logger.warning(f"Tool timeout: {tool_name} exceeded {tool_timeout}s")
                    # ErrorTracker: classify timeout error
                    action = await self._handle_tool_error(tool_name, result_str, retry_count=0)
                    if action.get("action") == "report":
                        logger.warning(f"Tool error [{tool_name}]: {action.get('error', '')[:100]}")
                    yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}
                    self.messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "name": tool_name, "content": result_str,
                    })
                    if self.session:
                        await self.session.append_message(self.messages[-1])
                    continue
                yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}

                asyncio.create_task(audit_tool_call(self, tool_name, tool_args, result_str))

                # v2: 工具结果截断，防止撑爆上下文（最长 5000 字符）
                truncated = result_str[:5000] if len(result_str) > 5000 else result_str
                self.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "name": tool_name, "content": truncated,
                })
                if self.session:
                    await self.session.append_message(self.messages[-1])

            turn += 1
            self._turn_count += 1

            if self._turn_count > 0 and self._turn_count % 10 == 0:
                yield {"type": "nudge", "turn": self._turn_count}

        yield {"type": "max_turns"}
        asyncio.create_task(on_session_end(self))

    async def _llm_chat(self, messages: list[dict], tools: list[dict]) -> tuple:
        """非流式 LLM 调用，返回 (content, reasoning, tool_calls)."""
        try:
            llm_task = asyncio.create_task(
                self.llm.chat(messages=messages, tools=tools if tools else None)
            )
            abort_task = asyncio.create_task(self._abort.wait())
            done, pending = await asyncio.wait(
                {llm_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if self._abort.is_set():
                return None, None, None

            response = llm_task.result()
            self._total_tokens += response.get("tokens_used", 0)
            tc = response.get("tool_calls")
            return response["content"], response.get("reasoning_content"), tc if tc else []
        except Exception:
            return None, None, None  # sentinel for error

    async def _llm_stream(self, messages: list[dict], tools: list[dict]) -> AsyncGenerator[dict, None]:
        """流式 LLM 调用，yield UI events，最后 yield _done 事件."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []

        yield {"type": "exploring_start", "ts": asyncio.get_event_loop().time()}
        first_token = True

        try:
            async for event in self.llm.chat_stream(
                messages=messages,
                tools=tools if tools else None,
                abort_event=self._abort,
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

    def set_mode(self, mode: AgentMode) -> None:
        self._mode = mode

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def approve_permission(self) -> None:
        self._permission_granted.set()

    def deny_permission(self) -> None:
        self._abort.set()
        self._permission_granted.set()

    def abort(self):
        self._abort.set()
        self._permission_granted.set()

    def clear_history(self):
        self.messages.clear()

    # ── 权限分类 ────────────────────────────────────────────────

    def _classify_permission(self, tool_name: str, tool_args: dict) -> PermissionCategory:
        tool = self.registry.get(tool_name)
        if tool is None:
            return PermissionCategory.SAFE
        if not tool.needs_permissions(tool_args):
            return PermissionCategory.SAFE
        if tool_name == "bash":
            from .tools.bash_tool import BashTool
            category_str = BashTool.classify_command(tool_args.get("command", ""))
            return {
                "safe": PermissionCategory.SAFE,
                "write": PermissionCategory.WRITE,
                "dangerous": PermissionCategory.DANGEROUS,
            }[category_str]
        return PermissionCategory.WRITE

    # ── internal ───────────────────────────────────────────────

    async def _extract_keywords(self, user_input: str) -> list[str]:
        """分词提取关键词（v4: 无 LLM，中英 bigram + 词分割）."""
        if len(user_input) < 10:
            return []
        try:
            import re
            text = user_input.lower().strip()
            stopwords = {
                '的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都',
                '一个', '这个', '那个', '你', '吗', '呢', '吧', '啊', '嗯', '哦',
                'the', 'a', 'an', 'is', 'are', 'was', 'were', 'to', 'of', 'in',
                'for', 'and', 'or', 'it', 'that', 'this', 'what', 'how', 'can',
            }
            # 英文词：空格分割
            en_words = re.findall(r'[a-z]{2,}', text)
            # 中文 bigram：连续 2 个中文字符
            zh_chars = re.findall(r'[\u4e00-\u9fff]', text)
            zh_bigrams = []
            for i in range(len(zh_chars) - 1):
                bigram = zh_chars[i] + zh_chars[i + 1]
                if bigram not in stopwords:
                    zh_bigrams.append(bigram)
            words = en_words + zh_bigrams
            seen, result = set(), []
            for w in words:
                if w not in seen:
                    seen.add(w)
                    result.append(w)
                    if len(result) >= 5:
                        break
            return result[:5]
        except Exception:
            return []

    async def _build_system_prompt(self) -> str:
          """组装 system prompt = 静态段(含动态人格自画像) + 当前上下文 + 自进化规则."""
          import json
          
          # 动态加载并拼装人格自画像
          prof = {}
          persona_section = ""
          profile_file = self.memory.base_dir / "persona_profile.json"
          if profile_file.exists():
              try:
                  prof = json.loads(profile_file.read_text(encoding="utf-8"))
                  pref_lines = "\n".join([f"- {p}" for p in prof.get("preferences", [])])
                  avoid_lines = "\n".join([f"- {a}" for a in prof.get("avoid_list", [])])
                  persona_section = (
                      f"## 你的人格自画像设定 (Your Persona Profile)\n"
                      f"- 你的名字: {prof.get('name', '小萤')}\n"
                      f"- 你的性别: {prof.get('gender', '女')}\n"
                      f"- 你称呼对方: {prof.get('user_address', '亮哥')}\n"
                      f"- 你的说话语气特质: {prof.get('tone_style', '')}\n"
                      f"- 你的行为偏好:\n{pref_lines}\n"
                      f"- 你绝不触碰的雷区:\n{avoid_lines}\n"
                  )
              except Exception as e:
                  logger.error(f"Failed to parse persona_profile: {e}")
          
          static_p = STATIC_PROMPT.replace("{persona_section}", persona_section)
          # 动态渲染人格属性到静态提示词模板
          _user_address = prof.get("user_address", "亮哥")
          try:
              static_p = static_p.format(user_address=_user_address)
          except (KeyError, ValueError) as e:
              logger.warning(f"STATIC_PROMPT format failed, using raw: {e}")
          
          from datetime import timezone, timedelta
          beijing_tz = timezone(timedelta(hours=8))
          now = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S (北京时间)")
          cwd = os.getcwd()
          dynamic = (
              f"\n\n---\n"
              f"## Current Context\n"
              f"Time: {now}\n"
              f"Working directory: {cwd}\n"
          )
          # 注入自进化规则
          rules_file = self.memory.base_dir / "EVOLVED_RULES.md"
          if rules_file.exists():
              rules = rules_file.read_text(encoding="utf-8").strip()
              if rules:
                  dynamic += f"\n## Self-Evolved Rules (learned from past corrections)\n{rules}\n"
          return static_p + dynamic

    async def _build_memory_block(self, user_input: str, turn: int) -> Optional[str]:
        """构建 [MEMORY BLOCK] — FTS5 BM25 + 上下文增强 + 规则重排.

        v6: 三合一优化
          1. 上下文增强：从最近对话提取关键词拼入 query
          2. 召回扩大：memory limit 5→20, notes limit 2→5
          3. 规则重排：type 优先级 + recency 二次排序
        """
        # ── 上下文增强：取最近2轮用户消息提取关键词 ──
        context_keywords = ""
        user_msgs = [m.get("content", "") for m in self.messages[-6:]
                     if m.get("role") == "user" and m.get("content")]
        recent_user_msgs = user_msgs[-2:]  # 最近2轮
        if recent_user_msgs:
            keywords = []
            for msg in recent_user_msgs:
                words = _KEYWORD_RE.findall(msg)
                keywords.extend(words[:6])
            context_keywords = " ".join(keywords[:12])

        # v6: 上下文增强 query
        enhanced_query = f"{context_keywords} {user_input}".strip() if context_keywords else user_input

        # v6: 扩大召回 → Top-20
        search_results = self.memory.search_memories(enhanced_query, limit=20)
        if search_results:
            relevant = []
            seen_fnames = set()
            for r in search_results:
                fname = r.get("filename", "")
                if fname and fname not in seen_fnames:
                    seen_fnames.add(fname)
                    relevant.append(r)
                if len(relevant) >= 20:
                    break

            # v6: 规则重排 — type 优先级 + 时间降序
            # 先按时间倒序排（稳定排序），再按 type 优先级排（同 type 内保持时间序）
            relevant.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
            relevant.sort(key=lambda r: _TYPE_PRIORITY.get(
                str(r.get("memory_type", "")).split("/")[0].strip().lower(), 4
            ))
            relevant = relevant[:5]  # 重排后取 Top-5 注入
        else:
            # Fallback: FTS5 无结果时，解析 index 按时间倒序取 5 条
            entries = self.memory._parse_index()
            if not entries:
                relevant = []
            else:
                entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
                relevant = [{"description": e["description"], "filename": e["filename"],
                             "timestamp": e.get("timestamp", ""), "content": ""} for e in entries[:5]]

        lines = ["[MEMORY BLOCK]"]
        lines.append("以下是你此前保存的长期记忆（来源: 个人记忆）。")
        lines.append("")

        for i, e in enumerate(relevant):
            ts = e.get("timestamp", "")[:19]
            if i < 1:  # v5: 优先用 FTS5 缓存的 content，省磁盘 IO
                cached = e.get("content", "")
                if cached:
                    clean = cached.split("<!-- previous version -->")[0]
                    clean = clean.split("<!-- updated:")[0].strip()[:400]
                    lines.append(f"### {e['description']} ({ts})\n{clean}\n")
                else:
                    # Fallback: 读文件
                    content = await self.memory.get_entry(e["filename"])
                    if content:
                        clean = content.split("<!-- previous version -->")[0]
                        clean = clean.split("<!-- updated:")[0].strip()[:400]
                        lines.append(f"### {e['description']} ({ts})\n{clean}\n")
                    else:
                        lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")
            else:
                lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")

        # v5: 只在长输入时追回笔记知识库（短输入如打招呼不需要）
        try:
            note_results = []
            if len(user_input) > 20:  # 短输入跳过 RAG，省 token
                note_results = self.memory.search_notes(enhanced_query, limit=5)
            if note_results:
                lines.append("")
                lines.append("## 相关知识（来源: 学习笔记）")
                for nr in note_results:
                    snippet = nr.get("content", "")[:120].replace("\n", " ")
                    cite = nr.get("path", "") or nr.get("title", "?")
                    lines.append(f"- 📖 [{nr.get('title','?')}]({cite}) — {snippet}")
                
                # --- Layer 4: 链接摘要注入（每 20 轮刷新一次） ---
                note_paths = list(set([nr.get("path") for nr in note_results if nr.get("path")]))
                if note_paths and self._turn_count % 20 == 0:
                    from .memory.notes_fts import get_link_summaries
                    link_summaries = await get_link_summaries(note_paths, self.llm)
                    if link_summaries:
                        lines.append("")
                        lines.append("## 关联外链摘要（来源: 笔记链接）")
                        lines.append(link_summaries)
                # -----------------------------

            # v7: 跨会话搜索 — 从历史聊天记录中检索相关内容
            if self.session and len(user_input) > 20:
                try:
                    from agent.session.handler import SessionHandler
                    past = await self.session.search_all_sessions(
                        user_input, self.llm, max_results=3
                    )
                    if past and "No past conversations" not in past:
                        lines.append("")
                        lines.append("## 相关历史对话（来源: 跨会话检索）")
                        lines.append(past[:500])
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error in RAG/Layer4 injection: {e}")

        if self._turn_count > 0 and self._turn_count % 10 == 0:
            lines.append("")
            lines.append("⚠️ Periodic Nudge: 已对话多轮。请检查是否有值得长期记住的内容。")

        lines.append("[/MEMORY BLOCK]")
        block = "\n".join(lines)

        max_chars = 2000  # 从 3000 降到 2000（省 token）
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (truncated)\n[/MEMORY BLOCK]"

        return block
