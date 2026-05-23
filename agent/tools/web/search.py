"""Web 搜索工具 — 基于 ddgs 库（原 duckduckgo_search）。

比 tinypace 的 BS4 HTML 抓取方案更可靠：
- tinypace 方案已知有 CAPTCHA 问题（代码自己承认）
- ddgs 库封装了 DDG 内部 API，社区维护
- 免费、无需 API key
- 注意：DuckDuckGo 中文搜索效果一般，英文搜索较好
"""

import logging
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """使用 DuckDuckGo 搜索互联网."""

    @property
    def name(self) -> str:
        return "web_search"

    async def description(self) -> str:
        return "Search the internet using DuckDuckGo. Returns titles, links, and snippets."

    def is_read_only(self) -> bool:
        return True

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Search the internet for information using DuckDuckGo. "
                    "Returns a list of results with title, link, and snippet. "
                    "Use this to find current information, documentation, news, "
                    "or anything that requires up-to-date web search."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results (default 5, max 10).",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        query = input_args.get("query", "")
        if not query or not query.strip():
            return {"result": False, "message": "query is required"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        query = input_args["query"]
        max_results = min(input_args.get("max_results", 5), 10)

        try:
            # duckduckgo_search 是同步库，用 run_in_executor 避免阻塞事件循环
            import asyncio
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None, self._search, query, max_results
            )

            if not results:
                yield ToolResult(
                    type="result",
                    data="No results found.",
                    result_for_assistant=f"No results found for: {query}",
                )
                return

            formatted = self._format_results(query, results)
            yield ToolResult(type="result", data=formatted, result_for_assistant=formatted)

        except Exception as e:
            logger.error(f"Web search failed: {e}")
            yield ToolResult(
                type="result",
                data=f"Search error: {e}",
                result_for_assistant=f"Search failed: {e}. Try a different query or check network connection.",
            )

    def _search(self, query: str, max_results: int) -> list[dict]:
        """同步搜索（在线程池中执行）."""
        from ddgs import DDGS

        with DDGS() as ddgs:
            # 限制搜索后端为极速且国内高可用的 duckduckgo,brave，彻底规避 mojeek, yandex 等慢速引擎导致的超时挂起
            results = list(ddgs.text(query, backend="duckduckgo,brave", max_results=max_results))
            return [
                {"title": r["title"], "link": r["href"], "snippet": r["body"]}
                for r in results
            ]

    def _format_results(self, query: str, results: list[dict]) -> str:
        """格式化搜索结果为文本."""
        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            snippet = r["snippet"][:300] + "..." if len(r["snippet"]) > 300 else r["snippet"]
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['link']}")
            lines.append(f"   {snippet}")
            lines.append("")
        return "\n".join(lines)
