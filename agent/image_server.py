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
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;600&amp;family=Plus+Jakarta+Sans:wght@700;800&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<style>
        .material-symbols-outlined {
            font-variation-settings: 'FILL' 1, 'wght' 400, 'GRAD' 0, 'opsz' 24;
        }
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
<!-- Card 1 -->
<div class="bg-surface-container-lowest border-2 border-[#ffdeeb] shadow-[0_4px_0_0_#ffdeeb] rounded-xl overflow-hidden hover:translate-y-[-2px] transition-transform duration-200 cursor-pointer flex flex-col">
<div class="h-32 bg-surface-container-high w-full relative border-b-2 border-[#ffdeeb]">
<img alt="Pixel art scenery" class="w-full h-full object-cover" data-alt="A bright, cheerful pixel art landscape showing a cozy green meadow with colorful daisy flowers scattered around. The lighting is sunny and warm, evoking a nostalgic 8-bit aesthetic but rendered in crisp high-fidelity pixels. The color palette features vibrant mint greens, soft petal pinks, and bright daisy yellows. The scene is framed symmetrically, creating a soothing and inviting digital environment." src="https://lh3.googleusercontent.com/aida-public/AB6AXuBcCD6JIDqK_cVypgjjMUwCVufsHhcuPvtG5x8ibtL32VcV3FFLX4ramI3eUtRR47KkFSMTqSCWAFKRmF_DQ4IfoHxyOHfDfNZ1JgkIHpi0502itlPfo7OcR93sAS8yDI07Pg-QIyLJhRuXSFZrWqq0LoE4MTI5lWhfGLAFgTAQsfegI0Pv592WKcOa-HCmJuMaOQ9IIa0APE9Ih6kyDRuuSE6duv_kRRQvdktsh1mPuqk1h2T0QMP0poTDkmGT7R62TzjL70u868g"/>
</div>
<div class="p-4 flex flex-col gap-1">
<span class="font-label-md text-label-md text-on-surface truncate">meadow_final.png</span>
<span class="font-body-md text-body-md text-outline text-sm">1.2 MB</span>
</div>
</div>
<!-- Card 2 -->
<div class="bg-surface-container-lowest border-2 border-[#ffdeeb] shadow-[0_4px_0_0_#ffdeeb] rounded-xl overflow-hidden hover:translate-y-[-2px] transition-transform duration-200 cursor-pointer flex flex-col">
<div class="h-32 bg-surface-container-high w-full relative border-b-2 border-[#ffdeeb]">
<img alt="Pixel art character" class="w-full h-full object-cover" data-alt="A cute, stylized pixel art character resembling a small woodland creature, perhaps a fox or a bear. The character is designed with simple, blocky geometry and smooth anti-aliased edges. It stands against a solid light pink background. The color scheme is warm and pastel-heavy, utilizing soft oranges, whites, and mint green accents. The overall mood is playful and endearing." src="https://lh3.googleusercontent.com/aida-public/AB6AXuAxmozECod6d6AMtg_b64r1DQ5THF-yGU_B_zTk4Wgjhn0se-XRVhrZQYHVLnw4ruPI8ikymyrkw1DErfhYRvUVYv1N2Bl44p3mzpSjbjYploLT6kLdi9QS8G54Kat5zIm2aBDJxHfuiW7nuCUGbO6tfVy6K7EPWaZGGElwnnhHOS_907vmUsWCelbfJNvk9RbRj4lgF9hs81uXxFLx7oao4yo53Eg7SXy_GgBUi7d-8hzPn4S5m6u0x_1qfK7nFLVyBzNRJP25V90"/>
</div>
<div class="p-4 flex flex-col gap-1">
<span class="font-label-md text-label-md text-on-surface truncate">cute_fox_sprite.gif</span>
<span class="font-body-md text-body-md text-outline text-sm">450 KB</span>
</div>
</div>
<!-- Card 3 -->
<div class="bg-surface-container-lowest border-2 border-[#ffdeeb] shadow-[0_4px_0_0_#ffdeeb] rounded-xl overflow-hidden hover:translate-y-[-2px] transition-transform duration-200 cursor-pointer flex flex-col">
<div class="h-32 bg-surface-container-high w-full relative border-b-2 border-[#ffdeeb] flex items-center justify-center">
<span class="material-symbols-outlined text-outline text-4xl block">image</span>
</div>
<div class="p-4 flex flex-col gap-1">
<span class="font-label-md text-label-md text-on-surface truncate">ui_elements_v2.png</span>
<span class="font-body-md text-body-md text-outline text-sm">2.1 MB</span>
</div>
</div>
<input type="file" id="file-input" accept="image/*" multiple style="display:none">
</div>
</section>
</main>
<div id="toast" style="position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:#2f6a3f;color:#fff;padding:12px 24px;border-radius:12px;font-family:Be Vietnam Pro,sans-serif;font-size:14px;z-index:999;opacity:0;transition:opacity 0.3s ease;border:2px solid #b2f2bb;box-shadow:0 4px 0 0 #145129;pointer-events:none;"></div>
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

