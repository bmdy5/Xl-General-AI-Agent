"""Image Upload Server — 图片上传 + 画廊展示.

Usage:
    python image_server.py
    # 或从 main.py 启动
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8866
IMAGES_DIR = Path(__file__).parent.parent.parent / "archive" / "images"
MANIFEST_FILE = IMAGES_DIR / "manifest.json"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# ── HTML 页面 ──────────────────────────────────────────────────

def load_gallery_html_file():
    """动态读取外部画廊 HTML"""
    p = Path(__file__).parent.parent / "resources" / "gallery.html"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return "<h1>像素花园画廊模板加载失败</h1>"

# 动态加载 HTML
GALLERY_HTML = load_gallery_html_file()

# ── multipart 解析 ─────────────────────────────────────────────

def parse_multipart(body: bytes, boundary: str) -> list[dict]:
    """手动解析 multipart/form-data，返回 [{filename, content_type, data}]."""
    parts = []
    boundary_bytes = boundary.encode("utf-8")
    # HTTP body 以 --boundary 开头（无前置 CRLF），后续以 \r\n--boundary 分隔
    start_marker = b"--" + boundary_bytes
    sep_marker = b"\r\n--" + boundary_bytes

    # 找到第一个 boundary
    first = body.find(start_marker)
    if first == -1:
        return parts
    # 跳过第一个 boundary 行到 \r\n
    body_start = body.find(b"\r\n", first)
    if body_start == -1:
        return parts
    body = body[body_start + 2:]

    sections = body.split(sep_marker)
    for section in sections:
        if section == b"--" or section.startswith(b"--\r\n"):
            break  # 结束标记
        header_end = section.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = section[:header_end].decode("utf-8", errors="replace")
        file_data = section[header_end + 4:]
        if file_data.endswith(b"\r\n"):
            file_data = file_data[:-2]
        
        filename = ""
        content_type = "application/octet-stream"
        for line in headers_raw.split("\r\n"):
            if line.startswith("Content-Disposition"):
                for part in line.split(";"):
                    part = part.strip()
                    if part.startswith("filename="):
                        filename = part.split("=", 1)[1].strip('"')
            elif line.startswith("Content-Type:"):
                content_type = line.split(":", 1)[1].strip()
        
        if filename and file_data:
            parts.append({
                "filename": filename,
                "content_type": content_type,
                "data": file_data,
            })
    return parts


def safe_filename(filename: str) -> str:
    """防路径穿越 + UUID 重命名."""
    base = Path(filename).name  # 去掉路径
    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".png"
    return f"{uuid.uuid4().hex}{ext}"


# ── HTTP 处理 ──────────────────────────────────────────────────

class ImageServer:
    """轻量图片上传服务器（纯 asyncio，无框架）."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.manifest: dict[str, str] = {}
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    def _load_manifest(self):
        """加载原始文件名映射."""
        if MANIFEST_FILE.exists():
            try:
                self.manifest = json.loads(MANIFEST_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                self.manifest = {}

    def _save_manifest(self):
        """保存原始文件名映射."""
        MANIFEST_FILE.write_text(json.dumps(self.manifest, ensure_ascii=False))

    async def start(self):
        self._load_manifest()
        """启动 HTTP 服务."""
        server = await asyncio.start_server(self._handle, self.host, self.port)
        print(f"\n  🖼️  Image Gallery: http://localhost:{self.port}")
        async with server:
            await server.serve_forever()

    async def _handle(self, reader, writer):
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).decode("utf-8", errors="replace")
            lines = request.split("\r\n")
            first_line = lines[0] if lines else ""
            method, path, *_ = first_line.split() + ["", ""]

            if method == "GET":
                await self._serve_get(path, writer)
            elif method == "POST" and path == "/upload":
                await self._handle_upload(request, reader, writer)
            elif method == "DELETE" and path.startswith("/images/"):
                await self._handle_delete(path, writer)
            else:
                await self._serve_404(writer)
        except Exception as e:
            logger.error(f"Request error: {e}")
            try:
                await self._serve_404(writer)
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _serve_get(self, path: str, writer):
        if path == "/" or path == "/index.html":
            await self._serve_html(writer, GALLERY_HTML)
        elif path.startswith("/images/"):
            await self._serve_image(path, writer)
        elif path == "/api/list":
            await self._serve_api_list(writer)
        else:
            await self._serve_404(writer)

    async def _handle_upload(self, request: str, reader, writer):
        # 提取 Content-Length
        content_length = 0
        for line in request.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())

        # 提取 boundary
        boundary = ""
        for line in request.split("\r\n"):
            if "boundary=" in line:
                boundary = line.split("boundary=", 1)[1].strip()
                if boundary.startswith('"'):
                    boundary = boundary.strip('"')
                break

        if not boundary or content_length == 0:
            await self._serve_json(writer, {"ok": False, "error": "invalid request"}, 400)
            return

        # 循环读取确保读够 content_length（TCP 分包兼容）
        body = b""
        remaining = content_length
        while remaining > 0:
            chunk = await reader.read(remaining)
            if not chunk:
                logger.warning(f"Upload: connection closed after {len(body)}/{content_length} bytes")
                break
            body += chunk
            remaining -= len(chunk)
        logger.info(f"Upload: read {len(body)}/{content_length} bytes, boundary={boundary[:40]}")
        parts = parse_multipart(body, boundary)

        saved = []
        for part in parts:
            name = safe_filename(part["filename"])
            filepath = IMAGES_DIR / name
            filepath.write_bytes(part["data"])
            saved.append({"name": name, "original": part["filename"], "size": len(part["data"])})
            self.manifest[name] = part["filename"]

        self._save_manifest()
        await self._serve_json(writer, {"ok": True, "files": saved})

    async def _serve_image(self, path: str, writer):
        filename = path.split("/images/", 1)[1]
        filepath = IMAGES_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            await self._serve_404(writer)
            return
        
        content_types = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        ext = filepath.suffix.lower()
        ct = content_types.get(ext, "application/octet-stream")
        data = filepath.read_bytes()
        await self._serve_binary(writer, data, ct)

    async def _serve_api_list(self, writer):
        images = []
        if IMAGES_DIR.exists():
            for f in sorted(IMAGES_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
                    original = self.manifest.get(f.name, f.name)
                    images.append({
                        "name": f.name,
                        "original_name": original,
                        "size": f.stat().st_size,
                        "time": f.stat().st_mtime,
                    })
        await self._serve_json(writer, {"images": images})

    # ── 响应辅助 ──────────────────────────────────────────────

    async def _serve_html(self, writer, html: str):
        body = html.encode("utf-8")
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        writer.write(response)
        await writer.drain()

    async def _serve_json(self, writer, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        response = (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body
        writer.write(response)
        await writer.drain()

    async def _serve_binary(self, writer, data: bytes, content_type: str):
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + data
        writer.write(response)
        await writer.drain()

    async def _serve_404(self, writer):
        await self._serve_json(writer, {"error": "not found"}, 404)

    async def _handle_delete(self, path: str, writer):
        filename = path.split("/images/", 1)[1]
        filepath = IMAGES_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            await self._serve_json(writer, {"error": "not found"}, 404)
            return
        filepath.unlink()
        self.manifest.pop(filename, None)
        self._save_manifest()
        await self._serve_json(writer, {"ok": True, "deleted": filename})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ImageServer().start())
