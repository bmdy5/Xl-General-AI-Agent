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
IMAGES_DIR = Path(__file__).parent.parent / "images"
MANIFEST_FILE = IMAGES_DIR / "manifest.json"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# ── HTML 页面 ──────────────────────────────────────────────────

GALLERY_HTML = """
<!DOCTYPE html>

<html class="light" lang="zh-CN"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>像素花园 - 上传</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;600&amp;family=Plus+Jakarta+Sans:wght@700;800&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script id="tailwind-config">
        tailwind.config = {
            darkMode: "class",
            theme: {
                extend: {
                    colors: {
                        "on-tertiary-container": "#636565",
                        "inverse-on-surface": "#f5eff7",
                        "secondary-container": "#fed33a",
                        "secondary-fixed-dim": "#ecc228",
                        "on-secondary": "#ffffff",
                        "on-background": "#1c1b20",
                        "surface-variant": "#e6e1e8",
                        "on-primary": "#ffffff",
                        "on-surface-variant": "#414940",
                        "secondary-fixed": "#ffe082",
                        "on-secondary-fixed-variant": "#564500",
                        "tertiary-fixed": "#e2e2e2",
                        "tertiary": "#5d5f5f",
                        "outline": "#717970",
                        "outline-variant": "#c0c9be",
                        "on-tertiary-fixed-variant": "#454747",
                        "error-container": "#ffdad6",
                        "surface-container": "#f2ecf4",
                        "surface-container-high": "#ece6ee",
                        "on-secondary-container": "#715b00",
                        "on-error": "#ffffff",
                        "on-primary-fixed-variant": "#145129",
                        "primary": "#2f6a3f",
                        "tertiary-container": "#e3e3e3",
                        "primary-container": "#b2f2bb",
                        "surface-bright": "#fdf7ff",
                        "tertiary-fixed-dim": "#c6c6c7",
                        "surface": "#fdf7ff",
                        "on-primary-fixed": "#00210b",
                        "on-surface": "#1c1b20",
                        "on-primary-container": "#367044",
                        "primary-fixed-dim": "#96d5a0",
                        "background": "#fdf7ff",
                        "error": "#ba1a1a",
                        "on-secondary-fixed": "#231b00",
                        "surface-dim": "#ded8e0",
                        "inverse-surface": "#322f35",
                        "on-error-container": "#93000a",
                        "surface-container-low": "#f8f2fa",
                        "surface-container-lowest": "#ffffff",
                        "primary-fixed": "#b2f2bb",
                        "inverse-primary": "#96d5a0",
                        "surface-container-highest": "#e6e1e8",
                        "on-tertiary": "#ffffff",
                        "secondary": "#725c00",
                        "surface-tint": "#2f6a3f",
                        "on-tertiary-fixed": "#1a1c1c"
                    },
                    borderRadius: {
                        "DEFAULT": "0.25rem",
                        "lg": "0.5rem",
                        "xl": "0.75rem",
                        "full": "9999px"
                    },
                    spacing: {
                        "margin-desktop": "32px",
                        "gutter": "16px",
                        "margin-mobile": "16px",
                        "unit": "4px",
                        "container-max": "1200px"
                    },
                    fontFamily: {
                        "body-md": ["Be Vietnam Pro"],
                        "label-md": ["Be Vietnam Pro"],
                        "headline-lg": ["Plus Jakarta Sans"],
                        "headline-md": ["Plus Jakarta Sans"],
                        "body-lg": ["Be Vietnam Pro"],
                        "display-lg": ["Plus Jakarta Sans"],
                        "headline-lg-mobile": ["Plus Jakarta Sans"]
                    },
                    fontSize: {
                        "body-md": ["16px", { "lineHeight": "1.6", "fontWeight": "400" }],
                        "label-md": ["14px", { "lineHeight": "1.4", "letterSpacing": "0.05em", "fontWeight": "600" }],
                        "headline-lg": ["32px", { "lineHeight": "1.3", "fontWeight": "700" }],
                        "headline-md": ["24px", { "lineHeight": "1.4", "fontWeight": "700" }],
                        "body-lg": ["18px", { "lineHeight": "1.6", "fontWeight": "400" }],
                        "display-lg": ["40px", { "lineHeight": "1.2", "letterSpacing": "-0.02em", "fontWeight": "800" }],
                        "headline-lg-mobile": ["28px", { "lineHeight": "1.3", "fontWeight": "700" }]
                    }
                }
            }
        }
    </script>
<style>
        .material-symbols-outlined {
            font-variation-settings: 'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24;
        }
      #upload-zone.drag-over{background:#e8f5e9!important;border-color:#2f6a3f!important}
  .gallery-card:hover .card-del{display:block!important}
</style>
</head>
<body class="bg-surface text-on-surface font-body-md min-h-screen">
<!-- TopAppBar -->
<header class="fixed top-0 z-50 flex justify-between items-center w-full px-margin-mobile md:px-margin-desktop h-16 bg-surface border-b-2 border-primary-container shadow-[0_4px_0_0_#fed33a]">
<div class="flex items-center">
<span class="font-headline-md text-headline-md font-black text-primary">像素花园</span>
</div>
<div class="flex items-center gap-4">
<button class="p-2 rounded-full hover:bg-primary-container/20 transition-colors duration-200 text-primary">
<span class="material-symbols-outlined">notifications</span>
</button>
<button class="p-2 rounded-full hover:bg-primary-container/20 transition-colors duration-200 text-primary">
<span class="material-symbols-outlined">settings</span>
</button>
</div>
</header>
<!-- Main Content -->
<main class="pt-32 pb-32 px-margin-mobile md:px-margin-desktop max-w-container-max mx-auto flex flex-col gap-16">
<!-- Upload Zone -->
<section class="relative bg-surface-container-lowest border-2 border-[#ffdeeb] shadow-[0_8px_0_0_#ffdeeb] rounded-xl p-8 md:p-12">
<!-- Daisy Decorations -->
<div class="absolute -top-4 -left-4 text-secondary-container bg-surface rounded-full p-1 border-2 border-primary-container shadow-[0_2px_0_0_#fed33a]">
<span class="material-symbols-outlined text-4xl block">local_florist</span>
</div>
<div class="absolute -bottom-4 -right-4 text-secondary-container bg-surface rounded-full p-1 border-2 border-primary-container shadow-[0_2px_0_0_#fed33a]">
<span class="material-symbols-outlined text-4xl block">local_florist</span>
</div>
<!-- Drop Area -->
<div id="upload-zone" class="border-4 border-dashed border-primary-container bg-primary-container/10 hover:bg-primary-container/20 transition-colors duration-200 rounded-xl p-12 flex flex-col items-center justify-center gap-6 cursor-pointer group">
<div class="bg-surface p-4 rounded-full border-2 border-primary shadow-[0_4px_0_0_#2f6a3f] group-hover:translate-y-1 group-hover:shadow-[0_2px_0_0_#2f6a3f] transition-all">
<span class="material-symbols-outlined text-display-lg text-primary block" style="font-size: 64px;">cloud_upload</span>
</div>
<div class="text-center flex flex-col gap-2">
<h2 class="font-headline-md text-headline-md text-on-surface">拖拽或点击上传</h2>
<p class="font-body-md text-body-md text-on-surface-variant">支持格式: PNG, JPG, GIF</p>
<p class="font-label-md text-label-md text-primary bg-primary-container/50 inline-block px-3 py-1 rounded-full mx-auto mt-2">最大限制: 50MB</p>
</div>
<button class="mt-4 bg-primary-container text-on-primary-container font-label-md text-label-md px-8 py-4 rounded-lg shadow-[0_4px_0_0_#fed33a] hover:translate-y-[2px] hover:shadow-[0_2px_0_0_#fed33a] active:translate-y-[4px] active:shadow-none transition-all border-2 border-primary">
                    选择文件
                </button>
</div>
</section>
<!-- Pixel Divider -->
<div class="flex justify-center items-center gap-2 py-4">
<div class="w-3 h-3 bg-primary-container"></div>
<div class="w-3 h-3 bg-secondary-container"></div>
<div class="w-3 h-3 bg-[#ffdeeb]"></div>
<div class="w-3 h-3 bg-secondary-container"></div>
<div class="w-3 h-3 bg-primary-container"></div>
</div>
<!-- Recent Uploads -->
<section class="flex flex-col gap-8">
<div class="flex items-center gap-3">
<span class="material-symbols-outlined text-primary text-3xl">history</span>
<h3 class="font-headline-md text-headline-md text-on-surface">最近上传</h3>
</div>
<div id="gallery" class="grid grid-cols-2 md:grid-cols-4 gap-gutter">
<div id="gallery-empty" class="col-span-full text-center text-outline py-12 font-body-md">还没有图片，拖拽或点击上传</div>
</div>/div>
</section>
</main>
<!-- BottomNavBar (Visible on Mobile as per standard app behavior, hiding on md) -->
<nav class="fixed bottom-0 left-0 w-full z-50 flex justify-around items-center h-20 px-2 pb-safe bg-surface-container border-t-2 border-primary-container rounded-t-xl shadow-[0_-4px_0_0_#fed33a] md:hidden">
<!-- 首页 Inactive -->
<a class="flex flex-col items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-all p-2 rounded-lg w-16" href="#">
<span class="material-symbols-outlined mb-1">home</span>
<span class="font-label-md text-label-md text-[10px]">首页</span>
</a>
<!-- 上传 Active -->
<a class="flex flex-col items-center justify-center bg-primary-container text-on-primary-container rounded-lg px-4 py-1 shadow-[2px_2px_0_0_#ecc228] scale-95 duration-150 w-16" href="#">
<span class="material-symbols-outlined mb-1">cloud_upload</span>
<span class="font-label-md text-label-md text-[10px]">上传</span>
</a>
<!-- 广场 Inactive -->
<a class="flex flex-col items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-all p-2 rounded-lg w-16" href="#">
<span class="material-symbols-outlined mb-1">local_florist</span>
<span class="font-label-md text-label-md text-[10px]">广场</span>
</a>
<!-- 我的 Inactive -->
<a class="flex flex-col items-center justify-center text-on-surface-variant hover:bg-surface-container-high transition-all p-2 rounded-lg w-16" href="#">
<span class="material-symbols-outlined mb-1">person</span>
<span class="font-label-md text-label-md text-[10px]">我的</span>
</a>
</nav>
<input type="file" id="file-input" accept="image/*" multiple style="display:none">
<div id="toast" style="position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#2f6a3f;color:#fff;padding:12px 24px;border-radius:12px;font-family:Be Vietnam Pro,sans-serif;font-size:14px;z-index:999;opacity:0;transition:opacity 0.3s ease;border:2px solid #b2f2bb;box-shadow:0 4px 0 0 #145129;pointer-events:none;"></div>

<script>
var _tt;
function toast(m){var t=document.getElementById('toast');if(!t)return;t.textContent=m;t.style.opacity='1';clearTimeout(_tt);_tt=setTimeout(function(){t.style.opacity='0'},2000);}
function fmtName(n){try{return decodeURIComponent(n).replace(/\.\w+$/,'').slice(0,18)}catch(e){return n.slice(0,18)}}
function fmtSize(s){if(s<1024)return s+' B';if(s<1024*1024)return (s/1024).toFixed(1)+' KB';return (s/1024/1024).toFixed(1)+' MB'}

async function loadGallery(){
  var g=document.getElementById('gallery');if(!g)return;
  var e=document.getElementById('gallery-empty');
  try{
    var r=await fetch('/api/list');var d=await r.json();var imgs=d.images||[];
    g.innerHTML='';
    if(imgs.length===0){if(e)e.style.display='block';return}
    if(e)e.remove();
    for(var i=0;i<imgs.length;i++){
      var img=imgs[i];
      var dn=fmtName(img.original_name||img.name);
      var div=document.createElement('div');
      div.className='gallery-card bg-surface-container-lowest border-2 border-[#ffdeeb] shadow-[0_4px_0_0_#ffdeeb] rounded-xl overflow-hidden flex flex-col';
      div.innerHTML='<div class="h-32 bg-surface-container-high w-full relative border-b-2 border-[#ffdeeb] flex items-center justify-center overflow-hidden">'+
        '<img src="/images/'+img.name+'" alt="'+dn+'" loading="lazy" style="width:100%;height:100%;object-fit:cover" onerror="this.remove()">'+
        '</div>'+
        '<div class="p-4 flex flex-col gap-1 relative">'+
        '<span class="font-label-md text-label-md text-on-surface truncate">'+dn+'</span>'+
        '<span class="font-body-md text-body-md text-outline text-sm">'+fmtSize(img.size)+'</span>'+
        '<button class="card-del" class="card-del" style="position:absolute;top:6px;right:6px;width:24px;height:24px;background:#fff;border:2px solid #2f6a3f;border-radius:6px;cursor:pointer;font-size:14px;line-height:20px;text-align:center;color:#2f6a3f;display:none;box-shadow:0 2px 0 #145129;z-index:10" onclick="deleteImg(this.dataset.name)" data-name="'+img.name+'">&times;</button>'+
        '</div>';
      div.onmouseenter=function(){this.querySelector('.card-del').style.display='block'};
      div.onmouseleave=function(){this.querySelector('.card-del').remove()};
      g.appendChild(div);
    }
  }catch(e){if(e)e.textContent='加载失败';if(e)e.style.display='block'}
}

async function uploadFiles(files){
  if(!files||!files.length)return;
  for(var i=0;i<files.length;i++){
    var f=files[i];if(!f.type.match(/image\//))continue;
    var fd=new FormData();fd.append('file',f,f.name);
    try{
      var r=await fetch('/upload',{method:'POST',body:fd});var j=await r.json();
      if(j.ok){toast('已上传: '+f.name)}else{toast('上传失败')}
    }catch(e){toast('上传出错')}
  }
  await loadGallery();
}

async function deleteImg(name){
  if(!confirm('确认删除？'))return;
  try{
    var r=await fetch('/images/'+name,{method:'DELETE'});var j=await r.json();
    if(j.ok){toast('已删除');loadGallery()}else{toast('删除失败')}
  }catch(e){toast('删除出错')}
}

document.addEventListener('DOMContentLoaded',function(){
  var uz=document.getElementById('upload-zone');
  var fi=document.getElementById('file-input');
  if(uz)uz.onclick=function(){if(fi)fi.click()};
  if(fi)fi.onchange=function(){uploadFiles(this.files);this.value=''};
  if(uz){
    uz.ondragover=function(e){e.preventDefault();uz.classList.add('drag-over')};
    uz.ondragleave=function(){uz.classList.remove('drag-over')};
    uz.ondrop=function(e){e.preventDefault();uz.classList.remove('drag-over');uploadFiles(e.dataTransfer.files)};
  }
  document.onpaste=function(e){if(e.clipboardData&&e.clipboardData.files.length)uploadFiles(e.clipboardData.files)};
  loadGallery();
});
</script>
</body></html>
"""

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