<style>
  #upload-zone.drag-over { background: #e8f5e9 !important; border-color: #2f6a3f !important; }
  .thumb-card { position:relative; transition: transform 0.2s ease; }
  .thumb-card:hover { transform: translateY(-2px); }
  .card-del { position:absolute; top:6px; right:6px; width:24px; height:24px;
    background:#fff; border:2px solid #2f6a3f; border-radius:6px; cursor:pointer;
    font-size:14px; line-height:20px; text-align:center; color:#2f6a3f; display:none; box-shadow:0 2px 0 #145129; z-index:10; }
  .thumb-card:hover .card-del { display:block; }
  .card-del:hover { background:#fdf7ff; }
</style>
<script>
var _tt;
function toast(m){var t=document.getElementById('toast');t.textContent=m;t.style.opacity='1';clearTimeout(_tt);_tt=setTimeout(function(){t.style.opacity='0'},2000);}
function fmtName(n){try{return decodeURIComponent(n).replace(/\.\w+$/,'').slice(0,18)}catch(e){return n.slice(0,18)}}
function fmtSize(s){if(s<1024)return s+' B';if(s<1024*1024)return (s/1024).toFixed(1)+' KB';return (s/1024/1024).toFixed(1)+' MB'}

async function loadGallery(){
  var g=document.getElementById('gallery');
  var e=document.getElementById('gallery-empty');
  try{
    var r=await fetch('/api/list');
    var d=await r.json();
    var imgs=d.images||[];
    if(imgs.length===0){if(e)e.style.display='block';g.querySelectorAll('.thumb-card').forEach(function(c){c.remove()});return}
    if(e)e.style.display='none';
    var h='';
    for(var i=0;i<imgs.length;i++){
      var img=imgs[i];
      var dn=fmtName(img.original_name||img.name);
      h+='<div class="thumb-card bg-surface-container-lowest border-2 border-\[\#ffdeeb\] shadow-\[0_4px_0_0_\#ffdeeb\] rounded-xl overflow-hidden flex flex-col">'+
        '<div class="h-32 bg-surface-container-high w-full relative border-b-2 border-\[\#ffdeeb\] flex items-center justify-center overflow-hidden">'+
        '<img src="/images/'+img.name+'" alt="'+dn+'" loading="lazy" style="width:100%;height:100%;object-fit:cover;" onerror="this.style.display=`none`">'+
        '</div>'+
        '<div class="p-4 flex flex-col gap-1 relative">'+
        '<span class="font-label-md text-label-md text-on-surface truncate">'+dn+'</span>'+
        '<span class="font-body-md text-body-md text-outline text-sm">'+fmtSize(img.size)+'</span>'+
        '<button class="card-del" data-name="'+img.name+'" onclick="deleteImg(this)">&times;</button>'+
        '</div></div>';
    }
    g.querySelectorAll('.thumb-card').forEach(function(c){c.remove()});
    g.insertAdjacentHTML('afterbegin',h);
  }catch(e){if(e)e.textContent='加载失败';if(e)e.style.display='block'}
}

async function uploadFiles(files){
  if(!files||!files.length)return;
  for(var i=0;i<files.length;i++){
    var f=files[i];if(!f.type.match(/image\//))continue;
    var fd=new FormData();fd.append('file',f,f.name);
    try{
      var r=await fetch('/upload',{method:'POST',body:fd});
      var j=await r.json();
      if(j.ok){toast('已上传: '+f.name)}else{toast('上传失败')}
    }catch(e){toast('上传出错')}
  }
  loadGallery();
}

async function deleteImg(name,btn){
  if(!confirm('确认删除？'))return;
  try{
    var r=await fetch('/images/'+name,{method:'DELETE'});
    var j=await r.json();
    if(j.ok){toast('已删除');loadGallery()}else{toast('删除失败')}
  }catch(e){toast('删除出错')}
}

document.addEventListener('DOMContentLoaded',function(){
  var uz=document.getElementById('upload-zone');
  var fi=document.getElementById('file-input');
  if(uz)uz.onclick=function(){if(fi)fi.click();};
  if(fi)fi.onchange=function(){uploadFiles(this.files);this.value='';};
  if(uz){
    uz.ondragover=function(e){e.preventDefault();uz.classList.add('drag-over');};
    uz.ondragleave=function(){uz.classList.remove('drag-over');};
    uz.ondrop=function(e){e.preventDefault();uz.classList.remove('drag-over');uploadFiles(e.dataTransfer.files);};
  }
  document.onpaste=function(e){if(e.clipboardData&&e.clipboardData.files.length)uploadFiles(e.clipboardData.files);};
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
