"""Pixel Office Dashboard — HTTP + SSE server.

Usage:
    from agent.dashboard import DashboardServer
    dash = DashboardServer(port=8765)
    await dash.start()
    await dash.send({"agent": "xl", "event": "oversee"})
"""

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

HTML_PATH = Path(__file__).parent / "dashboard_v2" / "office.html"


class DashboardServer:
    """Lightweight HTTP server with SSE for real-time agent visualization."""

    def __init__(self, port: int = 8765):
        self.port = port
        self._clients: list[asyncio.Queue] = []

    async def start(self):
        """Start HTTP server in background."""
        html_path = HTML_PATH
        if not html_path.exists():
            html_path = Path.cwd() / "agent" / "dashboard.html"
        print(f"  📄 Loading dashboard from: {html_path} (exists={html_path.exists()})")
        html = html_path.read_text(encoding="utf-8") if html_path.exists() else f"<h1>Not found: {html_path}</h1>"
        html_bytes = html.encode("utf-8")

        async def handler(reader, writer):
            try:
                request = (await reader.readuntil(b"\r\n\r\n")).decode("utf-8", errors="replace")
                first_line = request.split("\r\n")[0] if request else ""
                method, path, *_ = first_line.split() + ["", ""]

                if method == "GET" and (path == "/" or path == "/index.html"):
                    await self._serve_html(writer, html_bytes)
                elif method == "GET" and path == "/events":
                    await self._serve_sse(reader, writer)
                else:
                    await self._serve_404(writer)
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        self._server = await asyncio.start_server(handler, "0.0.0.0", self.port)
        logger.info(f"Dashboard: http://localhost:{self.port}")
        print(f"\n  🎮 Dashboard: http://localhost:{self.port}\n")

    async def send(self, data: dict):
        """Push event to all connected browsers."""
        payload = json.dumps(data, ensure_ascii=False)
        dead = []
        for q in self._clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._clients.remove(q)

    async def _serve_html(self, writer, html: bytes):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: %d\r\n\r\n" % len(html))
        writer.write(html)
        await writer.drain()

    async def _serve_sse(self, reader, writer):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._clients.append(q)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    writer.write(f"data: {data}\n\n".encode("utf-8"))
                    await writer.drain()
                except asyncio.TimeoutError:
                    writer.write(b": heartbeat\n\n")
                    await writer.drain()
        except Exception:
            pass
        finally:
            self._clients.remove(q)

    async def _serve_404(self, writer):
        body = b"404"
        writer.write(f"HTTP/1.1 404\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
