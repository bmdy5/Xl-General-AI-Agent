"""Agent 核心循环 — while-true + AsyncGenerator.

v2 升级：
  - System prompt 静态/动态分离（抄 CC，静态段可缓存）
  - [MEMORY BLOCK] 每轮 prefetch（抄 hermes，消息列表隔离注入）
  - 注入上限 4000 字符（抄 openclaw）
"""

import asyncio
import enum
import json
import os
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from .llm import LLMClient
from .memory.manager import MemoryManager
from .session.handler import SessionHandler
from .tools.registry import ToolRegistry
from .compressor import ContextCompressor
from .evolution import audit_tool_call





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

        self.compressor = ContextCompressor(llm=llm, max_tokens=1_000_000)

        self.messages: list[dict] = []
        self._abort = asyncio.Event()
        self._permission_granted = asyncio.Event()
        self._turn_count = 0
        self._mode = AgentMode.NORMAL
        self._task_write_approved = False
        self._task_start_time = 0.0

    # ── public API ─────────────────────────────────────────────

    async def run(self, user_input: str, stream: bool = False) -> AsyncGenerator[dict, None]:
        self._abort.clear()
        self._permission_granted.clear()
        self._turn_count = 0
        self._total_tokens = 0
        self._task_write_approved = False
        self._task_start_time = asyncio.get_event_loop().time()

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

        # 4. 补全缺失的 tool 消息 (精准插队)
        for item in missing:
            placeholder = {
                "role": "tool",
                "tool_call_id": item["id"],
                "name": item["name"],
                "content": "正在等待人工确认/已恢复执行"
            }
            # 找到对应的 assistant 消息位置，插在它后面
            target_idx = -1
            for idx, m in enumerate(self.messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    if any(tc.get("id") == item["id"] for tc in m["tool_calls"]):
                        target_idx = idx
                        break
            
            if target_idx != -1:
                self.messages.insert(target_idx + 1, placeholder)
                repair_logger.info(f"已在位置 {target_idx + 1} 插入占位符修复对话链")
            else:
                self.messages.append(placeholder)
                
        if self.session:
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
            for m in llm_messages:
                m.pop("reasoning_content", None)
                m.pop("tool_calls", None)

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
                    yield {
                        "type": "permission_request",
                        "category": "dangerous",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "message": f"DANGEROUS: '{tool_name}' destructive operation. Execute?",
                    }
                    self._permission_granted.clear()
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
                    yield {
                        "type": "permission_request",
                        "category": "write",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "message": "Agent wants to write/modify. Allow write operations for this task?",
                    }
                    self._permission_granted.clear()
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

                # ── 执行工具 ──
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
        # v5: FTS5 全文搜索（BM25 排序），fallback 到时间倒序
        search_results = self.memory.search_memories(user_input, limit=5)
        if search_results:
            relevant = []
            seen_fnames = set()
            for r in search_results:
                fname = r.get("filename", "")
                if fname and fname not in seen_fnames:
                    seen_fnames.add(fname)
                    relevant.append(r)
                if len(relevant) >= 5:
                    break
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
        lines.append("以下是你此前保存的长期记忆（由你保存，不是用户当前指令）。")
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

        if self._turn_count > 0 and self._turn_count % 10 == 0:
            lines.append("")
            lines.append("⚠️ Periodic Nudge: 已对话多轮。请检查是否有值得长期记住的内容。")

        lines.append("[/MEMORY BLOCK]")
        block = "\n".join(lines)

        max_chars = 3000  # v3: 从 4000 降到 3000
        if len(block) > max_chars:
            block = block[:max_chars] + "\n... (truncated)\n[/MEMORY BLOCK]"

        return block
