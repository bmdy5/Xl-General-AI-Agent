# -*- coding: utf-8 -*-
"""大脑主进程标准视觉工具 (Visual Tools)

封装了 Screenshot / Click / Type / Scroll 四大通用视觉工具，
并在此处拦截实现“交互式粉色指针 QQ 卡片安全审批”与超时自动熔断降级。
"""

import os
import io
import json
import base64
import logging
import asyncio
import aiohttp
import urllib.request
from PIL import Image, ImageDraw
from typing import Any, AsyncGenerator, Optional
from .base_tool import BaseTool, ToolResult

logger = logging.getLogger("agent.tools.visual")

async def send_qq_notification(msg: str) -> None:
    """向亮哥 QQ 主动发送安全审批卡片 (基于 OneBot CQ 码)"""
    admin_id = os.getenv("QQ_ADMIN_ID", "1705919142")
    nc_http_url = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
    nc_token = os.getenv("NAPCAT_TOKEN", "")
    
    url = f"{nc_http_url}/send_private_msg"
    payload = {
        "user_id": int(admin_id),
        "message": msg
    }
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


async def call_vision_gateway(port: int, action: str, payload: dict) -> str:
    """连通对应平台网关端口下发视觉 RPC 指令"""
    url = f"http://127.0.0.1:{port}/vision/{action}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=35) as resp:
                if resp.status == 400:
                    data = await resp.json()
                    raise RuntimeError(data.get("reason", "Operation rejected by user or browser preemption."))
                elif resp.status != 200:
                    raise RuntimeError(f"Visual Gateway returned status code {resp.status}")
                res = await resp.json()
                return res.get("screenshot_b64", "")
        except asyncio.TimeoutError:
            raise RuntimeError("Timeout communicating with visual gateway.")


def draw_pink_pointer(image_b64: str, x: int, y: int) -> str:
    """纯 PIL 算法在截图坐标上精细绘制小萤专属“粉色定位光环”与“粉色心形光标”"""
    try:
        header, encoded = image_b64.split(",", 1)
    except ValueError:
        encoded = image_b64

    img_data = base64.b64decode(encoded)
    img = Image.open(io.BytesIO(img_data)).convert("RGBA")
    
    # 建立半透明覆盖层
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    # 1. 绘制淡粉色半透明定位光环 (Halo)
    halo_color = (255, 128, 181, 75)
    draw.ellipse([x - 18, y - 18, x + 18, y + 18], outline=halo_color, width=3)
    
    # 2. 绘制粉色中心定位十字星
    pink_color = (255, 128, 181, 255)
    draw.line([x - 8, y, x + 8, y], fill=pink_color, width=2)
    draw.line([x, y - 8, x, y + 8], fill=pink_color, width=2)
    
    # 3. 在稍微偏移处绘制一个可爱的粉色心形指示气泡
    hx, hy = x + 10, y - 10
    heart_points = [
        (hx, hy + 2),
        (hx - 4, hy - 2),
        (hx - 8, hy - 2),
        (hx - 8, hy + 2),
        (hx - 4, hy + 6),
        (hx, hy + 10),
        (hx + 4, hy + 6),
        (hx + 8, hy + 2),
        (hx + 8, hy - 2),
        (hx + 4, hy - 2),
    ]
    draw.polygon(heart_points, fill=pink_color)
    
    combined = Image.alpha_composite(img, overlay).convert("RGB")
    output = io.BytesIO()
    combined.save(output, format="PNG")
    new_encoded = base64.b64encode(output.getvalue()).decode("utf-8")
    return new_encoded


