"""Token 估算工具 — 压缩功能已禁用，由疲劳+dreaming 替代。"""

import logging

logger = logging.getLogger(__name__)


class ContextCompressor:
    """Token 计数器（压缩已禁用）。"""

    def __init__(self, llm=None, max_tokens: int = 128000):
        self.llm = llm
        self.max_tokens = max_tokens

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

    @property
    def compress_at(self):
        return 0  # 禁用压缩
