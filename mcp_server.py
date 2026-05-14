"""XL MCP Server — 把 XL 的能力暴露为 MCP 工具.

用法:
  python mcp_server.py           # stdio 模式，供 MCP 客户端调用
  python mcp_server.py --list    # 列出可用工具

其他 agent (如 Claude/Cursor/Reasonix) 通过 MCP 协议调用 XL。
"""

import asyncio
import json
import sys
import os
import traceback

# 确保能找到 agent 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.core import Agent
from agent.llm import LLMClient
from agent.memory.manager import MemoryManager
from agent.tools.registry import ToolRegistry

registry = ToolRegistry()
agent = None


def lazy_agent():
    """延迟初始化 agent."""
    global agent
    if agent is None:
        model = os.getenv("MYAGENT_MODEL", "openai/gpt-4o")
        api_key = os.getenv("MYAGENT_API_KEY") or os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("MYAGENT_API_BASE")
        llm = LLMClient(model=model, api_key=api_key, api_base=api_base)
        agent = Agent(llm=llm, registry=registry, memory=MemoryManager())
    return agent


# ── 暴露给 MCP 的工具定义 ──
MCP_TOOLS = [
    {
        "name": "xl_analyze",
        "description": "分析代码/项目结构，返回分析报告. XL 擅长阅读和理解代码。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "分析问题，如'这个项目的架构是什么'"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "xl_design",
        "description": "设计方案、架构规划、技术选型。给出你的需求，XL 做方案。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "description": "需求描述"}
            },
            "required": ["requirement"]
        }
    },
    {
        "name": "xl_research",
        "description": "研究问题：网页搜索 + 代码阅读 + 综合分析。适合查资料、对比方案。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "研究问题"}
            },
            "required": ["question"]
        }
    },
    {
        "name": "xl_memory",
        "description": "读写 XL 的记忆系统。动作: search(搜索), save(保存), list(列出).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "save", "list"],
                    "description": "操作: search/search, save/保存, list/列出"
                },
                "query": {"type": "string", "description": "搜索关键词或要保存的内容"},
                "description": {"type": "string", "description": "保存时的描述"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "xl_stitch",
        "description": "调用 Stitch AI 生成 UI 设计。输入描述，输出 HTML 代码和预览链接。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "UI 设计描述，如'一个粉色系登录页面'"}
            },
            "required": ["prompt"]
        }
    },
]


async def handle_call(tool_name: str, arguments: dict) -> dict:
    """处理 MCP 工具调用."""
    from agent.tools.stitch_tool import StitchTool
    from agent.tools.web_tools import WebSearchTool, WebFetchTool

    try:
        if tool_name == "xl_analyze":
            a = lazy_agent()
            query = arguments.get("query", "")
            resp = await a.llm.chat([
                {"role": "system", "content": "你是一个代码分析专家。深入分析用户的问题，给出详细的回答。"},
                {"role": "user", "content": query}
            ])
            return {"content": [{"type": "text", "text": resp.get("content", "")}]}

        elif tool_name == "xl_design":
            a = lazy_agent()
            req = arguments.get("requirement", "")
            resp = await a.llm.chat([
                {"role": "system", "content": "你是一个架构师。给出详细的技术方案，包括架构图(ASCII)、技术选型、优缺点。"},
                {"role": "user", "content": req}
            ])
            return {"content": [{"type": "text", "text": resp.get("content", "")}]}

        elif tool_name == "xl_research":
            a = lazy_agent()
            q = arguments.get("question", "")
            # 先搜网页
            search = WebSearchTool()
            web_results = await search.execute(max_results=5, query=q)
            web_text = json.dumps(web_results, ensure_ascii=False)[:3000]
            # 综合分析
            resp = await a.llm.chat([
                {"role": "system", "content": "综合网页搜索结果和你的知识，回答用户问题。引用来源。"},
                {"role": "user", "content": f"问题: {q}\n\n搜索结果:\n{web_text}"}
            ])
            return {"content": [{"type": "text", "text": resp.get("content", "")}]}

        elif tool_name == "xl_memory":
            mm = MemoryManager()
            action = arguments.get("action", "search")
            if action in ("search", "search"):
                q = arguments.get("query", "")
                results = mm.search_memories(q)
                return {"content": [{"type": "text", "text": json.dumps(results, ensure_ascii=False, indent=2)}]}
            elif action in ("save", "保存"):
                desc = arguments.get("description", "xl_mcp")
                content = arguments.get("query", "")
                await mm.save("xl_mcp", desc, content)
                return {"content": [{"type": "text", "text": "saved"}]}
            elif action in ("list", "列出"):
                mems = mm.list_memories()
                return {"content": [{"type": "text", "text": "\n".join(mems)}]}

        elif tool_name == "xl_stitch":
            stitch = StitchTool()
            prompt = arguments.get("prompt", "一个简单的 UI 组件")
            # StitchTool.execute 需要特定参数
            result = await stitch.execute(prompt=prompt, style="modern")
            # 提取预览链接
            preview = ""
            if isinstance(result, dict):
                preview = result.get("preview_url", result.get("url", ""))
            html = result.get("html", result.get("code", ""))[:500]
            return {
                "content": [
                    {"type": "text", "text": f"预览: {preview}\n\n```html\n{html}\n```"}
                ]
            }

        else:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"未知工具: {tool_name}"}]
            }

    except Exception as e:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}"}]
        }


async def main():
    """MCP stdio server — 读 JSON line, 写 JSON line."""
    # 先注册所有工具
    from agent.tools.file_tools import ReadFileTool, WriteFileTool
    from agent.tools.bash_tool import BashTool
    from agent.tools.edit_file_tool import EditFileTool
    for t in [ReadFileTool(), WriteFileTool(), EditFileTool(), BashTool(".")]:
        registry.register(t)

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    # 初始化消息
    init_msg = json.loads(await stdin.readline())
    if init_msg.get("method") == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": init_msg.get("id"),
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}}
            }
        }
        await _write(stdout, resp)

        # tools/list
        msg = json.loads(await stdin.readline())
        if msg.get("method") == "tools/list":
            await _write(stdout, {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {"tools": MCP_TOOLS}
            })

    # 主循环：处理 tools/call
    while True:
        line = await stdin.readline()
        if not line:
            break
        msg = json.loads(line)
        method = msg.get("method")

        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})
            result = await handle_call(name, args)
            await _write(stdout, {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": result
            })

        elif method == "shutdown":
            await _write(stdout, {"jsonrpc": "2.0", "id": msg.get("id"), "result": None})
            break


async def _write(stream, data: dict):
    """写 JSON-RPC 消息 (带 Content-Length header)."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode()
    stream.write(header + body)
    await stream.drain()


if __name__ == "__main__":
    if "--list" in sys.argv:
        for t in MCP_TOOLS:
            print(f"  {t['name']}: {t['description'][:60]}...")
        sys.exit(0)
    asyncio.run(main())
