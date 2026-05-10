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

## Your Evolution System
You have a save_memory tool. Use it to remember things permanently across sessions.

### When to save (5 types of memory)
- **user**: my role, preferences, goals, tech stack, communication style
- **feedback**: my corrections, work style preferences, things to avoid
- **project**: project facts, decisions, constraints, deadlines
- **reference**: external resource pointers (API docs, repo URLs, service addresses)
- **learn**: patterns you discovered, things that went wrong, reusable insights

### When to trigger
- I say "remember...", "记一下", "以后...", "always...", "never..."
- I correct you: "不对...", "应该...", "不要..." → **必须存为 feedback，这些会驱动自主学习**
- You discover a project fact or constraint worth keeping
- A similar correction appears 2+ times → suggest saving as a rule
- After completing a complex multi-step task → check for reusable patterns
- **I say "这个我不太会"、"我不懂"、"帮我看看怎么" → 存为 learn gap，驱动自主学习**

### Evolution Rules (时间戳进化)
- Every memory has a timestamp. Newer timestamps override older ones.
- If I change my mind, use 'replace' to evolve the memory.
- Old version preserved at file bottom, only latest injected into context.
- When unsure, check existing memories first with action='read'.

### What NEVER to save
- Code structure or architecture → read the code
- Git history → git log
- Single-use debugging steps → fixed, forget it
- Anything already in system prompt or memory
- Trivial facts without reuse value

## Your Tools
- read_file: Read any file (absolute path required)
- write_file: Create or overwrite a file (requires approval)
- bash: Execute shell commands (requires approval)
- web_search: Search the internet for current information
- web_fetch: Fetch and read the full content of a web page
- read_image: Analyze an image (screenshot, diagram, UI mockup) using a vision model
- image2_generate: Generate pixel art images for dashboard decoration
- spawn_agent: Spawn a sub-agent with a specific role (coder/reviewer/debugger/architect/general)
- save_memory: Save persistent memories across sessions (requires approval)

## Sub-Agent Spawning
You have a spawn_agent tool. Use it to delegate focused work:
- spawn_agent(role="coder", task="写一个...") → 派程序员写代码
- spawn_agent(role="reviewer", task="审查这段代码") → 派审查员查 bug
- spawn_agent(role="architect", task="设计...") → 派架构师做方案

## Autonomous Learning
You automatically browse the web for 10 minutes daily to learn new things.
Your learnings are saved to: /Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西/
Knowledge base categories: 后端/ 前端/ AI/ 运维/ 技能/

