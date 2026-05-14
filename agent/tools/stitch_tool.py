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

STITCH_SERVER_CMD = os.getenv("STITCH_SERVER_CMD", "/Users/xiaofeng/.npm/_npx/d7bcf1e9427e7044/node_modules/.bin/stitch-mcp")
STITCH_TIMEOUT = int(os.getenv("STITCH_TIMEOUT", "200"))
CLOUDSDK_CONFIG = "/Users/xiaofeng/.stitch-mcp/config"

# 固定 env 设置，避免每次调用时环境变量传递问题
def _get_stitch_env(token: str = "") -> dict:
    """构建 Stitch MCP 子进程所需的环境变量."""
    env = os.environ.copy()
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
    return env

# ── MCP 协议助手 ──

def _encode_mcp(msg: dict) -> bytes:
    """编码 MCP JSON-RPC 消息（支持两种传输格式）. """
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return body + b"\n"  # 换行分隔（部分 server 接受）

def _encode_mcp_with_header(msg: dict) -> bytes:
    """编码 MCP 消息 + Content-Length header（标准格式）. """
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    return header + body

async def _read_mcp_message(stream: asyncio.StreamReader, timeout: float = STITCH_TIMEOUT) -> dict:
    """从 MCP 子进程 stdout 读取一条 JSON-RPC 消息.

    Stitch MCP 输出 JSON 时不带换行，所以不能用 readline()。
    改用 read() 直到可解析为完整 JSON。
    """
    raw = b""
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            chunk = await asyncio.wait_for(stream.read(4096), timeout=min(5, max(0.5, remaining)))
        except asyncio.TimeoutError:
            continue  # timeout waiting for data, keep trying
        if not chunk:
            raise ConnectionError("MCP子进程已关闭")
        raw += chunk

        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        # Content-Length header format
        if "Content-Length:" in text:
            for line in text.split("\n"):
                if line.strip().startswith("Content-Length:"):
                    length = int(line.split(":")[1].strip())
                    if "\r\n\r\n" in text:
                        body_start = text.index("\r\n\r\n") + 4
                        body = text[body_start:]
                        if len(body) >= length:
                            try:
                                return json.loads(body[:length])
                            except json.JSONDecodeError:
                                pass
                    break

        # 纯 JSON 格式 — 找到第一个 { 并尝试解析
        brace_idx = text.find("{")
        if brace_idx >= 0:
            candidate = text[brace_idx:]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass  # 还不完整，继续收

        if len(raw) > 500_000:
            break

    raise TimeoutError(f"MCP 响应超时 (已收 {len(raw)} bytes): {raw[:200]}")


async def _send_mcp(proc, msg: dict):
    """发送 MCP 消息（尝试两种格式）. """
    data = _encode_mcp(msg)
    proc.stdin.write(data)
    await proc.stdin.drain()
