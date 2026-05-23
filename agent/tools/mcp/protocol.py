import abc
import json
import shlex
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger("agent.tools.mcp.protocol")

class MCPTransport(ABC):
    """MCP 传输层抽象"""
    @abstractmethod
    async def send(self, request: dict) -> dict: ...
    @abstractmethod
    async def close(self): ...

class StdioTransport(MCPTransport):
    """stdio JSON-RPC 2.0 (stitch, mcp_client)"""
    def __init__(self, cmd: list[str], env: Optional[dict] = None):
        self.cmd = cmd
        self.env = env
        self.proc = None

    async def start(self):
        import shlex
        cmd_str = " ".join(shlex.quote(x) for x in self.cmd)
        self.proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env
        )

    async def send(self, request: dict) -> dict:
        if not self.proc:
            await self.start()
        payload = json.dumps(request) + "\n"
        self.proc.stdin.write(payload.encode())
        await self.proc.stdin.drain()
        
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=30)
        return json.loads(line.decode().strip())

    async def close(self):
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:
                if self.proc:
                    self.proc.kill()
            self.proc = None

class StreamableHTTPTransport(MCPTransport):
    """Streamable HTTP (xiaohongshu)"""
    def __init__(self, url: str, session_id: Optional[str] = None):
        self.url = url
        self.session_id = session_id

    async def send(self, request: dict) -> dict:
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.url, json=request, headers=headers)
            resp.raise_for_status()
            
            if request.get("method") == "initialize":
                extracted_id = resp.headers.get("mcp-session-id")
                if extracted_id:
                    self.session_id = extracted_id
                    
            return resp.json()

    async def close(self):
        pass

class MCPClient:
    """统一 MCP 客户端"""
    def __init__(self, transport: MCPTransport):
        self.transport = transport

    async def initialize(self) -> dict:
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "xl-agent", "version": "2.0.0"},
            },
        }
        resp = await self.transport.send(init_payload)
        
        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        await self.transport.send(notify_payload)
        return resp

    async def list_tools(self) -> list[dict]:
        list_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        resp = await self.transport.send(list_payload)
        if "error" in resp:
            error_msg = resp["error"].get("message", "Unknown error")
            raise RuntimeError(f"MCP list_tools error: {error_msg}")
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        call_payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        resp = await self.transport.send(call_payload)
        if "error" in resp:
            error_msg = resp["error"].get("message", "Unknown error")
            raise RuntimeError(f"MCP call_tool error: {error_msg}")
        return resp
