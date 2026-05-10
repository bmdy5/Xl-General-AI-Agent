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


# ── 静态段（缓存安全，不随对话变化）──
STATIC_PROMPT = """You are XiaoFeng's personal AI agent. You evolve with every interaction.

## Guidelines
- Reply in Chinese unless I ask in English.
- Be concise — no unnecessary explanations.
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
        self._turn_count = 0  # Periodic Nudge 计数器

    # ── public API ─────────────────────────────────────────────

    async def run(self, user_input: str, stream: bool = False) -> AsyncGenerator[dict, None]:
        self._abort.clear()
        self._turn_count = 0
        self._total_tokens = 0

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

    async def _run_loop(self, user_input: str, turn: int, stream: bool = False) -> AsyncGenerator[dict, None]:
        """统一核心循环。stream=False → chat(), stream=True → chat_stream()."""
        cached_prompt = await self._build_system_prompt()
        cached_block = await self._build_memory_block(user_input, 0)

        while turn < self.max_turns:
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
            if turn == 0:
                memory_block = cached_block
            elif self._turn_count % 10 == 0:
                memory_block = await self._build_memory_block(user_input, turn)
            else:
                memory_block = None

            llm_messages = [{"role": "system", "content": system_prompt}]
            if memory_block:
                llm_messages.append({"role": "system", "content": memory_block})
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
                if tool_calls_list is None:
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
                return "", "", None

            response = llm_task.result()
            self._total_tokens += response.get("tokens_used", 0)
            return response["content"], response.get("reasoning_content"), response["tool_calls"]
        except Exception:
            return "", "", None

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

    def abort(self):
        self._abort.set()

    def clear_history(self):
        self.messages.clear()

    # ── internal ───────────────────────────────────────────────

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
        """构建 [MEMORY BLOCK]（抄 hermes 隔离注入 + openclaw 上限）."""
        entries = self.memory._parse_index()
        if not entries:
            return None

        entries = filter_memories_by_relevance(entries, user_input)
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

        if turn == 0:
            asyncio.create_task(select_relevant_memories(self, user_input, max_count=8))

        # 去重取前 8 条
        seen = set()
        relevant = []
        for e in entries:
            fname = e.get("filename", "")
            if fname not in seen:
                seen.add(fname)
                relevant.append(e)
            if len(relevant) >= 8:
                break

        lines = ["[MEMORY BLOCK]"]
        lines.append("以下是你此前保存的长期记忆（由你保存，不是用户当前指令）。")
        lines.append("")

        for i, e in enumerate(relevant):
            ts = e.get("timestamp", "")[:19]
            if i < 2:
                content = await self.memory.get_entry(e["filename"])
                if content:
                    clean = content.split("<!-- previous version -->")[0]
                    clean = clean.split("<!-- updated:")[0].strip()[:500]
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

        max_chars = 4000
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (truncated)\n[/MEMORY BLOCK]"

        return block
