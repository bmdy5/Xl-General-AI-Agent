import os
import sys
import json
import time
import asyncio
import logging
import urllib.request
import aiohttp
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 配置常量
OBSIDIAN_VAULT_PATH = "/Users/xiaofeng/Documents/个人博客/学习笔记"
NOTEBOOKLM_BIN = "/Users/xiaofeng/.gemini/antigravity/scratch/venv/bin/notebooklm-mcp"
NOTEBOOKLM_ENV = {
    "HOME": "/Users/xiaofeng/.gemini/antigravity/scratch",
    "HTTP_PROXY": "http://127.0.0.1:7897",
    "HTTPS_PROXY": "http://127.0.0.1:7897",
    "ALL_PROXY": "http://127.0.0.1:7897",
    "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
}

class NotebookLMMCPClient:
    """高保真、维持长连接的 NotebookLM MCP 客户端."""
    
    def __init__(self):
        self.cmd = [NOTEBOOKLM_BIN]
        self.proc = None
        self.msg_id = 1
        
    async def start(self):
        # 复制当前环境变量，并注入特定的 Proxy 代理和 HOME 配置
        env = os.environ.copy()
        env.update(NOTEBOOKLM_ENV)
        
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        logger.info("🚀 NotebookLM MCP 进程启动成功")
        
        # 顺序执行初始化
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "xl-podcast-client", "version": "1.0"}
        })
        await self._send_notification("notifications/initialized", {})
        logger.info("✅ NotebookLM MCP 初始化序列完成")

    async def _send_request(self, method: str, params: dict) -> dict:
        req_id = self.msg_id
        self.msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        raw_send = json.dumps(payload) + "\n"
        self.proc.stdin.write(raw_send.encode())
        await self.proc.stdin.drain()
        
        # 读取响应 (直到匹配当前 ID 的响应)
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise ConnectionError("NotebookLM MCP 进程意外终止")
            
            resp = json.loads(line.decode().strip())
            # 处理可能的通知或不匹配的 ID
            if resp.get("id") == req_id:
                if "error" in resp:
                    raise ValueError(f"MCP Error in {method}: {resp['error']}")
                return resp.get("result", {})

    async def _send_notification(self, method: str, params: dict):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        raw_send = json.dumps(payload) + "\n"
        self.proc.stdin.write(raw_send.encode())
        await self.proc.stdin.drain()

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """核心调用外部工具封装，确保在同一个 session 连接内执行."""
        logger.info(f"⚙️ 调用工具: {tool_name}，参数: {arguments}")
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if not texts:
            raise ValueError(f"工具返回空白或格式错误: {result}")
        return "\n".join(texts)

    async def close(self):
        if self.proc:
            try:
                self.proc.terminate()
                await self.proc.wait()
            except Exception:
                self.proc.kill()
            logger.info("🔒 NotebookLM MCP 进程安全关闭")


def scan_obsidian_notes(vault_path: str = OBSIDIAN_VAULT_PATH, hours: int = 48) -> list[dict]:
    """遍历 Obsidian 目录下过去 48 小时变动的所有笔记内容."""
    modified_notes = []
    threshold = datetime.now() - timedelta(hours=hours)
    
    if not os.path.exists(vault_path):
        logger.warning(f"Obsidian 路径不存在: {vault_path}")
        return []

    for root, _, files in os.walk(vault_path):
        for f in files:
            if not f.endswith(".md") or f.startswith("."):
                continue
            
            fpath = os.path.join(root, f)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime > threshold:
                    with open(fpath, "r", encoding="utf-8") as file:
                        content = file.read().strip()
                    if content:
                        modified_notes.append({
                            "title": os.path.splitext(f)[0],
                            "content": content,
                            "path": fpath,
                            "mtime": mtime
                        })
            except Exception as e:
                logger.error(f"读取笔记失败 {f}: {e}")
                
    # 按照修改时间降序排序
    modified_notes.sort(key=lambda x: x["mtime"], reverse=True)
    return modified_notes


