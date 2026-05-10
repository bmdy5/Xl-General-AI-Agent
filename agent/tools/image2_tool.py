"""Image2 生图工具 — 调用老肖的 Image2 中转站 API 生成像素贴图.

用于装修像素办公室：生成家具、地板纹理、墙壁、角色精灵等。
"""

import asyncio
import base64
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

IMAGE2_BASE = os.getenv("IMAGE2_BASE", "https://visionary.ai")
IMAGE2_USER = os.getenv("IMAGE2_USER", "")
IMAGE2_PASS = os.getenv("IMAGE2_PASS", "")

ASSETS_DIR = Path(__file__).parent.parent / "dashboard_v2" / "assets"


class Image2GenerateTool(BaseTool):
    """调用 Image2 API 生成像素贴图."""

    @property
    def name(self) -> str:
        return "image2_generate"

    async def description(self) -> str:
        return "Generate pixel art textures using the Image2 API for office decoration."

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
                "description": (
                    "Generate pixel art images using XiaoFeng's Image2 API. "
                    "Use this to create textures for the pixel office dashboard: "
                    "furniture, floor tiles, wall textures, character sprites, decorations. "
                    "The generated image will be saved to the dashboard assets folder. "
                    "Quality: 'standard'(5pts), 'hd'(10pts), 'master'(15pts). "
                    "Aspect ratios: '1:1' for sprites/icons, '16:9' for wide textures."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Detailed pixel art prompt. Include 'pixel art, Stardew Valley style, "
                                "16-bit, top-down view, game asset'. Be specific about colors, size, and usage."
                            ),
                        },
                        "quality": {
                            "type": "string",
                            "enum": ["standard", "hd", "master"],
                            "description": "standard=5pts, hd=10pts, master=15pts",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "16:9", "9:16"],
                            "description": "1:1 for sprites/icons, 16:9 for wide textures",
                        },
                        "style": {
                            "type": "string",
                            "description": "Style ID, default is 'default'",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if not input_args.get("prompt"):
            return {"result": False, "message": "prompt is required"}
        if not IMAGE2_USER or not IMAGE2_PASS:
            return {"result": False, "message": "Image2 credentials not configured in .env"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        prompt = input_args["prompt"]
        quality = input_args.get("quality", "standard")
        aspect = input_args.get("aspect_ratio", "1:1")
        style = input_args.get("style", "default")

        loop = asyncio.get_running_loop()

        try:
            # Step 1: Login
            yield ToolResult(type="progress", data="登录 Image2...")
            token = await loop.run_in_executor(None, self._login)
            if not token:
                yield ToolResult(type="result", data="Error: Image2 login failed")
                return

            # Step 2: Generate
            yield ToolResult(type="progress", data=f"生成中: {prompt[:60]}...")
            task_id = await loop.run_in_executor(None, self._generate, token, prompt, quality, aspect, style)
            if not task_id:
                yield ToolResult(type="result", data="Error: generate failed")
                return

            # Step 3: Poll status
            image_url = None
            for i in range(30):
                await asyncio.sleep(3)
                status = await loop.run_in_executor(None, self._poll, token, task_id)
                if status and status.get("status") == "success":
                    image_url = status.get("image_url")
                    break
                elif status and status.get("status") == "failed":
                    yield ToolResult(type="result", data=f"生成失败: {status.get('error', 'unknown')}")
                    return
                if i % 5 == 0:
                    yield ToolResult(type="progress", data=f"等待中... ({i*3}s)")

            if not image_url:
                yield ToolResult(type="result", data="Error: timeout waiting for image")
                return

            # Step 4: Download
            yield ToolResult(type="progress", data="下载图片...")
            filename = await loop.run_in_executor(None, self._download, image_url, prompt)
            if filename:
                yield ToolResult(
                    type="result",
                    data=f"✅ 生成完成: {filename}",
                    result_for_assistant=(
                        f"图片生成成功！保存在 dashboard_v2/assets/{filename}\n"
                        f"Prompt: {prompt}\n"
                        f"URL: {image_url}\n"
                        f"可以在 office.html 中引用此图片替换对应位置的 fillRect。"
                    ),
                )
            else:
                yield ToolResult(type="result", data=f"生成完成但下载失败: {image_url}")

        except Exception as e:
            logger.error(f"Image2 tool error: {e}")
            yield ToolResult(type="result", data=f"Error: {e}")

    def _login(self) -> Optional[str]:
        body = json.dumps({"username": IMAGE2_USER, "password": IMAGE2_PASS}).encode()
        req = urllib.request.Request(
            f"{IMAGE2_BASE}/api/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data.get("access_token")
        except Exception as e:
            logger.error(f"Image2 login failed: {e}")
            return None

    def _generate(self, token, prompt, quality, aspect, style):
        body = json.dumps({
            "prompt": prompt, "quality": quality,
            "aspect_ratio": aspect, "style": style,
        }).encode()
        req = urllib.request.Request(
            f"{IMAGE2_BASE}/api/image/generate",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data.get("id")
        except Exception as e:
            logger.error(f"Generate failed: {e}")
            return None

    def _poll(self, token, task_id):
        req = urllib.request.Request(
            f"{IMAGE2_BASE}/api/image/status/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def _download(self, url, prompt):
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in prompt[:30] if c.isalnum() or c in " _-").strip().replace(" ", "_")
        filename = f"gen_{safe}.png"
        filepath = ASSETS_DIR / filename
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "XL-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                filepath.write_bytes(resp.read())
            return filename
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None