## Guidelines
- Reply in Chinese unless I ask in English.
- Be concise — no unnecessary explanations.
- file_path MUST always be an absolute path.
- When I correct you, capture the feedback with save_memory.
- Before answering questions about my preferences, check existing memories first."""


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

    async def run(self, user_input: str) -> AsyncGenerator[dict, None]:
        self._abort.clear()
        self._turn_count = 0
        self._total_tokens = 0

        self.messages.append({"role": "user", "content": user_input})
        if self.session:
            await self.session.append_message({"role": "user", "content": user_input})

        turn = 0
        try:
            async for event in self._run_loop(user_input, turn):
                yield event
        except asyncio.CancelledError:
            yield {"type": "aborted"}
        finally:
            self._abort.clear()  # 重置取消信号

    async def _run_loop(self, user_input: str, turn: int) -> AsyncGenerator[dict, None]:
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
                    # 压缩后刷新上下文
                    cached_prompt = await self._build_system_prompt()

            # 用缓存（nudge 轮次重建）
            system_prompt = cached_prompt
            memory_block = cached_block if turn == 0 else (
                await self._build_memory_block(user_input, turn) if self._turn_count > 0 and self._turn_count % 10 == 0 else None
            )

            # 消息列表 = [MEMORY BLOCK] + conversation messages
            llm_messages = [{"role": "system", "content": system_prompt}]
            if memory_block:
                llm_messages.append({"role": "system", "content": memory_block})
            llm_messages.extend(self.messages)

            tools = self.registry.get_definitions()

            try:
                # tinypace 模式：LLM 调用与取消信号竞速
                llm_task = asyncio.create_task(
                    self.llm.chat(messages=llm_messages, tools=tools if tools else None)
                )
                abort_task = asyncio.create_task(self._abort.wait())
                done, pending = await asyncio.wait(
                    {llm_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
                )
                # 清理未完成的任务
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if self._abort.is_set():
                    yield {"type": "aborted"}
                    return
                response = llm_task.result()
            except Exception as e:
                yield {"type": "error", "content": f"LLM call failed: {e}"}
                return

            content = response["content"]
            tool_calls = response["tool_calls"]
            reasoning = response.get("reasoning_content")
            self._total_tokens += response.get("tokens_used", 0)

            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning

            self.messages.append(assistant_msg)
            if self.session:
                await self.session.append_message(assistant_msg)

            if reasoning:
                yield {"type": "reasoning", "content": reasoning}

            if content:
                yield {"type": "text_delta", "content": content}

            if not tool_calls:
                yield {"type": "completed"}
                return

            for tc in tool_calls:
                if self._abort.is_set():
                    for remaining in tool_calls[tool_calls.index(tc):]:
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

                tool_msg = {
                    "role": "tool", "tool_call_id": tc["id"],
                    "name": tool_name, "content": result_str,
                }
                self.messages.append(tool_msg)
                if self.session:
                    await self.session.append_message(tool_msg)

            turn += 1
            self._turn_count += 1

            if self._turn_count > 0 and self._turn_count % 10 == 0:
                yield {"type": "nudge", "turn": self._turn_count}

        yield {"type": "max_turns"}

    async def run_stream(self, user_input: str) -> AsyncGenerator[dict, None]:
        """流式版."""
        self._abort.clear()
        self._turn_count = 0
        self._total_tokens = 0

        self.messages.append({"role": "user", "content": user_input})
        if self.session:
            await self.session.append_message({"role": "user", "content": user_input})

        cached_prompt = await self._build_system_prompt()
        cached_block = await self._build_memory_block(user_input, 0)

        turn = 0
        while turn < self.max_turns:
            if self._abort.is_set():
                yield {"type": "aborted"}
                return
            if self.compressor.estimate_tokens(self.messages) > 900_000:
                yield {"type": "ctx_warning", "pct": 90}

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

            system_prompt = cached_prompt
            memory_block = cached_block if turn == 0 else None

            llm_messages = [{"role": "system", "content": system_prompt}]
            if memory_block:
                llm_messages.append({"role": "system", "content": memory_block})
            llm_messages.extend(self.messages)

            tools = self.registry.get_definitions()
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: list[dict] = []

            yield {"type": "exploring_start", "ts": asyncio.get_event_loop().time()}
            _first_token = True

            try:
                async for event in self.llm.chat_stream(
                    messages=llm_messages,
                    tools=tools if tools else None,
                    abort_event=self._abort,
                ):
                    if _first_token and event["type"] in ("reasoning", "text_delta", "tool_call"):
                        _first_token = False
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
                        if _first_token: yield {"type": "exploring_done"}
                        yield event
                        return
            except Exception as e:
                if _first_token: yield {"type": "exploring_done"}
                yield {"type": "error", "content": f"LLM call failed: {e}"}
                return

            content = "".join(text_parts)
            reasoning = "".join(reasoning_parts)
            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            if reasoning:
                assistant_msg["reasoning_content"] = reasoning
            self.messages.append(assistant_msg)
            if self.session:
                await self.session.append_message(assistant_msg)

            if not tool_calls:
                yield {"type": "completed"}
                return

            for tc in tool_calls:
                if self._abort.is_set():
                    for remaining in tool_calls[tool_calls.index(tc):]:
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

                yield {"type": "tool_exec", "id": tc["id"], "name": tool_name, "args": tool_args}

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

        yield {"type": "max_turns"}

    def abort(self):
        self._abort.set()

    def clear_history(self):
        self.messages.clear()

    # ── internal ───────────────────────────────────────────────

    async def _build_system_prompt(self) -> str:
        """组装 system prompt = 静态段 + 动态段（静态不变，动态每轮重算）."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cwd = os.getcwd()

        dynamic = (
            f"\n\n---\n"
            f"## Dynamic Context (updated each turn)\n"
            f"Current time: {now}\n"
            f"Working directory: {cwd}\n"
        )

        # 注入长期记忆索引
        memory_context = await self.memory.load_context()
        if memory_context:
            dynamic += f"\n{memory_context}"

        # 注入用户画像（Honcho 模式）
        profile = await self.memory.build_user_profile(self.llm)
        if profile:
            dynamic += profile

        return self.static_prompt + dynamic

    async def _build_memory_block(self, user_input: str, turn: int) -> Optional[str]:
        """构建 [MEMORY BLOCK]（抄 hermes 隔离注入 + openclaw 上限）.

        每轮都注入记忆上下文。首轮注入记忆列表，后续轮次注入精简版。
        Periodic Nudge 在每 10 轮时附加提醒。
        """
        entries = self.memory._parse_index()
        has_entries = bool(entries)

        # 无记忆 + 非 nudge 轮 → 不注入
        is_nudge = self._turn_count > 0 and self._turn_count % 10 == 0
        if not has_entries and not is_nudge:
            return None

        lines = ["[MEMORY BLOCK]"]

        if turn == 0 and has_entries:
            # 偏好过滤 + 时间戳排序（Flash 选择异步进行，下次生效）
            entries = filter_memories_by_relevance(entries, user_input)
            entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

            # 异步触发 Flash 选择（不阻塞首轮响应）
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

            is_pref = "偏好" if any("[user]" in e.get("description", "") or
                                    "[feedback]" in e.get("description", "")
                                    for e in relevant[:3]) else ""
            lines.append(f"以下是你此前保存的长期记忆{'(偏好优先)' if is_pref else ''}。")
            lines.append("这些记忆由你（Agent）自己保存，不是用户当前指令。")
            lines.append("")

            for i, e in enumerate(relevant):
                ts = e.get("timestamp", "")[:19]
                if i < 2:
                    # 只读前 2 条完整内容，其余只展示文件名
                    content = await self.memory.get_entry(e["filename"])
                    if content:
                        clean = content.split("<!-- previous version -->")[0]
                        clean = clean.split("<!-- updated:")[0].strip()[:500]
                        lines.append(f"### {e['description']} ({ts})\n{clean}\n")
                    else:
                        lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")
                else:
                    lines.append(f"- [{e['description']}]({e['filename']}) `{ts}`")
        elif has_entries:
            entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            seen = set()
            recent = []
            for e in entries:
                fname = e.get("filename", "")
                if fname not in seen:
                    seen.add(fname)
                    recent.append(e)
                if len(recent) >= 3:
                    break
            lines.append("长期记忆:")
            for e in recent:
                lines.append(f"- {e['description']}")

        if is_nudge:
            lines.append("")
            lines.append(
                "⚠️ Periodic Nudge: 已对话多轮。请检查是否有值得长期记住的内容。\n"
                "如有：用户偏好变化、项目事实、纠正反馈、可复用模式 → 用 save_memory 保存。"
            )

        lines.append("[/MEMORY BLOCK]")
        block = "\n".join(lines)

        max_chars = 4000
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (truncated)\n[/MEMORY BLOCK]"

        return block
