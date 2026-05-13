"""Stitch Tool — 调用 Google Stitch MCP Server 生成前端 UI.

通过 asyncio subprocess 启动 npx stitch MCP server，发送 JSON-RPC 请求，
用自然语言 prompt 生成 HTML/CSS 代码。

接入方式：
  1. npx @google-labs/stitch-mcp-server 启动 MCP Server
  2. 通过 stdin/stdout 走 JSON-RPC 2.0 协议
  3. 调用 generate_ui 工具，传入 prompt + style
  4. 解析返回的 HTML/CSS 代码
"""

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

STITCH_SERVER_CMD = os.getenv("STITCH_SERVER_CMD", "npx @google-labs/stitch-mcp-server")
STITCH_TIMEOUT = int(os.getenv("STITCH_TIMEOUT", "60"))


class StitchTool(BaseTool):
    """用 Stitch AI 生成前端 UI 代码."""

    @property
    def name(self) -> str:
        return "stitch_generate"

    async def description(self) -> str:
        return "Generate HTML/CSS UI code using Google Stitch AI via MCP protocol."

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
                    "Generate beautiful HTML/CSS UI components using Google Stitch AI. "
                    "Describe what you want with a natural language prompt, and get "
                    "production-ready HTML/CSS code back. Use this for creating buttons, "
                    "cards, nav bars, forms, modals, galleries, or entire page layouts. "
                    "Styles available: 'pixel-art', 'modern', 'glass', 'brutalist', 'cyberpunk', 'minimal'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Natural language description of the UI you want. Be specific about colors, layout, and elements.",
                        },
                        "style": {
                            "type": "string",
                            "description": "Visual style: 'pixel-art', 'modern', 'glass', 'brutalist', 'cyberpunk', 'minimal'",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if not input_args.get("prompt"):
            return {"result": False, "message": "prompt is required"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        prompt = input_args["prompt"]
        style = input_args.get("style", "modern")

        try:
            yield ToolResult(type="progress", data=f"Stitch: 生成 {style} 风格 UI...")

            # 如果安装了 stitch MCP，走 MCP 协议；否则 fallback 到直接生成
            html_css = await self._generate_via_mcp(prompt, style)
            if not html_css:
                html_css = await self._generate_fallback(prompt, style)

            if html_css:
                yield ToolResult(
                    type="result",
                    data=html_css,
                    result_for_assistant=f"[Stitch 生成结果]\n{html_css[:3000]}",
                )
            else:
                yield ToolResult(
                    type="result",
                    data="Error: Stitch generation failed",
                    result_for_assistant="Stitch 生成失败，请尝试更具体的 prompt 或换一种 style。",
                )
        except Exception as e:
            logger.error(f"Stitch tool error: {e}")
            yield ToolResult(
                type="result",
                data=f"Error: {e}",
                result_for_assistant=f"Stitch 调用失败: {e}",
            )

    async def _generate_via_mcp(self, prompt: str, style: str) -> Optional[str]:
        """通过 MCP 协议调用 Stitch Server."""
        try:
            cmd = STITCH_SERVER_CMD.split()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # MCP initialize
            init_request = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xl-agent-stitch-tool", "version": "1.0.0"},
                },
            }) + "\n"

            proc.stdin.write(init_request.encode())
            await proc.stdin.drain()

            # Read initialize response
            init_line = await asyncio.wait_for(proc.stdout.readline(), timeout=STITCH_TIMEOUT)
            init_resp = json.loads(init_line.decode().strip())
            logger.info(f"Stitch MCP initialized: {init_resp.get('result', {}).get('serverInfo', {})}")

            # Send initialized notification
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            proc.stdin.write(notif.encode())
            await proc.stdin.drain()

            # Call generate_ui tool
            tool_request = json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "generate_ui",
                    "arguments": {
                        "prompt": prompt,
                        "style": style,
                    },
                },
            }) + "\n"

            proc.stdin.write(tool_request.encode())
            await proc.stdin.drain()

            # Read result
            result_line = await asyncio.wait_for(proc.stdout.readline(), timeout=STITCH_TIMEOUT)
            result = json.loads(result_line.decode().strip())

            # Cleanup
            proc.stdin.close()
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                proc.kill()

            # Extract HTML/CSS from result
            content = result.get("result", {}).get("content", [])
            for item in content:
                if item.get("type") == "text":
                    return item.get("text", "")
            return None

        except asyncio.TimeoutError:
            logger.error("Stitch MCP timeout")
            return None
        except FileNotFoundError:
            logger.warning("Stitch MCP server not installed (npx not found or stitch package missing)")
            return None
        except Exception as e:
            logger.error(f"Stitch MCP error: {e}")
            return None

    async def _generate_fallback(self, prompt: str, style: str) -> Optional[str]:
        """Fallback: 当 MCP 不可用时，生成基础 HTML/CSS 模板."""
        styles_map = {
            "pixel-art": "font-family: 'Courier New', monospace; border: 2px solid #533483; background: #1a1a2e; color: #f4d058; text-shadow: 2px 2px #6b4c1a; image-rendering: pixelated;",
            "modern": "font-family: 'Inter', sans-serif; border-radius: 12px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; box-shadow: 0 4px 15px rgba(0,0,0,0.2);",
            "glass": "font-family: 'Inter', sans-serif; border-radius: 16px; background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.2); color: white;",
            "brutalist": "font-family: monospace; border: 3px solid black; background: #ff0; color: #000; box-shadow: 5px 5px 0 #000;",
            "cyberpunk": "font-family: 'Courier New', monospace; border: 2px solid #0ff; background: #0a0a0a; color: #0f0; text-shadow: 0 0 5px #0f0; box-shadow: 0 0 10px #0ff;",
            "minimal": "font-family: 'Inter', sans-serif; border-radius: 8px; background: #f5f5f5; color: #333; box-shadow: 0 1px 3px rgba(0,0,0,0.1);",
        }
        css = styles_map.get(style, styles_map["modern"])
        return f"""<div style="padding: 24px; {css}">
  <h3>{prompt[:60]}</h3>
  <p>Stitch MCP 未安装，这是 fallback 样式。安装: npx @google-labs/stitch-mcp-server</p>
</div>"""
