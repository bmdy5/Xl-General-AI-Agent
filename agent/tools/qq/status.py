"""QQ 状态感知工具 — 允许 Agent 主动查询自己在 QQ 网关中的状态和加群列表。

遵循 ACI (Agent-Computer Interface) 规范 design。
"""

import os
import json
import logging
import urllib.request
from typing import Any, AsyncGenerator, Optional
from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class GetQQStatusTool(BaseTool):
    """获取当前 QQ 机器人的在线状态与已加入的群聊列表。"""

    @property
    def name(self) -> str:
        return "get_qq_status"

    async def description(self) -> str:
        return "获取当前 QQ 机器人的状态、账号登录信息以及当前加入的群聊列表 (Get the login status and group list of the QQ bot)."

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
                "description": "获取当前 QQ 机器人的状态、账号登录信息以及当前加入的群聊列表，以此来确认机器人是否已被加入某些群聊中。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        nc_http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
        headers = {"Content-Type": "application/json"}
        
        results = {}
        
        # 1. 获取登录信息
        try:
            url_login = f"{nc_http_url}/get_login_info"
            req = urllib.request.Request(url_login, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "ok":
                    results["login_info"] = data.get("data", {})
                else:
                    results["login_info"] = data
        except Exception as e:
            results["login_info"] = f"Failed to fetch login info: {e}"

        # 2. 获取群列表
        try:
            url_groups = f"{nc_http_url}/get_group_list"
            req = urllib.request.Request(url_groups, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "ok":
                    results["group_list"] = data.get("data", [])
                else:
                    results["group_list"] = data
        except Exception as e:
            results["group_list"] = f"Failed to fetch group list: {e}"

        # 格式化输出
        lines = ["=== QQ Bot Status ==="]
        if isinstance(results["login_info"], dict):
            li = results["login_info"]
            lines.append(f"账号: {li.get('nickname', '未知')} ({li.get('user_id', '未知')})")
        else:
            lines.append(f"账号状态: {results['login_info']}")
            
        if isinstance(results["group_list"], list):
            gl = results["group_list"]
            lines.append(f"加入群聊数量: {len(gl)}")
            for idx, g in enumerate(gl, 1):
                lines.append(f"  {idx}. 群名: {g.get('group_name', '未知')} (群号: {g.get('group_id', '未知')})")
        else:
            lines.append(f"群聊列表获取异常: {results['group_list']}")
            
        output = "\n".join(lines)
        yield ToolResult(type="result", data=output, result_for_assistant=output)
