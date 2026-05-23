"""LLM 接口封装 — 基于 LiteLLM.

统一 Anthropic / OpenAI / 其他 provider 的调用接口。
支持 streaming + tool calling。
"""

import asyncio
import logging
import os
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
        max_tokens: int = 16384,
        temperature: float = 1.0,
        model_vision: Optional[str] = None,
        model_pro: Optional[str] = None,
    ):
        self.model = model               # 默认模型（DeepSeek Flash）
        self.api_key = api_key           # Mimo API key
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.model_vision = model_vision  # 视觉模型（Mimo）
        self.model_pro = model_pro        # 深度推理模型（DeepSeek Pro）
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY") or ""

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
            
        model_name = model_override or self.model
        if model_name.startswith("deepseek/") and not self.api_base:
            if self.deepseek_api_key:
                kwargs["api_key"] = self.deepseek_api_key
        else:
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

        max_retries = 3
        backoff_factor = 2.0
        response = None

        for attempt in range(max_retries):
            if abort_event and abort_event.is_set():
                return {"content": "", "tool_calls": None, "reasoning_content": None, "tokens_used": 0}
            try:
                if attempt >= 1 and kwargs["model"] != "openai/gpt-4o":
                    logging.warning(
                        f"⚠️ LLM 调用重试 (第 {attempt + 1} 次尝试)，启动自动容灾机制："
                        f"从 {kwargs['model']} 切换为高稳定通道 openai/gpt-4o"
                    )
                    kwargs["model"] = "openai/gpt-4o"
                    if self.api_key:
                        kwargs["api_key"] = self.api_key
                    elif "api_key" in kwargs:
                        kwargs.pop("api_key")
                    if self.api_base:
                        kwargs["api_base"] = self.api_base
                    elif "api_base" in kwargs:
                        kwargs.pop("api_base")
                response = await acompletion(**kwargs)
                break
            except (litellm.RateLimitError, litellm.InternalServerError, litellm.APIError, litellm.APIConnectionError, litellm.Timeout, Exception) as e:
                if attempt == max_retries - 1:
                    logging.error(f"LLM call failed after {max_retries} attempts: {e}")
                    raise e
                sleep_time = backoff_factor ** attempt
                logging.warning(f"LLM call failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)

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
        model_override: str = "",
    ) -> AsyncGenerator[dict, None]:
        """流式调用 LLM，逐个 yield {"type": "text_delta"|"tool_call", ...}.

        前端可以用这个做打字机效果。
        model_override: 可指定不同模型。
        """
        kwargs = {
            "model": model_override or self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": 120,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            
        model_name = model_override or self.model
        if model_name.startswith("deepseek/") and not self.api_base:
            if self.deepseek_api_key:
                kwargs["api_key"] = self.deepseek_api_key
        else:
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

        max_retries = 3
        backoff_factor = 2.0
        response = None

        for attempt in range(max_retries):
            if abort_event and abort_event.is_set():
                yield {"type": "aborted"}
                return
            try:
                if attempt >= 1 and kwargs["model"] != "openai/gpt-4o":
                    logging.warning(
                        f"⚠️ LLM 流式调用重试 (第 {attempt + 1} 次尝试)，启动自动容灾机制："
                        f"从 {kwargs['model']} 切换为高稳定通道 openai/gpt-4o"
                    )
                    kwargs["model"] = "openai/gpt-4o"
                    if self.api_key:
                        kwargs["api_key"] = self.api_key
                    elif "api_key" in kwargs:
                        kwargs.pop("api_key")
                    if self.api_base:
                        kwargs["api_base"] = self.api_base
                    elif "api_base" in kwargs:
                        kwargs.pop("api_base")
                response = await acompletion(**kwargs)
                break
            except (litellm.RateLimitError, litellm.InternalServerError, litellm.APIError, litellm.APIConnectionError, litellm.Timeout, Exception) as e:
                if attempt == max_retries - 1:
                    logging.error(f"LLM stream call failed after {max_retries} attempts: {e}")
                    raise e
                sleep_time = backoff_factor ** attempt
                logging.warning(f"LLM stream call failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {sleep_time}s...")
                await asyncio.sleep(sleep_time)

        stream_timeout = float(os.getenv("LLM_STREAM_TIMEOUT", "15.0"))
        try:
            tool_calls_acc: dict[int, dict] = {}
            # 通过 asyncio.wait_for 为 chunk 的迭代读取提供配置化强心跳超时拦截，杜绝网络无响应死锁
            response_iter = response.__aiter__()
            while True:
                if abort_event and abort_event.is_set():
                    yield {"type": "aborted"}
                    return
                try:
                    chunk = await asyncio.wait_for(response_iter.__anext__(), timeout=stream_timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logging.error(f"LLM stream chunk read timeout ({stream_timeout}s limit reached). Connection hung.")
                    yield {"type": "error", "content": f"LLM stream connection lost: chunk read timeout after {stream_timeout}s"}
                    return

                delta = chunk.choices[0].delta

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
        except Exception as e:
            logging.error(f"Error during LLM stream chunk processing: {e}")
            yield {"type": "error", "content": f"LLM stream connection lost: {e}"}
        finally:
            # 极致网络加固：即使抛错或超时退出，也必须强物理释放 response HTTP/socket 句柄，杜绝泄露
            if response and hasattr(response, "close"):
                try:
                    if asyncio.iscoroutinefunction(response.close):
                        await response.close()
                    else:
                        response.close()
                except Exception as close_err:
                    logging.warning(f"Failed to safely close LLM stream response: {close_err}")
