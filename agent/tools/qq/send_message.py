"""QQ 消息主动发送工具 — 允许 Agent 主动私聊或在群聊中发送消息，并支持 CQ 码（如 @ 对方）。

遵循 ACI (Agent-Computer Interface) 规范 design。
"""

import os
import json
import logging
import urllib.request
import asyncio
from typing import Any, AsyncGenerator, Optional
from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class SendQQMessageTool(BaseTool):
    """主动向 QQ 用户或群聊发送消息的物理工具。"""

    @property
    def name(self) -> str:
        return "send_qq_message"

    async def description(self) -> str:
        return "主动发送 QQ 消息 (Send QQ messages actively). 支持私聊、群聊，并可在消息内使用 [CQ:at,qq=QQ号] 等 CQ 码进行主动 @ 提醒。"

    def is_read_only(self) -> bool:
        # 因为会修改外部聊天状态，不算只读
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        # 主动发送消息不是高危敏感命令（如 bash/删除），不需要每步弹出权限申请，直接放行
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "主动发送 QQ 消息，可用于定时早报推送、突发通知、或者在发现有价值问题时主动去群里 @ 某人。可以使用 [CQ:at,qq=... ] 在消息中 @ 对方。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "msg_type": {
                            "type": "string",
                            "enum": ["private", "group"],
                            "description": "消息类型：'private' 表示私聊，'group' 表示群聊"
                        },
                        "target_id": {
                            "type": "string",
                            "description": "发送的目标 ID，私聊为对方 QQ 号，群聊为 QQ 群号"
                        },
                        "message": {
                            "type": "string",
                            "description": "要发送的消息文本内容，支持在里面混入 [CQ:at,qq=... ] 等 OneBot 规范的 CQ 码"
                        }
                    },
                    "required": ["msg_type", "target_id", "message"]
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        msg_type = input_args.get("msg_type")
        target_id = input_args.get("target_id")
        message = input_args.get("message")

        if msg_type not in ("private", "group"):
            return {"result": False, "message": "msg_type 必须是 'private' 或 'group'"}
        if not target_id or not str(target_id).strip().isdigit():
            return {"result": False, "message": "target_id 必须是纯数字的 QQ号 或 群号"}
        if not message or not str(message).strip():
            return {"result": False, "message": "message 消息内容不能为空"}

        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        msg_type = input_args.get("msg_type")
        target_id = str(input_args.get("target_id")).strip()
        message = input_args.get("message")

        nc_http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
        nc_token = os.getenv("NAPCAT_TOKEN", "")

        endpoint = "/send_private_msg" if msg_type == "private" else "/send_group_msg"
        url = f"{nc_http_url}{endpoint}"

        payload = {}
        if msg_type == "private":
            payload["user_id"] = int(target_id)
        else:
            payload["group_id"] = int(target_id)
        payload["message"] = message

        headers = {"Content-Type": "application/json"}
        if nc_token:
            headers["Authorization"] = f"Bearer {nc_token}"

        loop = asyncio.get_running_loop()
        success = False
        response_data = ""

        try:
            def _send_request():
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return resp.read().decode("utf-8")

            res_text = await loop.run_in_executor(None, _send_request)
            res_json = json.loads(res_text)
            if res_json.get("status") == "ok" or res_json.get("retcode") == 0:
                success = True
                response_data = f"发送成功！返回: {res_text}"
            else:
                response_data = f"发送失败：{res_text}"
        except Exception as e:
            response_data = f"网络请求异常: {e}"
            logger.error(f"Failed to actively send QQ message via tool: {e}")

        output = f"【QQ 主动发送消息结果】\n状态: {'成功' if success else '失败'}\n详情: {response_data}"
        yield ToolResult(type="result", data=output, result_for_assistant=output)
