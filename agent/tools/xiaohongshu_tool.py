"""小红书 MCP 自动化发布与数据调研工具.

基于 Streamable HTTP 协议与后台常驻的小红书 Go MCP 服务器交互。
"""

import asyncio
import logging
import os
import socket
from typing import Any, AsyncGenerator, Optional
import httpx

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

SERVER_URL = "http://127.0.0.1:18060/mcp"
DEFAULT_PORT = 18060


def is_port_open(port: int) -> bool:
    """检查本地端口是否被占用 (服务是否在跑)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (socket.timeout, ConnectionRefusedError):
            return False


class XiaohongshuTool(BaseTool):
    """小红书 MCP 工具，支持搜索、详情、发帖（触发 QQ 审批）."""

    @property
    def name(self) -> str:
        return "xiaohongshu"

    async def description(self) -> str:
        return (
            "Interact with Xiaohongshu (RedNote) to search posts, read post details "
            "or publish new image-text posts."
        )

    def is_read_only(self) -> bool:
        # publish 写入操作不为 read-only
        return False

    def is_concurrency_safe(self) -> bool:
        # 小红书自动化依赖浏览器，不是并发安全的
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        # 仅当 action 为 publish 时触发 QQ 审批阻断，只读操作（search, detail）静默直接执行
        if input_args and input_args.get("action") == "publish":
            return True
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Interact with Xiaohongshu. Supported actions:\n"
                    "- 'search': Search notes by a keyword.\n"
                    "- 'detail': Fetch detailed post content and comments using note_id.\n"
                    "- 'publish': Publish an image-text post (Requires QQ approval)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "detail", "publish"],
                            "description": "The action to perform.",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "Keyword for search action.",
                        },
                        "note_id": {
                            "type": "string",
                            "description": "Note ID/Feed ID for detail action.",
                        },
                        "xsec_token": {
                            "type": "string",
                            "description": "Security token (xsecToken) for detail action, retrieved from search results.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Post title for publish action (Max 20 chars).",
                        },
                        "content": {
                            "type": "string",
                            "description": "Post content/body for publish action (Max 1000 chars).话题标签单独使用 tags 参数，正文不要包含以#开头的标签。",
                        },
                        "image_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of absolute local image paths to upload (Min 1 image). Supports HTTPS URLs as well.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of tags/topics (e.g. ['AI', '生活']) for publish action.",
                        }
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        action = input_args.get("action")
        if action not in ("search", "detail", "publish"):
            return {"result": False, "message": "action must be 'search', 'detail' or 'publish'"}

        if action == "search" and not input_args.get("keyword"):
            return {"result": False, "message": "keyword is required for search action"}

        if action == "detail":
            if not input_args.get("note_id"):
                return {"result": False, "message": "note_id is required for detail action"}
            if not input_args.get("xsec_token"):
                return {"result": False, "message": "xsec_token is required for detail action"}

        if action == "publish":
            if not input_args.get("title") or not input_args.get("content"):
                return {"result": False, "message": "title and content are required for publish action"}
            images = input_args.get("image_paths")
            if not images or not isinstance(images, list) or len(images) == 0:
                return {"result": False, "message": "image_paths (non-empty list) is required for publish action"}

        return {"result": True, "message": ""}

    async def _ensure_server_running(self) -> None:
        """确保后台的 Go MCP 进程在运行，若没运行则在后台自动调起."""
        if is_port_open(DEFAULT_PORT):
            logger.info("Xiaohongshu MCP server is already running.")
            return

        logger.info("Xiaohongshu MCP server is not running. Launching in background...")
        # 二进制绝对路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        bin_path = os.path.abspath(
            os.path.join(current_dir, "../../bin/xiaohongshu-mcp/xiaohongshu-mcp-darwin-arm64")
        )

        if not os.path.exists(bin_path):
            raise FileNotFoundError(f"Xiaohongshu MCP binary not found at: {bin_path}")

        # 后台拉起进程
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "-headless=true",
            f"-port=:{DEFAULT_PORT}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # 轮询端口直到可用，最多等待 10 秒
        for _ in range(20):
            if is_port_open(DEFAULT_PORT):
                logger.info("Xiaohongshu MCP server started successfully!")
                return
            await asyncio.sleep(0.5)

        raise TimeoutError("Timeout waiting for Xiaohongshu MCP server to start.")

    async def _call_mcp(self, tool_name: str, arguments: dict) -> str:
        """基于 Streamable HTTP 协议封装，单次请求-响应调用指定的 MCP 工具."""
        await self._ensure_server_running()

        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. 发起 initialize 握手以换取 Session ID
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xl-agent", "version": "1.0.0"},
                },
            }
            init_resp = await client.post(SERVER_URL, json=init_payload)
            init_resp.raise_for_status()

            session_id = init_resp.headers.get("mcp-session-id")
            if not session_id:
                raise ValueError("Did not receive 'mcp-session-id' from MCP server.")

            # 2. 携带 Session ID 发送 notifications/initialized 通知
            headers = {
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            }
            await client.post(
                SERVER_URL,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

            # 3. 携带 Session ID 发送 tools/call 调用具体的小红书工具
            call_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
            call_resp = await client.post(SERVER_URL, headers=headers, json=call_payload)
            call_resp.raise_for_status()

            res_json = call_resp.json()
            if "error" in res_json:
                error_msg = res_json["error"].get("message", "Unknown error")
                raise RuntimeError(f"MCP server error: {error_msg}")

            # 提取返回文本内容
            content = res_json.get("result", {}).get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            if not texts:
                return f"No text content returned. Raw: {res_json}"
            return "\n".join(texts)

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]

        yield ToolResult(type="progress", data=f"开始执行小红书 [{action}] 操作...")

        try:
            if action == "search":
                keyword = input_args["keyword"]
                result = await self._call_mcp("search_feeds", {"keyword": keyword})
                yield ToolResult(
                    type="result",
                    data=result,
                    result_for_assistant=f"小红书搜索“{keyword}”结果:\n{result}",
                )

            elif action == "detail":
                note_id = input_args["note_id"]
                xsec_token = input_args["xsec_token"]
                result = await self._call_mcp("get_feed_detail", {"feed_id": note_id, "xsec_token": xsec_token})
                yield ToolResult(
                    type="result",
                    data=result,
                    result_for_assistant=f"小红书笔记 [{note_id}] 详情:\n{result}",
                )

            elif action == "publish":
                title = input_args["title"]
                content = input_args["content"]
                image_paths = input_args["image_paths"]
                tags = input_args.get("tags") or []

                mcp_args = {
                    "title": title,
                    "content": content,
                    "images": image_paths,
                    "tags": tags,
                }
                
                # 调用 publish_content 发布图文笔记
                result = await self._call_mcp("publish_content", mcp_args)
                yield ToolResult(
                    type="result",
                    data=result,
                    result_for_assistant=f"小红书发布图文笔记成功!\n{result}",
                )

        except Exception as e:
            logger.error(f"小红书 [{action}] 执行失败: {e}", exc_info=True)
            yield ToolResult(
                type="result",
                data=f"小红书 [{action}] 执行失败: {e}",
                result_for_assistant=f"小红书 [{action}] 执行失败: {e}。请重试或检查环境登录状态。",
            )
