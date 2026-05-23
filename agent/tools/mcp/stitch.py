import json
import logging
import os
import asyncio
from typing import Any, AsyncGenerator, Optional
from ..base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.mcp.stitch")

STITCH_SERVER_CMD = os.getenv("STITCH_SERVER_CMD", "/Users/xiaofeng/.npm/_npx/d7bcf1e9427e7044/node_modules/.bin/stitch-mcp")
STITCH_TIMEOUT = int(os.getenv("STITCH_TIMEOUT", "200"))
CLOUDSDK_CONFIG = "/Users/xiaofeng/.stitch-mcp/config"
STITCH_QUOTA_PROJECT = os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))

def _get_stitch_env(token: str = "") -> dict:
    """构建 Stitch MCP 子进程所需的环境变量."""
    env = dict(os.environ)
    gcloud_bin = "/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin"
    node_bin = "/Users/xiaofeng/.nvm/versions/node/v25.8.0/bin"
    venv_python = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3"

    env["PATH"] = f"{gcloud_bin}:{node_bin}:{env.get('PATH', '/usr/bin:/bin')}"
    env["GOOGLE_CLOUD_PROJECT"] = "stitch-496215"
    env["CLOUDSDK_CONFIG"] = "/Users/xiaofeng/.stitch-mcp/config"
    env["CLOUDSDK_PYTHON"] = venv_python
    if token:
        env["STITCH_ACCESS_TOKEN"] = token
    return env


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

    @property
    def timeout(self) -> int:
        return 180

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "使用 Google Stitch AI 生成 HTML/CSS 前端 UI 组件。风格自由定义，支持任意视觉风格。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "用中文描述你想要的 UI，尽量具体说明颜色、布局、元素和交互效果。",
                        },
                        "style": {
                            "type": "string",
                            "description": "自定义视觉风格，自由描述。例如：'赛博朋克霓虹风'、'苹果极简风'、'像素复古游戏风'、'玻璃拟态风'、'暗黑科技风'、'清新自然风'、'中式国潮风'等，不限于此。",
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
        style = input_args.get("style", "")

        try:
            风格提示 = f"{style}风格" if style else "默认风格"
            yield ToolResult(type="progress", data=f"Stitch: 正在生成 {风格提示} 的UI...")

            html_css = await self._generate_via_mcp(prompt, style)
            if html_css is None:
                html_css = await self._generate_via_mcp(prompt, style)
            if not html_css or html_css.startswith("[Stitch"):
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

    async def _generate_via_rest_api(self, prompt: str, style: str) -> Optional[str]:
        """通过 Stitch REST API 直接生成 UI."""
        import subprocess
        try:
            gcloud_bin = "/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin/gcloud"
            creds_file = "/Users/xiaofeng/.config/gcloud/application_default_credentials.json"
            config = "/Users/xiaofeng/.stitch-mcp/config"
            quota_project = STITCH_QUOTA_PROJECT or "stitch-496215"
            venv_python = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3"

            env_copy = dict(os.environ)
            env_copy["GOOGLE_APPLICATION_CREDENTIALS"] = creds_file
            env_copy["CLOUDSDK_CONFIG"] = config
            env_copy["CLOUDSDK_PYTHON"] = venv_python
            env_copy["GOOGLE_CLOUD_PROJECT"] = "stitch-496215"
            env_copy["PATH"] = f"{os.path.dirname(gcloud_bin)}:/Users/xiaofeng/.nvm/versions/node/v25.8.0/bin:{os.environ.get('PATH','')}"

            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(None, lambda: subprocess.run(
                [gcloud_bin, "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=10,
                env=env_copy))
            token = proc.stdout.strip()
            if not token:
                logger.warning("No Stitch access token")
                return None

            headers = {"Authorization": f"Bearer {token}",
                       "x-goog-user-project": quota_project,
                       "Content-Type": "application/json"}
            base_url = "https://stitch.googleapis.com/v1"

            list_resp = await loop.run_in_executor(None,
                lambda: __import__("urllib.request").request.urlopen(
                    __import__("urllib.request").request.Request(
                        f"{base_url}/projects", headers=headers), timeout=10))
            projects = json.loads(list_resp.read().decode())
            proj_list = projects.get("projects", [])
            if not proj_list:
                create_req = __import__("urllib.request").request.Request(
                    f"{base_url}/projects",
                    data=json.dumps({"title": "xl-generated-ui",
                        "projectType": "TEXT_TO_UI_PRO"}).encode(), headers=headers)
                create_resp = await loop.run_in_executor(None,
                    lambda: __import__("urllib.request").request.urlopen(create_req, timeout=10))
                proj = json.loads(create_resp.read().decode())
                project_id = proj["name"]
            else:
                project_id = proj_list[0]["name"]

            numeric_id = project_id.split("/")[-1]
            logger.info(f"Stitch project: {project_id}")

            gen_req = __import__("urllib.request").request.Request(
                f"{base_url}/projects/{numeric_id}/screens:generateFromText",
                data=json.dumps({"prompt": prompt, "deviceType": "DESKTOP",
                    "modelId": "GEMINI_3_1_PRO"}).encode(), headers=headers)
            gen_resp = await loop.run_in_executor(None,
                lambda: __import__("urllib.request").request.urlopen(gen_req, timeout=60))
            screen = json.loads(gen_resp.read().decode())
            screen_id = screen["name"]
            logger.info(f"Stitch screen: {screen_id}")

            get_req = __import__("urllib.request").request.Request(
                f"{base_url}/{screen_id}?view=CODE", headers=headers)
            get_resp = await loop.run_in_executor(None,
                lambda: __import__("urllib.request").request.urlopen(get_req, timeout=10))
            screen_data = json.loads(get_resp.read().decode())
            html = screen_data.get("htmlContent", "")
            css = screen_data.get("cssContent", "")
            if html:
                return f"<style>{css}</style>\n{html}"
            return None
        except Exception as e:
            logger.warning(f"Stitch REST API error: {e}")
            return None

    async def _generate_via_mcp(self, prompt: str, style: str) -> Optional[str]:
        """通过 MCP 协议调用 Stitch Server."""
        try:
            loop = asyncio.get_running_loop()
            import subprocess as _sp
            
            script = f"""
export GOOGLE_CLOUD_PROJECT=stitch-496215
export PATH="/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin:/Users/xiaofeng/.nvm/versions/node/v25.8.0/bin:$PATH"
export CLOUDSDK_PYTHON=/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3
cat <<'MCP_INPUT' | /Users/xiaofeng/.npm/_npx/d7bcf1e9427e7044/node_modules/.bin/stitch-mcp 2>/dev/null
{json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"xl-agent","version":"2.0"}}})}
{json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})}
{json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"generate_screen_from_text","arguments":{"projectId":"9177609784991880809","prompt":prompt,"deviceType":"DESKTOP","modelId":"GEMINI_3_1_PRO"}}})}
MCP_INPUT"""
            result = await loop.run_in_executor(None, lambda: _sp.run(
                ["bash", "-c", script],
                capture_output=True, text=True, timeout=STITCH_TIMEOUT))
            if result.returncode != 0:
                logger.warning(f"Stitch shell failed: {result.stderr[:200]}")
                return None

            for line in result.stdout.split("\n"):
                try:
                    data = json.loads(line)
                    if data.get("id") == 2:
                        for item in data.get("result", {}).get("content", []):
                            if item.get("type") == "text":
                                raw = item.get("text", "")
                                try:
                                    inner = json.loads(raw)
                                    for comp in inner.get("outputComponents", []):
                                        for screen in comp.get("design", {}).get("screens", []):
                                            code_url = screen.get("htmlCode", {}).get("downloadUrl", "")
                                            if code_url:
                                                import urllib.request, ssl
                                                ctx = ssl.create_default_context()
                                                ctx.check_hostname = False
                                                ctx.verify_mode = ssl.CERT_NONE
                                                full_url = code_url if code_url.startswith("http") else f"https:{code_url}"
                                                req = urllib.request.Request(full_url)
                                                with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                                                    html = resp.read().decode("utf-8", errors="replace")
                                                if html and len(html) > 100:
                                                    return html
                                except json.JSONDecodeError:
                                    pass
                except json.JSONDecodeError:
                    pass
            return None

        except asyncio.TimeoutError:
            logger.error("Stitch MCP timeout")
            return None
        except FileNotFoundError:
            logger.warning("Stitch MCP server not installed")
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
  <p>Stitch MCP 已连接，但需要 OAuth 以完成项目操作。确认或运行: npx @_davideast/stitch-mcp init --client cc</p>
</div>"""
