"""Web Fetch 工具 — 抓取网页全文，提取正文内容.

自主学习的核心工具：让 agent 能真正"读"网页，而不只是搜索摘要。
"""

import logging
import re
import urllib.request
from typing import Any, AsyncGenerator, Optional
from urllib.error import URLError

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

MAX_CONTENT = 30 * 1024  # 30KB 上限
REQUEST_TIMEOUT = 15  # 秒


class WebFetchTool(BaseTool):
    """抓取并提取网页正文内容."""

    @property
    def name(self) -> str:
        return "web_fetch"

    async def description(self) -> str:
        return "Fetch the full content of a web page and extract readable text."

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
                    "Fetch and extract the main text content from a web page. "
                    "Returns clean text stripped of HTML, scripts, and styles. "
                    "Use this when you need to read an article, documentation page, "
                    "or any web content in full (not just a search snippet). "
                    f"Output capped at {MAX_CONTENT // 1024}KB."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The full URL of the page to fetch (include https://).",
                        },
                    },
                    "required": ["url"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        url = input_args.get("url", "")
        if not url or not url.strip():
            return {"result": False, "message": "url is required"}
        if not url.startswith(("http://", "https://")):
            return {"result": False, "message": "url must start with http:// or https://"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        url = input_args["url"]

        try:
            import asyncio
            loop = asyncio.get_running_loop()
            html = await loop.run_in_executor(None, self._fetch, url)

            if not html:
                yield ToolResult(type="result", data="Error: empty response from server")
                return

            text = self._extract_text(html)

            if len(text) < 50:
                yield ToolResult(
                    type="result",
                    data=f"Page content too short ({len(text)} chars). May be JS-rendered or blocked.",
                    result_for_assistant=f"Page too short ({len(text)} chars). Try a different URL or use web_search to find alternative sources.",
                )
                return

            if len(text) > MAX_CONTENT:
                text = text[:MAX_CONTENT] + "\n\n... (truncated)"

            title = self._extract_title(html)
            header = f"# {title}\nSource: {url}\n\n" if title else f"Source: {url}\n\n"
            result = header + text

            yield ToolResult(
                type="result",
                data=result,
                result_for_assistant=result,
            )

        except Exception as e:
            logger.error(f"Web fetch failed for {url}: {e}")
            yield ToolResult(
                type="result",
                data=f"Fetch error: {e}",
                result_for_assistant=f"Failed to fetch {url}: {e}. Try a different URL.",
            )

    def _fetch(self, url: str) -> Optional[str]:
        """同步抓取网页."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return None
                raw = resp.read()
                # 尝试常见编码
                for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
                    try:
                        return raw.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw.decode("utf-8", errors="replace")
        except URLError as e:
            logger.warning(f"URL error for {url}: {e}")
            return None

    def _extract_text(self, html: str) -> str:
        """从 HTML 中提取纯文本."""
        # 去掉 script 和 style
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # 去掉 HTML 注释
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

        # 替换块级元素为换行
        for tag in ['p', 'div', 'article', 'section', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'tr']:
            html = re.sub(f'</?{tag}[^>]*>', '\n', html, flags=re.IGNORECASE)

        # 去掉所有剩余标签
        html = re.sub(r'<[^>]+>', '', html)

        # 清理空白
        html = re.sub(r'&nbsp;', ' ', html)
        html = re.sub(r'&amp;', '&', html)
        html = re.sub(r'&lt;', '<', html)
        html = re.sub(r'&gt;', '>', html)
        html = re.sub(r'&quot;', '"', html)
        html = re.sub(r'&#\d+;', '', html)

        # 合并空行
        lines = [line.strip() for line in html.split('\n')]
        lines = [l for l in lines if l and len(l) > 1]
        return '\n'.join(lines)

    def _extract_title(self, html: str) -> str:
        """提取页面标题."""
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()[:200]
        return ""