STITCH_QUOTA_PROJECT = os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))


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
        return 180  # Stitch 生成慢，需要更长的超时

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Generate HTML/CSS UI components using Google Stitch AI. Styles: pixel-art, modern, glass, brutalist, cyberpunk, minimal.",
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
            # 按优先级尝试：MCP → Fallback
            html_css = await self._generate_via_mcp(prompt, style)
            if html_css is None:
                # 再次尝试带超时的方式
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
        """通过 Stitch REST API 直接生成 UI（绕过 MCP proxy 的 quota project 问题）."""
        import subprocess
        try:
            gcloud_bin = "/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin/gcloud"
            creds_file = "/Users/xiaofeng/.config/gcloud/application_default_credentials.json"
            config = "/Users/xiaofeng/.stitch-mcp/config"
            quota_project = STITCH_QUOTA_PROJECT or "stitch-496215"
            venv_python = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3"

            # 获取 Access Token
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(None, lambda: subprocess.run(
                [gcloud_bin, "auth", "application-default", "print-access-token"],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "GOOGLE_APPLICATION_CREDENTIALS": creds_file,
                     "CLOUDSDK_CONFIG": config, "CLOUDSDK_PYTHON": venv_python,
                     "GOOGLE_CLOUD_PROJECT": "stitch-496215",
                     "PATH": f"{os.path.dirname(gcloud_bin)}:/Users/xiaofeng/.nvm/versions/node/v25.8.0/bin:{os.environ.get('PATH','')}"}))
            token = proc.stdout.strip()
            if not token:
                logger.warning("No Stitch access token")
                return None

            headers = {"Authorization": f"Bearer {token}",
                       "x-goog-user-project": quota_project,
                       "Content-Type": "application/json"}
            base_url = "https://stitch.googleapis.com/v1"

            # 列出或创建项目
            list_resp = await loop.run_in_executor(None,
                lambda: __import__("urllib.request").request.urlopen(
                    __import__("urllib.request").request.Request(
                        f"{base_url}/projects", headers=headers), timeout=10))
            projects = json.loads(list_resp.read().decode())
            proj_list = projects.get("projects", [])
            if not proj_list:
                # 创建项目
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

            # 生成 screen
            gen_req = __import__("urllib.request").request.Request(
                f"{base_url}/projects/{numeric_id}/screens:generateFromText",
                data=json.dumps({"prompt": prompt, "deviceType": "DESKTOP",
                    "modelId": "GEMINI_3_1_PRO"}).encode(), headers=headers)
            gen_resp = await loop.run_in_executor(None,
                lambda: __import__("urllib.request").request.urlopen(gen_req, timeout=60))
            screen = json.loads(gen_resp.read().decode())
            screen_id = screen["name"]  # projects/xxx/screens/yyy
            logger.info(f"Stitch screen: {screen_id}")

            # 获取 screen 代码
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
            # 获取 OAuth Access Token（优先于 API Key，项目操作需要）
            import subprocess as _sp
            loop = asyncio.get_running_loop()
            # 不预取 token，让 stitch-mcp 自己调 gcloud 获取
            # （预取的 token 可能在子进程启动时已过期）
            env = _get_stitch_env()

            # 直接用 bash 脚本方式调用（已验证稳定）
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
            return result.stdout
            cmd = STITCH_SERVER_CMD.split()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # 完整请求: init + 通知 + tools/call
            full_request = (
                json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                    "params":{"protocolVersion":"2024-11-05","capabilities":{},
                        "clientInfo":{"name":"xl-agent","version":"2.0"}}})
                + "\n"
                + json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})
                + "\n"
                + json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call",
                    "params":{
                        "name":"generate_screen_from_text",
                        "arguments":{
                            "projectId":"9177609784991880809",
                            "prompt": prompt,
                            "deviceType":"DESKTOP",
                            "modelId":"GEMINI_3_1_PRO",
                        },
                    },
                })
                + "\n"
            )
            proc.stdin.write(full_request.encode())
            await proc.stdin.drain()
            proc.stdin.close()  # 关闭 stdin，让服务端处理

            # 读取完整输出
            out_data = await asyncio.wait_for(proc.stdout.read(), timeout=STITCH_TIMEOUT)
            text = out_data.decode("utf-8", errors="replace").strip()
            await proc.wait()

            # 尝试从输出中解析 JSON
            # 格式可能是: init_resp + tools_call_resp（两个 JSON 对象）
            lines = text.split("\n")
            for line in lines:
                try:
                    data = json.loads(line)
                    if data.get("id") == 2:  # tools/call 的响应
                        # 提取结果
                        content = data.get("result", {}).get("content", [])
                        for item in content:
                            if item.get("type") == "text":
                                raw = item.get("text", "")
                                try:
                                    inner = json.loads(raw)
                                    # 尝试下载 HTML 代码
                                    for out in inner.get("outputComponents", []):
                                        for d in out.get("design", {}).get("screens", []):
                                            code_url = d.get("htmlCode", {}).get("downloadUrl", "")
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
                                except:
                                    pass
                                return raw[:2000]
                except json.JSONDecodeError:
                    pass

            return None
            await _send_mcp(proc, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xl-agent", "version": "2.0"},
                },
            })

            # Read initialize response
            init_resp = await _read_mcp_message(proc.stdout)
            logger.info(f"Stitch MCP initialized: {init_resp.get('result', {}).get('serverInfo', {})}")

            # Send initialized notification
            await _send_mcp(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            await asyncio.sleep(0.5)  # 给 server 一点时间处理

            # List tools to discover available commands
            await _send_mcp(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools_resp = await _read_mcp_message(proc.stdout)
            tools = tools_resp.get("result", {}).get("tools", [])
            tool_names = [t["name"] for t in tools]
            logger.info(f"Stitch tools: {tool_names}")

            # 选择正确的工具名
            tool_name = "generate_screen_from_text"
            if tool_name not in tool_names:
                # 尝试备选工具名
                for fallback in ["generate_ui", "generate_screen", "create_screen"]:
                    if fallback in tool_names:
                        tool_name = fallback
                        break

            # Call generate tool
            await _send_mcp(proc, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "projectId": "9177609784991880809",
                        "prompt": prompt,
                        "deviceType": "DESKTOP",
                        "modelId": "GEMINI_3_1_PRO",
                    },
                },
            })

            # Read result
            result = await _read_mcp_message(proc.stdout)

            # Cleanup
            proc.stdin.close()
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                proc.kill()

            # Extract and simplify Stitch result for XL
            content = result.get("result", {}).get("content", [])
            for item in content:
                if item.get("type") == "text":
                    raw = item.get("text", "")
                    try:
                        data = json.loads(raw)
                        # Extract key info from Stitch response
                        summary = {
                            "sessionId": data.get("sessionId", ""),
                            "projectId": data.get("projectId", ""),
                        }
                        outputs = data.get("outputComponents", [])
                        for out in outputs:
                            designs = out.get("design", {}).get("screens", [])
                            for d in designs:
                                sc = d.get("screenshot", {})
                                code = d.get("htmlCode", {})
                                if "downloadUrl" in sc:
                                    sc_url = sc["downloadUrl"][:80]
                                else:
                                    sc_url = ""
                                if "downloadUrl" in code:
                                    code_url = code["downloadUrl"][:80]
                                else:
                                    code_url = ""
                                summary["screenshot_url"] = sc_url
                                summary["code_url"] = code_url
                        if "title" in data:
                            summary["title"] = data.get("title", "")

                        # 下载实际的 HTML 代码
                        if code_url:
                            try:
                                import urllib.request, ssl
                                ctx = ssl.create_default_context()
                                ctx.check_hostname = False
                                ctx.verify_mode = ssl.CERT_NONE
                                full_url = code_url if code_url.startswith("http") else f"https:{code_url}"
                                req = urllib.request.Request(full_url)
                                with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                                    html_code = resp.read().decode("utf-8", errors="replace")
                                if html_code and len(html_code) > 100:
                                    return html_code
                            except Exception as e:
                                logger.warning(f"下载 Stitch 代码失败: {e}")

                        return f"[Stitch 完成] 预览: {sc_url}... 代码: {code_url}... (project: {summary['projectId']})"
                    except (json.JSONDecodeError, KeyError):
                        return raw[:500]  # Fallback: show first 500 chars
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
  <p>Stitch MCP 已连接，但需要 OAuth 以完成项目操作。确认或运行: npx @_davideast/stitch-mcp init --client cc</p>
</div>"""
