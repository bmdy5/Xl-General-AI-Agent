"""Image Upload Server — 图片上传 + 画廊展示.

Usage:
    python image_server.py
    # 或从 main.py 启动
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8866
IMAGES_DIR = Path(__file__).parent.parent / "images"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# ── HTML 页面 ──────────────────────────────────────────────────

GALLERY_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XL Image Gallery</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Courier New', monospace;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px;
  }
  #header {
    color: #f4d058;
    font-size: 24px;
    padding: 20px;
    text-shadow: 2px 2px #6b4c1a;
    letter-spacing: 4px;
  }
  #upload-zone {
    border: 2px dashed #533483;
    border-radius: 8px;
    padding: 30px;
    margin: 10px 0 20px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    width: 100%;
    max-width: 900px;
  }
  #upload-zone:hover, #upload-zone.drag-over {
    border-color: #f4d058;
    background: #16213e;
  }
  #upload-zone input { display: none; }
  #upload-zone .hint { color: #888; font-size: 14px; }
  #upload-zone .hint span { color: #f4d058; text-decoration: underline; }
  #gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
    width: 100%;
    max-width: 900px;
  }
  .thumb {
    background: #16213e;
    border: 2px solid #533483;
    border-radius: 4px;
    overflow: hidden;
    cursor: pointer;
    transition: border-color 0.2s;
    position: relative;
  }
  .thumb:hover { border-color: #f4d058; }
  .thumb img {
    width: 100%;
    height: 150px;
    object-fit: cover;
    display: block;
  }
  .thumb-info {
    padding: 6px;
    font-size: 11px;
    color: #888;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .thumb .delete-btn {
    position: absolute;
    top: 4px;
    right: 4px;
    background: #e74c3c;
    color: #fff;
    border: none;
    border-radius: 3px;
    width: 20px;
    height: 20px;
    font-size: 12px;
    cursor: pointer;
    display: none;
  }
  .thumb:hover .delete-btn { display: block; }
  .empty {
    grid-column: 1 / -1;
    text-align: center;
    color: #555;
    padding: 60px;
    font-size: 16px;
  }
  .toast {
    position: fixed;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    background: #f4d058;
    color: #1a1a2e;
    padding: 10px 24px;
    border-radius: 4px;
    font-size: 14px;
    z-index: 999;
    opacity: 0;
    transition: opacity 0.3s;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<div id="header">XL IMAGE GALLERY</div>
<div id="upload-zone">
  <input type="file" id="file-input" accept="image/*" multiple>
  <div class="hint">拖拽图片到此处 或 <span>点击选择文件</span></div>
</div>
<div id="gallery">
  <div class="empty">加载中...</div>
</div>
<div id="toast" class="toast"></div>
<script>
const gallery = document.getElementById('gallery');
const fileInput = document.getElementById('file-input');
const uploadZone = document.getElementById('upload-zone');
const toast = document.getElementById('toast');

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2000);
}

async function loadGallery() {
  try {
    const res = await fetch('/api/list');
    const data = await res.json();
    gallery.innerHTML = '';
    if (!data.images || data.images.length === 0) {
      gallery.innerHTML = '<div class="empty">还没有图片，拖拽或点击上传</div>';
      return;
    }
    data.images.forEach(img => {
      const div = document.createElement('div');
      div.className = 'thumb';
      div.innerHTML = `
        <img src="/images/${img.name}" alt="${img.name}" loading="lazy">
        <div class="thumb-info">${img.name}</div>
        <button class="delete-btn" data-name="${img.name}">&times;</button>
      `;
      div.querySelector('.delete-btn').onclick = (e) => {
        e.stopPropagation();
        deleteImage(img.name);
      };
      div.querySelector('img').onclick = () => window.open('/images/' + img.name);
      gallery.appendChild(div);
    });
  } catch(e) {
    gallery.innerHTML = '<div class="empty">加载失败</div>';
  }
}

async function uploadFiles(files) {
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue;
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch('/upload', { method: 'POST', body: form });
      const data = await res.json();
      if (data.ok) {
        showToast('已上传: ' + file.name);
      } else {
        showToast('上传失败: ' + (data.error || 'unknown'));
      }
    } catch(e) {
      showToast('上传失败: ' + e.message);
    }
  }
  loadGallery();
}

async function deleteImage(name) {
  try {
    await fetch('/images/' + name, { method: 'DELETE' });
  } catch(e) {}
  loadGallery();
}

fileInput.onchange = () => uploadFiles(fileInput.files);

uploadZone.onclick = () => fileInput.click();

uploadZone.ondragover = (e) => {
  e.preventDefault();
  uploadZone.classList.add('drag-over');
};
uploadZone.ondragleave = () => uploadZone.classList.remove('drag-over');
uploadZone.ondrop = (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  uploadFiles(e.dataTransfer.files);
};

document.ondragover = (e) => e.preventDefault();
document.ondrop = (e) => e.preventDefault();

loadGallery();
</script>
</body>
</html>"""

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
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self):
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
                    images.append({
                        "name": f.name,
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ImageServer().start())
