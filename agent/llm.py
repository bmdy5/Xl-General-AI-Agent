"""LLM 接口封装 — 基于 LiteLLM.

统一 Anthropic / OpenAI / 其他 provider 的调用接口。
支持 streaming + tool calling。
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

import litellm
from litellm import acompletion

# 关闭 LiteLLM 噪音日志
litellm.suppress_debug_info = True
logging.getLogger("litellm").setLevel(logging.WARNING)


class LLMClient:
    """LiteLLM 封装，支持流式调用和 tool calling."""

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        abort_event: Optional[asyncio.Event] = None,
        model_override: str = "",
    ) -> dict:
        """调用 LLM，返回 {"content": str, "tool_calls": list | None}.

        model_override: 可指定不同模型（自主学习用 flash 模型省钱）。
        """
        kwargs = {
            "model": model_override or self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": 120,
        }
        if tools:
            kwargs["tools"] = tools
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        if abort_event and abort_event.is_set():
            return {"content": "", "tool_calls": None, "reasoning_content": None, "tokens_used": 0}
        try:
            response = await acompletion(**kwargs)
        except litellm.RateLimitError:
            if abort_event and abort_event.is_set():
                return {"content": "", "tool_calls": None, "reasoning_content": None, "tokens_used": 0}
            await asyncio.sleep(2)
            try:
                response = await acompletion(**kwargs)
            except Exception:
                return {"content": "", "tool_calls": None, "reasoning_content": None, "tokens_used": 0}

        choice = response.choices[0].message

        # 保留 DeepSeek thinking 模式的 reasoning_content
        reasoning = getattr(choice, "reasoning_content", None)

        tool_calls = None
        if choice.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.tool_calls
            ]

        usage = getattr(response, "usage", None)
        tokens_used = usage.total_tokens if usage else 0

        return {
            "content": choice.content or "",
            "tool_calls": tool_calls,
            "reasoning_content": reasoning,
            "tokens_used": tokens_used,
        }

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        abort_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[dict, None]:
        """流式调用 LLM，逐个 yield {"type": "text_delta"|"tool_call", ...}.

        前端可以用这个做打字机效果。
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": 120,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        try:
            response = await acompletion(**kwargs)
        except litellm.RateLimitError:
            yield {"type": "error", "error": "rate_limit", "message": "Rate limit exceeded, please retry"}
            return
        except asyncio.TimeoutError:
            yield {"type": "error", "error": "timeout", "message": "LLM request timed out"}
            return
        except Exception as e:
            yield {"type": "error", "error": "api_error", "message": str(e)}
            return

        tool_calls_acc: dict[int, dict] = {}
        async for chunk in response:
            if abort_event and abort_event.is_set():
                yield {"type": "aborted"}
                return

            try:
                delta = chunk.choices[0].delta
            except (IndexError, AttributeError):
                continue

            # DeepSeek reasoning (thinking process)
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                yield {"type": "reasoning", "content": delta.reasoning_content}

            if delta.content:
                yield {"type": "text_delta", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

        for tc in tool_calls_acc.values():
            yield {"type": "tool_call", "data": tc}
