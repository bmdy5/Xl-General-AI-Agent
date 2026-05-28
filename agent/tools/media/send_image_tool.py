"""公共发图工具 — 将本地图片自动上传至腾讯云 COS 并通过 QQ 推送给管理员亮哥。

遵循 ACI 规范设计，高内聚低耦合解耦。
"""

import os
import json
import logging
import urllib.request
import asyncio
import base64
from typing import Any, AsyncGenerator, Optional
from qcloud_cos import CosConfig, CosS3Client

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class SendImageToQqTool(BaseTool):
    """通用发图工具，将本地图片转换为 COS 链接并发送给管理员 QQ。"""

    @property
    def name(self) -> str:
        return "send_image_to_qq"

    async def description(self) -> str:
        return "将本地图片上传至腾讯云 COS 并通过 QQ 推送给管理员。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return True

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "主动将本地图片上传至腾讯云 COS，并将图片链接发送给管理员 QQ。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "local_path": {
                            "type": "string",
                            "description": "要发送的本地图片绝对路径。"
                        },
                        "cos_key_suffix": {
                            "type": "string",
                            "description": "可选的 COS 自定义后缀路径。例如 'douyin_qrcode/login.png'。"
                        },
                        "message_prefix": {
                            "type": "string",
                            "description": "可选的文字前缀，会和图片一起发送。"
                        }
                    },
                    "required": ["local_path"]
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        local_path = input_args.get("local_path")
        if not local_path:
            return {"result": False, "message": "local_path is required."}
        if not os.path.exists(local_path):
            return {"result": False, "message": f"local_path file does not exist: {local_path}"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        local_path = input_args.get("local_path")
        cos_key_suffix = input_args.get("cos_key_suffix") or "common/image.png"
        message_prefix = input_args.get("message_prefix") or "小萤为你推送图片："

        yield ToolResult(type="progress", data="开始上传图片到腾讯云 COS...")

        # 1. 初始化 COS 客户端并上传
        region = os.environ.get('TENCENT_CLOUD_COS_REGION', 'ap-guangzhou')
        secret_id = os.environ.get('TENCENT_CLOUD_SECRET_ID', '')
        secret_key = os.environ.get('TENCENT_CLOUD_SECRET_KEY', '')
        bucket = os.environ.get('TENCENT_CLOUD_COS_BUCKET', 'gpt-images-1409520107')

        if not secret_id or not secret_key:
            err_msg = "COS credentials not found in env (TENCENT_CLOUD_SECRET_ID/KEY)."
            logger.error(err_msg)
            yield ToolResult(type="result", data=err_msg, result_for_assistant=err_msg)
            return

        loop = asyncio.get_running_loop()
        cos_url = ""
        try:
            def _upload():
                cos_cfg = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
                cos_client = CosS3Client(cos_cfg)
                
                with open(local_path, 'rb') as f:
                    cos_client.put_object(
                        Bucket=bucket,
                        Body=f,
                        Key=cos_key_suffix,
                        ContentType='image/png'
                    )
                return f"https://{bucket}.cos.{region}.myqcloud.com/{cos_key_suffix}"

            cos_url = await loop.run_in_executor(None, _upload)
            logger.info(f"Image uploaded to COS successfully: {cos_url}")
        except Exception as upload_err:
            err_msg = f"Failed to upload image to COS: {upload_err}"
            logger.error(err_msg, exc_info=True)
            yield ToolResult(type="result", data=err_msg, result_for_assistant=err_msg)
            return

        # 2. 组装并发送 QQ 消息到管理员
        yield ToolResult(type="progress", data="图片已上传，开始通过 OneBot 推送 QQ 消息...")
        
        # 优先读取 settings 中的 admin_id 作为默认管理员
        from agent.core.config import settings
        sec_cfg = settings.get("security") or {}
        admin_id = os.getenv("QQ_ADMIN_ID", sec_cfg.get("admin_id", "1705919142"))

        nc_http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
        nc_token = os.getenv("NAPCAT_TOKEN", "")

        url = f"{nc_http_url}/send_private_msg"
        cq_image_msg = f"[CQ:image,file={cos_url}]"
        full_message = f"{message_prefix}\n{cq_image_msg}"

        payload = {
            "user_id": int(admin_id),
            "message": full_message
        }
        headers = {"Content-Type": "application/json"}
        if nc_token:
            headers["Authorization"] = f"Bearer {nc_token}"

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
                response_data = f"OneBot 发送成功: {res_text}"
            else:
                response_data = f"OneBot 发送失败: {res_text}"
        except Exception as send_err:
            response_data = f"OneBot 发送网络异常: {send_err}"
            logger.error(f"Failed to actively send QQ image via send_image_tool: {send_err}")

        output = f"【图片发送结果】\n状态: {'成功' if success else '失败'}\nCOS URL: {cos_url}\n详情: {response_data}"
        yield ToolResult(type="result", data=output, result_for_assistant=output)
