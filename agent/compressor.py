"""上下文压缩 — 组合 CC + tinypace + openclaw 三家设计.

CC 贡献：熔断器（3次失败停止）+ 断点保护
tinypace 贡献：Head/Tail 分割 + LLM 摘要
openclaw 贡献：压缩前记忆刷新（pre-compression memory flush）
"""

import logging
import time

logger = logging.getLogger(__name__)

# 结构化摘要模板（抄 hermes）
SUMMARY_PROMPT = """请对以下对话历史进行精简压缩，使用以下格式：

任务：当前正在做什么
约束：有哪些限制条件
已完成：完成了哪些步骤和结果
当前状态：进展到什么阶段
关键决策：做了哪些重要决定
用户偏好：用户表达了哪些偏好或纠正

待压缩对话：
{conversation}

只输出上述格式的摘要，不要输出其他内容。"""


class ContextCompressor:
    """上下文压缩器.

    用法：
        comp = ContextCompressor(llm_client, max_tokens=128000)
        if comp.should_compress(messages):
            messages, was_compressed = await comp.compress(messages, memory)
    """

    def __init__(
        self,
        llm,  # LLMClient instance
        max_tokens: int = 128000,
        threshold: float = 0.75,
        max_summary_tokens: int = 1000,
    ):
        self.llm = llm
        self.max_tokens = max_tokens
        self.threshold = threshold
        self.compress_at = int(max_tokens * threshold)
        self.max_summary_tokens = max_summary_tokens

        # 熔断器（抄 CC）
        self._consecutive_failures = 0
        self._circuit_open = False
        self._max_failures = 3

        # 压缩冷却：避免连续多轮反复压缩
        self._last_compress_turn = -10

    def estimate_tokens(self, messages: list[dict]) -> int:
        """混合 token 估算：中文~2字/token，英文~4字/token."""
        total = 0
        for m in messages:
            content = str(m.get("content", ""))
            cjk = sum(1 for c in content if '一' <= c <= '鿿')
            en = len(content) - cjk
            total += max(1, cjk // 2 + en // 4)
            if m.get("tool_calls"):
                total += len(str(m["tool_calls"])) // 4
        return total

    def should_compress(self, messages: list[dict], turn: int = 0) -> bool:
        """判断是否触发压缩."""
        self._should_unstick_circuit()
        if self._circuit_open:
            return False
        if len(messages) < 6:  # 太少消息不值得压
            return False
        estimated = self.estimate_tokens(messages)
        if estimated < self.compress_at:
            return False
        # 冷却期：上次压缩后 5 轮内不再压缩，除非接近 90% 上限
        if turn - self._last_compress_turn < 5:
            if estimated < int(self.max_tokens * 0.90):
                return False
        return True

    async def compress(
        self, messages: list[dict], memory=None, turn: int = 0
    ) -> tuple[list[dict], bool]:
        """执行压缩，返回 (新消息列表, 是否执行了压缩).

        memory 参数可选，用于压缩前记忆刷新（openclaw 模式）。
        """
        try:
            # ── 1. 找分割点（抄 tinypace）──
            split_idx = self._find_split_point(messages)

            head = messages[:split_idx]
            tail = messages[split_idx:]

            if len(head) < 3:
                logger.info("Head too short, skipping compression")
                return messages, False

            logger.info(
                f"Compressing: {len(head)} head msgs → summary, "
                f"{len(tail)} tail msgs kept "
                f"(estimated {self.estimate_tokens(messages)} tokens)"
            )

            # ── 2. 压缩前记忆刷新（抄 openclaw）──
            if memory and hasattr(memory, 'list_memories'):
                try:
                    existing = memory.list_memories()
                    if existing:
                        logger.info(f"Pre-compression: {len(existing)} existing memories preserved")
                except Exception:
                    pass

            # ── 3. LLM 摘要 Head ──
            conversation_text = self._format_head(head)
            prompt = SUMMARY_PROMPT.format(conversation=conversation_text)

            try:
                response = await self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    tools=None,  # 摘要不需要工具
                )
                summary = response.get("content", "").strip()
            except Exception as e:
                logger.error(f"Summary LLM call failed: {e}")
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._max_failures:
                    self._circuit_open = True
                    logger.warning("Circuit breaker OPEN: skipping compression")
                return messages, False

            if not summary or len(summary) < 20:
                logger.warning("Summary too short, skipping")
                self._consecutive_failures += 1
                return messages, False

            # ── 4. 截断过长摘要 ──
            max_chars = self.max_summary_tokens * 4
            if len(summary) > max_chars:
                summary = summary[:max_chars] + "\n...(truncated)"

            # ── 5. 组装新消息列表 ──
            summary_msg = {
                "role": "system",
                "content": f"[历史对话摘要]\n{summary}",
            }
            new_messages = [summary_msg] + tail

            # 成功 → 重置熔断器 + 记录压缩轮次
            self._consecutive_failures = 0
            self._last_compress_turn = turn

            logger.info(
                f"Compression done: {len(messages)} → {len(new_messages)} messages "
                f"(estimated {self.estimate_tokens(messages)} → {self.estimate_tokens(new_messages)} tokens)"
            )

            return new_messages, True

        except Exception as e:
            logger.error(f"Compression failed: {e}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_failures:
                self._circuit_open = True
            return messages, False

    def _find_split_point(self, messages: list[dict]) -> int:
        """断点保护：不在 tool_use/tool_result 链中间切断."""
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                # 检查 i+1 是否是 tool 消息（user 后面直接跟着 tool result）
                if i + 1 < len(messages) and messages[i + 1].get("role") == "tool":
                    continue  # 往前找更早的 user 消息
                return i
        return max(0, len(messages) - 4)

    def _should_unstick_circuit(self) -> bool:
        """超过 30 分钟自动重置熔断器."""
        if self._circuit_open and self._consecutive_failures >= self._max_failures:
            if not hasattr(self, '_circuit_opened_at'):
                self._circuit_opened_at = time.time()
            if time.time() - self._circuit_opened_at > 1800:
                self.reset()
                return True
        return False

    def _format_head(self, head: list[dict]) -> str:
        """将 Head 消息格式化为文本，供 LLM 摘要."""
        lines = []
        for m in head:
            role = m.get("role", "unknown")
            content = str(m.get("content", ""))

            # 截断过长内容
            if len(content) > 500:
                content = content[:500] + "..."

            if role == "user":
                lines.append(f"用户: {content}")
            elif role == "assistant":
                prefix = ""
                if m.get("tool_calls"):
                    tool_names = [
                        tc.get("function", {}).get("name", "?")
                        for tc in m["tool_calls"]
                    ]
                    prefix = f"[调用工具: {', '.join(tool_names)}] "
                lines.append(f"AI: {prefix}{content}")
            elif role == "tool":
                tool_name = m.get("name", "tool")
                short = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"工具结果({tool_name}): {short}")
            elif role == "system":
                if "[历史对话摘要]" not in content:
                    lines.append(f"系统: {content[:200]}")

        return "\n".join(lines)

    def reset_cooldown(self):
        """重置压缩冷却计时器（每次 agent.run() 调用时重置）."""
        self._last_compress_turn = -10

    def reset(self):
        """重置熔断器状态."""
        self._consecutive_failures = 0
        self._circuit_open = False