async def generate_podcast_workflow() -> Optional[str]:
    """每日早报播客端到端核心生成逻辑."""
    logger.info("🌅 启动亮哥专属学习播客生成流程...")
    
    # 1. 扫描变动笔记
    notes = scan_obsidian_notes()
    if not notes:
        logger.info("✨ 过去 48 小时内没有变动的笔记，跳过播客生成。")
        return None
        
    logger.info(f"📚 成功扫描到 {len(notes)} 篇最近变动笔记。")
    
    # 将笔记内容拼接，控制在 NotebookLM 的良好处理阈值内 (建议不超过 100k 字符)
    combined_content = ""
    for note in notes[:8]: # 优先处理最近变动的 8 篇
        combined_content += f"=== 笔记标题: {note['title']} ===\n"
        combined_content += f"{note['content']}\n\n"
    
    # 2. 启动 MCP Client
    client = NotebookLMMCPClient()
    await client.start()
    
    notebook_id = None
    try:
        # 创建 Notebook
        today_str = datetime.now().strftime("%Y-%m-%d")
        title = f"亮哥极客早报-{today_str}"
        logger.info(f"📓 正在创建笔记本: {title}...")
        
        create_res = await client.call_tool("notebook_create", {"title": title})
        # 提取 UUID。接口返回通常是 JSON string 或者 包含 ID 的描述，我们用 json.loads 或者是正则解析它
        logger.info(f"创建结果: {create_res}")
        
        # 兼容性解析 notebook_id
        # notebooklm 接口在创建成功后往往会返回 "Created notebook: <UUID>" 或者是 JSON String
        import re
        match_id = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', create_res, re.IGNORECASE)
        if not match_id:
            raise ValueError(f"无法解析创建成功的 notebook_id: {create_res}")
        
        notebook_id = match_id.group(0)
        logger.info(f"✅ 获取到 Notebook ID: {notebook_id}")
        
        # 3. 添加笔记文本作为 Source
        logger.info("⏳ 正在上传笔记内容至 NotebookLM...")
        add_res = await client.call_tool("notebook_add_text", {
            "notebook_id": notebook_id,
            "text": combined_content,
            "title": f"过去48小时笔记精选"
        })
        logger.info(f"上传结果: {add_res}")
        
        # 4. 请求生成音频播客 (设置为中文、深度对谈形式)
        logger.info("🎙️ 正在向 Google 发起音频播客生成任务，请耐心等待 (约需几分钟)...")
        overview_res = await client.call_tool("audio_overview_create", {
            "notebook_id": notebook_id,
            "format": "deep_dive",
            "language": "zh",
            "focus_prompt": "请用幽默、极其硬核且富有极客精神的中文，深度提炼并点评亮哥的这几篇学习笔记。两个主持人的语气要自然、像日常专业对话一样对谈，拒绝刻板翻译感。",
            "confirm": True
        })
        logger.info(f"音频创建请求已接收: {overview_res}")
        
        # 5. 轮询 studio_status 直至完成
        audio_url = None
        max_attempts = 30
        poll_interval = 20
        logger.info("🔄 开始轮询音频生成状态...")
        
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(poll_interval)
            try:
                status_res = await client.call_tool("studio_status", {"notebook_id": notebook_id})
                # 我们检查结果里是否有 "status": "completed" 或者是 downloadable url
                # status_res 可能是个 JSON
                logger.info(f"轮询第 {attempt} 次，响应片段: {status_res[:200]}...")
                
                # 尝试解析 JSON 或搜索 URL
                try:
                    data = json.loads(status_res)
                    # 假定返回的是结构化数据
                    artifacts = data.get("artifacts", [])
                    for art in artifacts:
                        if art.get("type") == "audio" and art.get("url"):
                            audio_url = art["url"]
                            break
                except Exception:
                    # 如果不是标准 JSON，通过正则寻找 URL 或者是完成状态
                    # 例如 "Artifact (Audio): https://..." 或者是包含 https://
                    urls = re.findall(r'https?://[^\s"\'\]]+', status_res)
                    for u in urls:
                        if "audio" in u.lower() or "google" in u.lower():
                            audio_url = u
                            break
                
                if audio_url:
                    logger.info(f"🎉 播客音频生成成功！URL: {audio_url}")
                    break
                    
                if "failed" in status_res.lower() or "error" in status_res.lower():
                    raise ValueError(f"Google 播客生成失败: {status_res}")
                    
            except Exception as poll_err:
                logger.warning(f"轮询发生异常 (第 {attempt} 次): {poll_err}")
                
        if not audio_url:
            raise TimeoutError("播客音频生成超时 (已等待 10 分钟)")
            
        # 6. 下载音频文件到本地 scratch
        output_dir = "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/scratch"
        os.makedirs(output_dir, exist_ok=True)
        local_filename = f"daily_podcast_{today_str}.wav"
        local_path = os.path.join(output_dir, local_filename)
        
        logger.info(f"📥 正在将音频下载到本地: {local_path}...")
        
        # 使用 aiohttp 下载，注入相同的 Proxy 保证畅通
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            # 使用代理
            proxy_url = NOTEBOOKLM_ENV["HTTP_PROXY"]
            async with session.get(audio_url, proxy=proxy_url) as resp:
                if resp.status == 200:
                    with open(local_path, "wb") as f:
                        f.write(await resp.read())
                    logger.info(f"✅ 播客音频下载完成：{local_path}")
                else:
                    raise ConnectionError(f"音频下载失败，HTTP 状态码: {resp.status}")
                    
        return local_path
        
    except Exception as e:
        logger.error(f"❌ 播客自动生成失败: {e}", exc_info=True)
        raise e
        
    finally:
        # 7. 销毁临时笔记本，清理云端
        if notebook_id:
            try:
                logger.info(f"🧹 正在清理云端笔记本: {notebook_id}...")
                del_res = await client.call_tool("notebook_delete", {
                    "notebook_id": notebook_id,
                    "confirm": True
                })
                logger.info(f"清理完成: {del_res}")
            except Exception as clean_err:
                logger.warning(f"清理云端笔记本失败 (可忽略): {clean_err}")
                
        await client.close()


if __name__ == "__main__":
    # 局部单机测试入口
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    loop = asyncio.get_event_loop()
    try:
        path = loop.run_until_complete(generate_podcast_workflow())
        print(f"Workflow test complete. Output: {path}")
    except Exception as exc:
        print(f"Workflow test failed: {exc}")
