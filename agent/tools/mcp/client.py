import logging
from typing import Any, AsyncGenerator, Optional
from ..base_tool import BaseTool, ToolResult
from .protocol import StdioTransport, MCPClient

logger = logging.getLogger(__name__)

class MCPClientTool(BaseTool):
    """连接 MCP 服务器，列出并调用其暴露的工具."""

    @property
    def name(self) -> str:
        return "mcp_client"

    async def description(self) -> str:
        return "Connect to an MCP stdio server, list tools, or call a tool."

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
                "description": "Interact with an MCP stdio server. Actions: 'list' (list tools), 'call' (call a tool).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "call"],
                            "description": "list: show available tools. call: execute a tool.",
                        },
                        "server_command": {
                            "type": "string",
                            "description": "The MCP server command (e.g. 'npx @modelcontextprotocol/server-filesystem /path').",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Tool name to call (for action=call).",
                        },
                        "tool_args": {
                            "type": "object",
                            "description": "Arguments for the tool (for action=call).",
                        },
                    },
                    "required": ["action", "server_command"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        if input_args.get("action") not in ("list", "call"):
            return {"result": False, "message": "action must be 'list' or 'call'"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]
        server_cmd = input_args["server_command"]
        cmd = server_cmd.split()

        transport = StdioTransport(cmd)
        client = MCPClient(transport)
        try:
            await client.initialize()
            if action == "list":
                tools = await client.list_tools()
                names = [t.get("name", "?") for t in tools]
                yield ToolResult(type="result", data=f"MCP server tools ({len(names)}):\n" + "\n".join(f"  - {n}" for n in names))

            elif action == "call":
                tool_name = input_args.get("tool_name", "")
                tool_args = input_args.get("tool_args", {})
                if not tool_name:
                    yield ToolResult(type="result", data="Error: tool_name required")
                    return
                
                call_resp = await client.call_tool(tool_name, tool_args)
                content = call_resp.get("result", {}).get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                result_text = "\n".join(texts)[:5000] if texts else str(call_resp)[:500]
                yield ToolResult(type="result", data=result_text)

        except Exception as e:
            yield ToolResult(type="result", data=f"MCP error: {e}")
        finally:
            await transport.close()
