"""浏览器视觉工具 — BrowserAgentTool 通过 Playwright CDP 自主操作网页。"""

import os
import json
import logging
import asyncio
import urllib.request
from typing import Any, AsyncGenerator, Optional
from .base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.visual")


async def send_qq_notification(msg: str) -> None:
    """通过 OneBot CQ 码向 QQ 发送通知卡片。"""
    admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
    nc_http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
    nc_token = os.getenv("NAPCAT_TOKEN", "")

    url = f"{nc_http_url}/send_private_msg"
    payload = {"user_id": int(admin_id), "message": msg}
    headers = {"Content-Type": "application/json"}
    if nc_token:
        headers["Authorization"] = f"Bearer {nc_token}"

    loop = asyncio.get_running_loop()
    def _run():
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5.0) as r:
            r.read()
    try:
        await loop.run_in_executor(None, _run)
    except Exception as e:
        logger.error(f"Failed to send visual approval to QQ: {e}")


class BrowserAgentTool(BaseTool):
    """通用视觉Agent工具 — 截图->判断->点击/打字/滚动，Playwright CDP 直连浏览器"""

    @property
    def name(self) -> str:
        return "browser_agent"

    async def description(self) -> str:
        return "通用视觉操作引擎。截图->思考->点击/打字/滚动->验证，在网页上完成任意任务。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return True

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "【推荐优先使用】通用视觉浏览器操作引擎。自动连接浏览器->截图->看图->点击/打字/滚动->验证，在网页上完成任意任务。当你需要在网页上做任何操作时直接调用它，把任务描述清楚即可。例如：'打开百度搜索Python'、'在抖音私信里回复最新消息：你好呀'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "要执行的任务"
                        },
                        "cdp_url": {
                            "type": "string",
                            "description": "可选，浏览器CDP调试地址。不填则用默认"
                        }
                    },
                    "required": ["task"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if not input_args.get("task"):
            return {"result": False, "message": "Missing task"}
        return {"result": True, "message": ""}

    async def call(self, input_args: dict, context: Any = None) -> AsyncGenerator[ToolResult, None]:
        task = str(input_args.get("task"))
        cdp_url = input_args.get("cdp_url", "")
        agent = context

        if not agent or not getattr(agent, "llm", None):
            yield ToolResult(type="result", data="视觉任务失败: 未绑定LLM客户端")
            return

        try:
            from agent.core.visual_agent import VisualAgent
            async with VisualAgent(llm_client=agent.llm, memory_manager=agent.memory,
                                   cdp_url=cdp_url) as visual:
                result = await visual.execute(task=task)
            summary = json.dumps({
                "success": result["success"],
                "steps": result["steps"],
                "error": result.get("error", ""),
                "history": [h.get("result", "") for h in result.get("history", [])[-3:]],
            }, ensure_ascii=False)
            yield ToolResult(type="result", data=summary)
        except Exception as e:
            yield ToolResult(type="result", data=f"视觉任务失败: {e}")
