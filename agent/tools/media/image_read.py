"""Vision tool — 借视觉模型当 agent 的眼睛.

支持本地图片路径。base64 编码 → 调 Mimo vision API → 返回文字描述。
"""

import base64
import logging
import mimetypes
import os
import operator
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Mimo vision API 配置
VISION_API_BASE = "https://api.xiaomimimo.com/v1"
VISION_MODEL = "mimo-v2.5"
VISION_API_KEY = os.getenv("MYAGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or ""

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


class ReadImageTool(BaseTool):
    """用视觉模型描述图片内容，返回文字给主 agent 分析."""

    @property
    def name(self) -> str:
        return "read_image"

    async def description(self) -> str:
        return "调用视觉模型读取图片内容并返回详细的文字描述。"

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
                    "调用视觉大模型读取并分析图片文件。 "
                    "可用于理解截图、图表、报错信息、架构图、UI 界面或任何视觉内容。"
                    "模型会返回极其详细的图片内容文字描述。"
                    "支持的格式：png, jpg, jpeg, gif, webp, bmp。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "本地图片文件的绝对路径。",
                        },
                        "focus": {
                            "type": "string",
                            "description": (
                                "希望视觉模型重点关注的内容。"
                                "例如：'报错信息', '代码', '架构', 'UI 布局', '流程图', '文字内容'。"
                                "如果留空，模型将提供全局的详细描述。"
                            ),
                        },
                    },
                    "required": ["file_path"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        file_path = input_args.get("file_path", "")
        if not file_path:
            return {"result": False, "message": "file_path is required"}
        path = Path(file_path)
        if not path.is_absolute():
            return {"result": False, "message": "file_path must be absolute"}
        if not path.exists():
            return {"result": False, "message": f"File not found: {file_path}"}
        ext = path.suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            return {"result": False, "message": f"Unsupported format: {ext}. Supported: {', '.join(IMAGE_EXTENSIONS)}"}
        if path.stat().st_size > 20971520:
            return {"result": False, "message": "Image too large (max 20MB)"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        file_path = input_args["file_path"]
        focus = input_args.get("focus", "").strip()
        path = Path(file_path)

        try:
            # 读取并编码图片（超过1MB自动压缩）
            raw = path.read_bytes()
            mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
            if len(raw) > 1000000:
                try:
                    from PIL import Image
                    import io as _io
                    img = Image.open(_io.BytesIO(raw))
                    scale = min(800 / max(img.width, img.height), 1.0)
                    img = img.resize((int(operator.mul(img.width, scale)), int(operator.mul(img.height, scale))), Image.LANCZOS)
                    buf = _io.BytesIO()
                    img.save(buf, format='PNG', optimize=True)
                    raw = buf.getvalue()
                    logger.info(f"Image compressed: {len(raw)} bytes")
                except ImportError:
                    pass  # PIL not available, send original
            b64 = base64.b64encode(raw).decode("utf-8")

            # 构建 prompt
            if focus:
                user_prompt = f"请详细描述这张图片的内容，重点关注：{focus}。如果是代码或报错信息，请逐行转录。"
            else:
                user_prompt = "请详细描述这张图片的内容。如果是截图，请描述界面布局、文字内容、按钮位置等。如果是图表，请解释其含义。"

            # 调 vision API
            import asyncio
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, self._call_vision_api, b64, mime_type, user_prompt
            )

            if result:
                yield ToolResult(
                    type="result",
                    data=result,
                    result_for_assistant=f"[图片分析结果]\n{result}",
                )
            else:
                yield ToolResult(
                    type="result",
                    data="Error: vision API returned empty response",
                    result_for_assistant="图片分析失败：API 返回空结果。",
                )

        except Exception as e:
            logger.error(f"Read image failed: {e}")
            yield ToolResult(
                type="result",
                data=f"Error: {e}",
                result_for_assistant=f"图片分析失败：{e}",
            )

    def _call_vision_api(self, b64: str, mime_type: str, user_prompt: str) -> Optional[str]:
        """同步调用 Mimo vision API."""
        import urllib.request
        import json

        body = json.dumps({
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.1,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{VISION_API_BASE}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {VISION_API_KEY}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Vision API call failed: {e}")
            return None