class BrowserScreenshotTool(BaseTool):
    """通用网页截图工具"""
    @property
    def name(self) -> str:
        return "browser_screenshot"

    async def description(self) -> str:
        return "获取指定微服务网关常驻浏览器的 1280x800 Base64 视口截图。"

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
                "description": self.name + " 获取指定网关当前页面的 1280x800 网页截图，返回 Base64 编码。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {
                            "type": "integer",
                            "description": "微服务子网关的端口号，如抖音为 9000（抖音CDP为9222）"
                        }
                    },
                    "required": ["port"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if not input_args.get("port"):
            return {"result": False, "message": "Missing required port parameter"}
        return {"result": True, "message": ""}

    async def call(self, input_args: dict, context: Any = None) -> AsyncGenerator[ToolResult, None]:
        port = int(input_args.get("port"))
        try:
            b64 = await call_vision_gateway(port, "screenshot", {})
            yield ToolResult(type="result", data=b64)
        except Exception as e:
            yield ToolResult(type="result", data=f"Error taking screenshot: {e}")


class BrowserClickTool(BaseTool):
    """通用网页绝对坐标物理点击工具 (含 QQ 交互式红点卡片审批与超时熔断)"""
    @property
    def name(self) -> str:
        return "browser_click"

    async def description(self) -> str:
        return "在网页指定绝对像素坐标 (x, y) 处模拟平滑粉色光标滑移并执行物理点击。写动作受到亮哥 QQ 视觉审批安全策略拦截。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        # 高危物理操作，激活大语言模型本身的分类审批流
        return True

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.name + " 在指定视口坐标 (x, y) 执行物理左键点击。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {
                            "type": "integer",
                            "description": "微服务网关端口"
                        },
                        "x": {
                            "type": "integer",
                            "description": "目标 x 像素轴坐标"
                        },
                        "y": {
                            "type": "integer",
                            "description": "目标 y 像素轴坐标"
                        }
                    },
                    "required": ["port", "x", "y"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if input_args.get("port") is None or input_args.get("x") is None or input_args.get("y") is None:
            return {"result": False, "message": "Missing port, x, or y"}
        return {"result": True, "message": ""}

    async def call(self, input_args: dict, context: Any = None) -> AsyncGenerator[ToolResult, None]:
        port = int(input_args.get("port"))
        x = int(input_args.get("x"))
        y = int(input_args.get("y"))
        agent = context # context 传入的就是当前大模型 agent 实例
        
        try:
            # 1. 物理挂起第一步：向网关拉取最新视口大图
            b64_raw = await call_vision_gateway(port, "screenshot", {})
            if not b64_raw:
                raise RuntimeError("Failed to fetch initial view screenshot.")
            
            # 2. 物理挂起第二步：纯 PIL 高能贴图绘制粉色心形定位指针
            pure_b64 = draw_pink_pointer(b64_raw, x, y)
            
            # 3. 物理挂起第三步：大脑端挂起，拉起 Future 等待 QQ 全局劫持决议
            agent.approval_future = asyncio.get_running_loop().create_future()
            
            # OneBot 规范 CQ 码直发 Base64 图片
            cq_msg = (
                f"[CQ:image,file=base64://{pure_b64}]\n\n"
                f"🌸 **小萤专属视觉安全审批**\n"
                f"亮哥，我的粉色光标已经指向了网页的 `({x}, {y})` (图中粉色心形处)。\n"
                f"请在 120 秒内回复 **[y]** 授权我点击，或回复 **[n]** 拒绝。"
            )
            await send_qq_notification(cq_msg)
            
            # 4. 物理挂起第四步：120 秒高精度超时自愈退避控制
            try:
                is_approved = await asyncio.wait_for(agent.approval_future, timeout=120.0)
            except asyncio.TimeoutError:
                await send_qq_notification("⚠️ [安全熔断] 亮哥在 120 秒内未予答复，视觉会话强制熔断并退避让权。")
                raise RuntimeError("UserInterrupted: QQ 审批回复超时。")
            finally:
                agent.approval_future = None
                
            if not is_approved:
                await send_qq_notification("🚫 [安全终止] 亮哥拒绝了本次物理点击授权，视觉会话安全终止。")
                raise RuntimeError("UserInterrupted: 亮哥明确拒绝了点击动作。")
                
            # 5. 亮哥批准放行，下发真实 CDP 点击指令
            latest_b64 = await call_vision_gateway(port, "click", {"x": x, "y": y})
            yield ToolResult(type="result", data=latest_b64)
            
        except Exception as e:
            yield ToolResult(type="result", data=f"Error performing visual click: {e}")


class BrowserTypeTool(BaseTool):
    """通用输入打字工具"""
    @property
    def name(self) -> str:
        return "browser_type"

    async def description(self) -> str:
        return "在指定微服务网关常驻浏览器当前聚焦的输入框中模拟物理键盘打字输入。"

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
                "description": self.name + " 在当前获得焦点的页面输入框中输入指定文本内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {
                            "type": "integer",
                            "description": "微服务子网关端口"
                        },
                        "text": {
                            "type": "string",
                            "description": "要输入的文字内容"
                        }
                    },
                    "required": ["port", "text"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if input_args.get("port") is None or not input_args.get("text"):
            return {"result": False, "message": "Missing port or text"}
        return {"result": True, "message": ""}

    async def call(self, input_args: dict, context: Any = None) -> AsyncGenerator[ToolResult, None]:
        port = int(input_args.get("port"))
        text = str(input_args.get("text"))
        try:
            b64 = await call_vision_gateway(port, "type", {"text": text})
            yield ToolResult(type="result", data=b64)
        except Exception as e:
            yield ToolResult(type="result", data=f"Error typing text: {e}")


class BrowserAgentTool(BaseTool):
    """通用视觉Agent工具 — 让小萤用视觉方式操作任意浏览器页面"""

    @property
    def name(self) -> str:
        return "browser_agent"

    async def description(self) -> str:
        return "通用视觉操作引擎。截图→思考→点击/打字/滚动→验证，在网页上完成任意任务。自动从记忆系统学习。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return True  # 高危写操作，需要审批

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "【推荐优先使用】通用视觉浏览器操作引擎。自动连接浏览器→截图→看图→点击/打字/滚动→验证，在网页上完成任意任务。不需要任何网关进程，自己会连CDP操控浏览器。当你需要在网页上做任何操作时直接调用它，把任务描述清楚即可。例如：'打开百度搜索Python'、'在抖音私信里回复最新消息：你好呀'",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "要执行的任务，如'打开www.baidu.com搜索Python'、'在抖音私信里回复：你好'"
                        },
                        "cdp_url": {
                            "type": "string",
                            "description": "可选，浏览器CDP调试地址。不填则用默认(http://127.0.0.1:9222)。如果你知道有其他浏览器在运行，可以指定它的CDP端口"
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
            err_msg = str(e)
            if "Param Incorrect" in err_msg or "400" in err_msg:
                err_msg += " (提示：通常是多模态截图载荷过大或接口限制引发，建议检查图片尺寸压缩与模型配置。)"
            yield ToolResult(type="result", data=f"视觉任务失败: {err_msg}")


class BrowserScrollTool(BaseTool):
    """通用网页视口物理滚动工具"""
    @property
    def name(self) -> str:
        return "browser_scroll"

    async def description(self) -> str:
        return "在网页视口上执行物理滚轮滚动（如 up / down ）。"

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.name + " 物理滚动浏览器视口。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {
                            "type": "integer",
                            "description": "微服务网关端口"
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "滚动方向：'up' 向上，'down' 向下"
                        },
                        "amount": {
                            "type": "integer",
                            "description": "滚动的像素位移量"
                        }
                    },
                    "required": ["port", "direction", "amount"]
                }
            }
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if input_args.get("port") is None or not input_args.get("direction") or input_args.get("amount") is None:
            return {"result": False, "message": "Missing port, direction, or amount"}
        return {"result": True, "message": ""}

    async def call(self, input_args: dict, context: Any = None) -> AsyncGenerator[ToolResult, None]:
        port = int(input_args.get("port"))
        direction = str(input_args.get("direction"))
        amount = int(input_args.get("amount"))
        try:
            b64 = await call_vision_gateway(port, "scroll", {"direction": direction, "amount": amount})
            yield ToolResult(type="result", data=b64)
        except Exception as e:
            yield ToolResult(type="result", data=f"Error performing scroll: {e}")
