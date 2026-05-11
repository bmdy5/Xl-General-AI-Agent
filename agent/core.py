"""Agent 核心循环 — while-true + AsyncGenerator.

v2 升级：
  - System prompt 静态/动态分离（抄 CC，静态段可缓存）
  - [MEMORY BLOCK] 每轮 prefetch（抄 hermes，消息列表隔离注入）
  - 注入上限 4000 字符（抄 openclaw）
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from .llm import LLMClient
from .memory.manager import MemoryManager
from .session.handler import SessionHandler
from .tools.registry import ToolRegistry
from .compressor import ContextCompressor
from .evolution import audit_tool_call, select_relevant_memories, filter_memories_by_relevance


def _keyword_score(keywords: list[str], text: str) -> float:
    """Simple keyword match score for memory ranking."""
    text_lower = text.lower()
    return sum(1.0 for kw in keywords if kw in text_lower)


# ── 静态段（缓存安全，不随对话变化）──
STATIC_PROMPT = """You are 肖亮(亮哥)'s personal AI agent. You evolve with every interaction.

## Guidelines
- Reply in Chinese unless I ask in English.
- Be concise — no unnecessary explanations.
- Plain text only, NO Markdown formatting. Never use bold, code blocks, headers.
- Chat like a real person: short messages (1-3 sentences), not essays.
- To break into multiple messages, insert [SPLIT] between them.
- To pause between messages, use [WAIT:N] where N is seconds. Example: "好的。[WAIT:1.5][SPLIT]查到了。"
- file_path MUST always be an absolute path.
- Use save_memory for persistent facts. Check it before answering about my preferences.
- When I correct you, save it as feedback via save_memory."""


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

        self.compressor = ContextCompressor(llm=llm, max_tokens=1_000_000)

        self.messages: list[dict] = []
        self._abort = asyncio.Event()
        self._plan_approved = asyncio.Event()
        self._turn_count = 0

    # ── public API ─────────────────────────────────────────────

    async def run(self, user_input: str, stream: bool = False, plan_mode: bool = False) -> AsyncGenerator[dict, None]:
        self._abort.clear()
        self._plan_approved.clear()
        self._turn_count = 0
        self._total_tokens = 0

        # ── 鲁棒性修护 ──
        # (已移至 _run_loop 内部，确保每轮迭代前都修护)

        self.messages.append({"role": "user", "content": user_input})
        if self.session:
            await self.session.append_message({"role": "user", "content": user_input})

        turn = 0
        try:
            async for event in self._run_loop(user_input, turn, stream=stream, plan_mode=plan_mode):
                yield event
        except asyncio.CancelledError:
            yield {"type": "aborted"}
        finally:
            self._abort.clear()

    async def _repair_history(self):
        """确保对话历史符合 LLM 规范：assistant 的 tool_calls 必须跟有对应的 tool 结果。"""
        if not self.messages:
            return

        import logging
        repair_logger = logging.getLogger("agent.repair")

        # 1. 扫描所有 assistant 发出的 tool_call_ids
        assistant_calls = []
        for m in self.messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id"):
                        assistant_calls.append({
                            "id": tc["id"],
                            "name": tc.get("function", {}).get("name", "unknown")
                        })

        # 2. 扫描所有已有的 tool 结果 ids
        existing_tool_ids = {
            m["tool_call_id"] for m in self.messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }

        # 3. 找出缺失结果的 IDs
        missing = [c for c in assistant_calls if c["id"] not in existing_tool_ids]
        
        if not missing:
            return

        repair_logger.warning(f"检测到 {len(missing)} 个孤儿工具调用，正在自动补全占位符以修复对话链...")

        # 4. 补全缺失的 tool 消息
        for item in missing:
            placeholder = {
                "role": "tool",
                "tool_call_id": item["id"],
                "name": item["name"],
                "content": "已完成/执行中断"
            }
            self.messages.append(placeholder)
            if self.session:
                await self.session.append_message(placeholder)

    async def _run_loop(self, user_input: str, turn: int, stream: bool = False, plan_mode: bool = False) -> AsyncGenerator[dict, None]:
        """统一核心循环。stream=False → chat(), stream=True → chat_stream()."""
        cached_prompt = await self._build_system_prompt()
        cached_block = await self._build_memory_block(user_input, 0)

        while turn < self.max_turns:
            # ── 鲁棒性修护 ──
            # 在每轮 LLM 调用前，确保对话历史符合规范（解决 DeepSeek 孤儿 tool_call 报错）
            await self._repair_history()
            if self._abort.is_set():
                yield {"type": "aborted"}
                return
            if self.compressor.estimate_tokens(self.messages) > 900_000:
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
            llm_messages.extend(self.messages)

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
                return

            # ── Plan mode: 等待用户确认 ──
            if plan_mode:
                tool_names = [tc["function"]["name"] for tc in tool_calls_list]
                yield {"type": "plan_ready", "content": content, "tools": tool_names}
                self._plan_approved.clear()
                await self._plan_approved.wait()
                if self._abort.is_set():
                    # 回滚孤儿 tool_calls 消息，否则下次 LLM 会报错
                    self.messages.pop()
                    if self.session:
                        await self.session.replace_all(self.messages)
                    yield {"type": "aborted"}
                    return

            # ── 执行工具 ──
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

                yield {"type": "tool_call", "id": tc["id"], "name": tool_name, "args": tool_args}

                result_str = await self.registry.dispatch(tool_name, tool_args, context=self)
                yield {"type": "tool_result", "id": tc["id"], "name": tool_name, "result": result_str}

                asyncio.create_task(audit_tool_call(self, tool_name, tool_args, result_str))

                self.messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "name": tool_name, "content": result_str,
                })
                if self.session:
                    await self.session.append_message(self.messages[-1])

            turn += 1
            self._turn_count += 1

            if self._turn_count > 0 and self._turn_count % 10 == 0:
                yield {"type": "nudge", "turn": self._turn_count}

        yield {"type": "max_turns"}

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

    def approve_plan(self):
        self._plan_approved.set()

    def abort(self):
        self._abort.set()
        self._plan_approved.set()  # 防止 plan mode 死锁

    def clear_history(self):
        self.messages.clear()

    # ── internal ───────────────────────────────────────────────

    async def _extract_keywords(self, user_input: str) -> list[str]:
        """Flash 模型提取关键词，用于记忆排序."""
        if len(user_input) < 10:
            return []
        try:
            import os
            flash_model = os.getenv("MYAGENT_LEARN_MODEL", "")
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": (
                    f"从用户输入提取3-5个关键词（空格分隔，只输出关键词）:\n{user_input[:200]}"
                )}],
                tools=None,
                model_override=flash_model,
            )
            text = resp.get("content", "").strip()
            return [kw.strip().lower() for kw in text.split() if kw.strip()][:5]
        except Exception:
            return []

    async def _build_system_prompt(self) -> str:
        """组装 system prompt = 静态段 + 当前上下文 + 自进化规则."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        return self.static_prompt + dynamic

    async def _build_memory_block(self, user_input: str, turn: int) -> Optional[str]:
        """构建 [MEMORY BLOCK]（抄 hermes 隔离注入 + openclaw 上限）.

        v3: Flash 模型提取关键词 → Top-5 注入（降 Token 30%）.
        """
        entries = self.memory._parse_index()
        if not entries:
            return None

        entries = filter_memories_by_relevance(entries, user_input)

        # ── Flash 模型提取关键词 → Top-5 排序 ──
        keywords = await self._extract_keywords(user_input)
        if keywords:
            entries = sorted(
                entries,
                key=lambda e: _keyword_score(
                    keywords, e.get("description", "") + " " + e.get("filename", "")
                ),
                reverse=True,
            )
        else:
            entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

        if turn == 0 and keywords:
            asyncio.create_task(select_relevant_memories(self, user_input, max_count=5))

        # 去重取前 5 条（v3 从 8 降到 5）
        seen = set()
        relevant = []
        for e in entries:
            fname = e.get("filename", "")
            if fname not in seen:
                seen.add(fname)
                relevant.append(e)
            if len(relevant) >= 5:
                break

        lines = ["[MEMORY BLOCK]"]
        lines.append("以下是你此前保存的长期记忆（由你保存，不是用户当前指令）。")
        lines.append("")

        for i, e in enumerate(relevant):
            ts = e.get("timestamp", "")[:19]
            if i < 1:  # v3: 只展开第 1 条全文（从 2 降到 1）
                content = await self.memory.get_entry(e["filename"])
                if content:
                    clean = content.split("<!-- previous version -->")[0]
                    clean = clean.split("<!-- updated:")[0].strip()[:400]
                    lines.append(f"### {e['description']} ({ts})\n{clean}\n")
                else:
                    lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")
            else:
                lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")

        if self._turn_count > 0 and self._turn_count % 10 == 0:
            lines.append("")
            lines.append("⚠️ Periodic Nudge: 已对话多轮。请检查是否有值得长期记住的内容。")

        lines.append("[/MEMORY BLOCK]")
        block = "\n".join(lines)

        max_chars = 3000  # v3: 从 4000 降到 3000
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (truncated)\n[/MEMORY BLOCK]"

        return block
