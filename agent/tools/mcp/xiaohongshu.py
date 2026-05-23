"""小红书 MCP 自动化发布与数据调研工具.

基于 Streamable HTTP 协议与后台常驻的小红书 Go MCP 服务器交互。
支持 12 个高心智 actions 覆盖登录、看推荐、高级搜索、全量评论抓取、点赞收藏评论及定时图文视频发布全套原生功能。
"""

import asyncio
import logging
import os
import socket
import re
import base64
from typing import Any, AsyncGenerator, Optional
import httpx

from ..base_tool import BaseTool, ToolResult

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
    """小红书全功能高心智大升级工具，支持全自动扫码、调研、互动、发布与定时管理."""

    @property
    def name(self) -> str:
        return "xiaohongshu"

    async def description(self) -> str:
        return (
            "Interact with Xiaohongshu (RedNote). Support 12 actions covering checking login status, "
            "fetching login QR code, listing recommend feeds, advanced post search, post detail & comments crawling, "
            "user profile checking, liking/favoriting posts, commenting/replying comments, and publishing "
            "or scheduling image-text and video posts. 100% automated with no permissions required."
        )

    def is_read_only(self) -> bool:
        # 该工具包含大量发布与交互类写入操作，不属于 read-only
        return False

    def is_concurrency_safe(self) -> bool:
        # 小红书自动化依赖浏览器会话，不是并发安全的
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        # 解开小红书所有发帖、评论、互动枷锁，100% 独立自主运行，无需任何安全审批
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Interact with Xiaohongshu. Supported actions:\n"
                    "- 'login_status': Check current login status or clear cookies to log out.\n"
                    "- 'login_qrcode': Fetch base64 login QR code and decode it to qrcode_login.png automatically.\n"
                    "- 'list_feeds': Retrieve recommended feeds from home page.\n"
                    "- 'search': Search notes with advanced filters (sort, type, publish time, range, location).\n"
                    "- 'detail': Fetch note details and comments (supports loading all comments & nested replies).\n"
                    "- 'user_profile': Retrieve user page stats (followers, likes) and work list.\n"
                    "- 'like': Like or unlike a note.\n"
                    "- 'favorite': Favorite or unfavorite a note.\n"
                    "- 'comment': Leave a comment on a note.\n"
                    "- 'reply_comment': Reply to a specific comment under a note.\n"
                    "- 'publish': Publish or schedule an image-text post (supports定时, 带货, 原创, 可见性).\n"
                    "- 'publish_video': Publish or schedule a video post (supports定时, 带货, 可见性)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "login_status",
                                "login_qrcode",
                                "list_feeds",
                                "search",
                                "detail",
                                "user_profile",
                                "like",
                                "favorite",
                                "comment",
                                "reply_comment",
                                "publish",
                                "publish_video"
                            ],
                            "description": "The action to perform on Xiaohongshu.",
                        },
                        "sub_action": {
                            "type": "string",
                            "enum": ["check", "logout"],
                            "description": "Required for 'login_status'. 'check' (default) to check status, 'logout' to log out.",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "Required for 'search' action.",
                        },
                        "sort_by": {
                            "type": "string",
                            "enum": ["综合", "最新", "最多点赞", "最多评论", "最多收藏"],
                            "description": "Optional filter for 'search'. Default: '综合'.",
                        },
                        "note_type": {
                            "type": "string",
                            "enum": ["不限", "视频", "图文"],
                            "description": "Optional filter for 'search'. Default: '不限'.",
                        },
                        "publish_time": {
                            "type": "string",
                            "enum": ["不限", "一天内", "一周内", "半年内"],
                            "description": "Optional filter for 'search'. Default: '不限'.",
                        },
                        "search_scope": {
                            "type": "string",
                            "enum": ["不限", "已看过", "未看过", "已关注"],
                            "description": "Optional filter for 'search'. Default: '不限'.",
                        },
                        "location": {
                            "type": "string",
                            "enum": ["不限", "同城", "附近"],
                            "description": "Optional filter for 'search'. Default: '不限'.",
                        },
                        "note_id": {
                            "type": "string",
                            "description": "Required for 'detail', 'like', 'favorite', 'comment', and 'reply_comment' actions.",
                        },
                        "xsec_token": {
                            "type": "string",
                            "description": "Required for 'detail', 'user_profile', 'like', 'favorite', 'comment', 'reply_comment' actions.",
                        },
                        "load_all_comments": {
                            "type": "boolean",
                            "description": "Optional for 'detail'. True to scroll and load all comments, False (default) for top 10.",
                        },
                        "click_more_replies": {
                            "type": "boolean",
                            "description": "Optional for 'detail' (only when load_all_comments=True). True to expand nested replies.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Optional for 'detail'. Limit of top-level comments to load (Default: 20).",
                        },
                        "reply_limit": {
                            "type": "integer",
                            "description": "Optional for 'detail'. Skip comments with too many replies to avoid rate limit (Default: 10).",
                        },
                        "scroll_speed": {
                            "type": "string",
                            "enum": ["slow", "normal", "fast"],
                            "description": "Optional for 'detail'. Scrolling speed (Default: 'normal').",
                        },
                        "user_id": {
                            "type": "string",
                            "description": "Required for 'user_profile' and 'reply_comment'.",
                        },
                        "comment_id": {
                            "type": "string",
                            "description": "Required for 'reply_comment' action.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Required for 'comment', 'reply_comment', 'publish', and 'publish_video' actions.",
                        },
                        "unlike": {
                            "type": "boolean",
                            "description": "Optional for 'like'. True to cancel like, False (default) to like.",
                        },
                        "unfavorite": {
                            "type": "boolean",
                            "description": "Optional for 'favorite'. True to cancel favorite, False (default) to favorite.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Required for 'publish' and 'publish_video'. Max 20 chars.",
                        },
                        "image_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Required for 'publish'. Local absolute paths or HTTP links of images.",
                        },
                        "video_path": {
                            "type": "string",
                            "description": "Required for 'publish_video'. Local absolute path of a single video file.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags/topics list (e.g. ['AI', '旅行']) for publish actions.",
                        },
                        "schedule_at": {
                            "type": "string",
                            "description": "Optional ISO8601 string for scheduled release (e.g. 2024-01-20T10:30:00+08:00). Support 1 hour to 14 days.",
                        },
                        "visibility": {
                            "type": "string",
                            "enum": ["公开可见", "仅自己可见", "仅互关好友可见"],
                            "description": "Optional visibility settings. Default: '公开可见'.",
                        },
                        "is_original": {
                            "type": "boolean",
                            "description": "Optional for 'publish'. True to declare original content.",
                        },
                        "products": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional product keyword list for monetization binding.",
                        }
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        action = input_args.get("action")
        if not action:
            return {"result": False, "message": "action is required."}

        if action == "login_status":
            sub = input_args.get("sub_action", "check")
            if sub not in ("check", "logout"):
                return {"result": False, "message": "sub_action must be 'check' or 'logout'."}

        elif action == "search":
            if not input_args.get("keyword"):
                return {"result": False, "message": "keyword is required for search."}

        elif action == "detail":
            if not input_args.get("note_id") or not input_args.get("xsec_token"):
                return {"result": False, "message": "note_id and xsec_token are required for detail."}

        elif action == "user_profile":
            if not input_args.get("user_id") or not input_args.get("xsec_token"):
                return {"result": False, "message": "user_id and xsec_token are required for user_profile."}

        elif action in ("like", "favorite", "comment"):
            if not input_args.get("note_id") or not input_args.get("xsec_token"):
                return {"result": False, "message": "note_id and xsec_token are required."}
            if action == "comment" and not input_args.get("content"):
                return {"result": False, "message": "content is required for comment."}

        elif action == "reply_comment":
            if not input_args.get("note_id") or not input_args.get("xsec_token") or not input_args.get("comment_id") or not input_args.get("user_id") or not input_args.get("content"):
                return {"result": False, "message": "note_id, xsec_token, comment_id, user_id, and content are required for reply_comment."}

        elif action == "publish":
            if not input_args.get("title") or not input_args.get("content"):
                return {"result": False, "message": "title and content are required for publish."}
            images = input_args.get("image_paths")
            if not images or not isinstance(images, list) or len(images) == 0:
                return {"result": False, "message": "image_paths (non-empty list) is required for publish."}

        elif action == "publish_video":
            if not input_args.get("title") or not input_args.get("content") or not input_args.get("video_path"):
                return {"result": False, "message": "title, content, and video_path are required for publish_video."}

        return {"result": True, "message": ""}

    async def _ensure_server_running(self) -> None:
        """确保后台的 Go MCP 进程在运行，若没运行则在后台自动调起."""
        if is_port_open(DEFAULT_PORT):
            logger.info("Xiaohongshu MCP server is already running.")
            return

        logger.info("Xiaohongshu MCP server is not running. Launching in background...")
        current_dir = os.path.dirname(os.path.abspath(__file__))
        bin_path = os.path.abspath(
            os.path.join(current_dir, "../../../bin/xiaohongshu-mcp/xiaohongshu-mcp-darwin-arm64")
        )

        if not os.path.exists(bin_path):
            raise FileNotFoundError(f"Xiaohongshu MCP binary not found at: {bin_path}")

        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "-headless=true",
            f"-port=:{DEFAULT_PORT}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

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

            headers = {
                "Content-Type": "application/json",
                "mcp-session-id": session_id,
            }
            await client.post(
                SERVER_URL,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

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
            from .xiaohongshu_ops import execute_action
            result, assistant_msg = await execute_action(self, action, input_args)
            yield ToolResult(
                type="result",
                data=result,
                result_for_assistant=assistant_msg,
            )
        except Exception as e:
            logger.error(f"小红书 [{action}] 执行失败: {e}", exc_info=True)
            yield ToolResult(
                type="result",
                data=f"小红书 [{action}] 执行失败: {e}",
                result_for_assistant=f"小红书 [{action}] 执行失败: {e}。请重试或检查小红书登录与网络状态。",
            )
