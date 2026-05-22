"""小萤专属 NotebookLM 播客全自动投喂与查询工具.

提供 feed_text 发起云端生成和 query_status 触发常规 Chrome 捕获下载两大功能。
"""

import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult
from agent.auto_podcast import NotebookLMMCPClient, ACTIVE_PODCAST_JSON, call_tool_with_retry

logger = logging.getLogger(__name__)


class NotebookLMTool(BaseTool):
    """NotebookLM 播客助手工具，支持全自动投喂生成与下载捕获."""

    @property
    def name(self) -> str:
        return "notebooklm"

    async def description(self) -> str:
        return (
            "Interact with NotebookLM to automatically feed text to generate a Chinese "
            "technical podcast, or query the generation status to trigger active Chrome "
            "download and environment cleanup."
        )

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        # feed_text 涉及云端写入和发起，需要权限确认；query_status 静默直接执行，无需打扰亮哥
        if input_args and input_args.get("action") == "feed_text":
            return True
        return False

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Manage NotebookLM podcast workflows. Supported actions:\n"
                    "- 'feed_text': Feed custom text to cloud NotebookLM and trigger dual-host Chinese tech podcast generation.\n"
                    "- 'query_status': Check current status of the active podcast task and trigger real Chrome active download if finished."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["feed_text", "query_status"],
                            "description": "The action to perform.",
                        },
                        "text": {
                            "type": "string",
                            "description": "The custom tech note or comparison text content to feed into NotebookLM (required for feed_text action).",
                        },
                        "topic": {
                            "type": "string",
                            "description": "The topic or title of this tech podcast (required for feed_text action).",
                        },
                        "high_frequency": {
                            "type": "boolean",
                            "description": "Whether to query status in high frequency (30s interval). Default is true to allow quick QQ feedback.",
                        }
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        action = input_args.get("action")
        if action not in ("feed_text", "query_status"):
            return {"result": False, "message": "action must be 'feed_text' or 'query_status'"}

        if action == "feed_text":
            if not input_args.get("text"):
                return {"result": False, "message": "text content is required for feed_text action"}
            if not input_args.get("topic"):
                return {"result": False, "message": "topic is required for feed_text action"}

        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]

        yield ToolResult(type="progress", data=f"正在拉起 NotebookLM [{action}] 流程...")

        try:
            if action == "feed_text":
                text = input_args["text"]
                topic = input_args["topic"]
                high_frequency = input_args.get("high_frequency", True)

                # 1. 启动 NotebookLM MCP 客户端
                client = NotebookLMMCPClient()
                await client.start()

                notebook_id = None
                try:
                    # 2. 在云端创建笔记本
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    title = f"亮哥极客播客-专题-{topic}-{today_str}"
                    yield ToolResult(type="progress", data=f"正在云端创建笔记本: {title}...")
                    
                    create_res = await call_tool_with_retry(client, "notebook_create", {"title": title})
                    match_id = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', create_res, re.IGNORECASE)
                    if not match_id:
                        raise ValueError(f"无法解析 notebook_id: {create_res}")
                    notebook_id = match_id.group(0)
                    yield ToolResult(type="progress", data=f"云端笔记本创建成功，ID: {notebook_id}")

                    # 3. 添加用户提供的自定义文本
                    yield ToolResult(type="progress", data="正在投喂正文内容...")
                    await call_tool_with_retry(client, "notebook_add_text", {
                        "notebook_id": notebook_id,
                        "text": text,
                        "title": f"专题深度笔记"
                    })

                    # 4. 请求云端开始生成双人中文极客技术播客
                    yield ToolResult(type="progress", data="正在发起音频播客生成任务...")
                    focus_prompt = f"请针对本篇关于【{topic}】的深度极客研究笔记，展开一场极富思考深度、专业硬核且言语幽默自然的中文技术对谈。两个主持人要像真正的顶尖架构师和高级开发员那样，通过互相探讨、质疑和案例印证，为听众亮哥（收听者）提供醍醐灌顶的技术启发，拒绝AI腔调、翻译腔和浅尝辄止。"
                    
                    overview_res = await call_tool_with_retry(client, "audio_overview_create", {
                        "notebook_id": notebook_id,
                        "format": "deep_dive",
                        "language": "zh",
                        "focus_prompt": focus_prompt,
                        "confirm": True
                    })

                    # 5. 写入持久化状态机 active_podcast.json，激活 gateway.py 守护轮询协程
                    state = {
                        "notebook_id": notebook_id,
                        "query_count": 0,
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "status": "generating",
                        "last_query_time": time.time(),
                        "created_at": time.time(),
                        "debug_mode": high_frequency,
                        "topic": topic
                    }
                    
                    os.makedirs(os.path.dirname(ACTIVE_PODCAST_JSON), exist_ok=True)
                    with open(ACTIVE_PODCAST_JSON, "w", encoding="utf-8") as f:
                        json.dump(state, f, ensure_ascii=False, indent=2)

                    msg = f"🎉 成功为专题【{topic}】拉起 NotebookLM 播客生成！已记录到状态机中，后台轮询守护已激活。"
                    yield ToolResult(
                        type="result",
                        data=msg,
                        result_for_assistant=(
                            f"已成功为主题【{topic}】在云端创建笔记本，并成功拉起双人中文技术播客生成任务！\n"
                            f"笔记本 ID: {notebook_id}，本地状态机已持久化。\n"
                            f"我会在后台以 30 秒间隔帮您高频轮询查询。一旦云端生成好，我会通过您本地的常规 Chrome 静默下载音频，并直接在 QQ 中把语音推送给您，请稍候！"
                        ),
                    )

                except Exception as e:
                    if notebook_id:
                        try:
                            await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
                        except Exception:
                            pass
                    raise e
                finally:
                    await client.close()

            elif action == "query_status":
                if not os.path.exists(ACTIVE_PODCAST_JSON):
                    yield ToolResult(
                        type="result",
                        data="当前没有活跃的播客生成任务。",
                        result_for_assistant="亮哥，我检查了一下，当前没有处于 generating 状态的活跃播客任务哦。",
                    )
                    return

                # 直接复用 mcp_agent_learning_server 里面的 check_and_push_podcast
                from agent.tools.mcp_agent_learning_server import check_and_push_podcast
                
                yield ToolResult(type="progress", data="正在向 Google 侧查询最新生成状态...")
                res_str = await check_and_push_podcast()
                res_data = json.loads(res_str)
                status = res_data.get("status")

                if status == "success":
                    local_path = res_data.get("local_path")
                    topic = res_data.get("topic")
                    yield ToolResult(
                        type="result",
                        data=res_str,
                        result_for_assistant=(
                            f"🎉 太棒了亮哥！专题【{topic}】的音频已生成完毕并成功通过常规 Chrome 捕获到本地！\n"
                            f"保存路径：{local_path}\n"
                            f"（系统后台将立即为您执行 _pad_wav 高保真静音填充并推送到您的 QQ 手机，请查看！）"
                        ),
                    )
                elif status == "pending":
                    yield ToolResult(
                        type="result",
                        data="pending",
                        result_for_assistant="☕️ 亮哥，云端双人技术播客仍在生成中，进度正常，请稍候。我会在后台继续为您自动轮询！",
                    )
                else:
                    msg = res_data.get("message", "未知错误")
                    yield ToolResult(
                        type="result",
                        data=res_str,
                        result_for_assistant=f"⚠️ 查询播客生成状态时遇到了问题：{msg}。我会在后台重试。",
                    )

        except Exception as e:
            logger.error(f"NotebookLM [{action}] 执行失败: {e}", exc_info=True)
            yield ToolResult(
                type="result",
                data=f"NotebookLM [{action}] 执行失败: {e}",
                result_for_assistant=f"❌ 在执行 NotebookLM [{action}] 流程时遇到了报错：{e}。请检查代理或网络环境。",
            )
