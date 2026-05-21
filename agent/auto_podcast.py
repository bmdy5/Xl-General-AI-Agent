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


def load_notebooklm_cookies() -> dict:
    """加载 ~/.notebooklm-mcp/auth.json 中的 Cookies，用于高保真文件下载的身份验证."""
    auth_path = os.path.join(NOTEBOOKLM_ENV["HOME"], ".notebooklm-mcp/auth.json")
    if not os.path.exists(auth_path):
        # 兼容性寻找系统默认 HOME
        auth_path = os.path.expanduser("~/.notebooklm-mcp/auth.json")
        
    if os.path.exists(auth_path):
        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookies = data.get("cookies", {})
                logger.info(f"🔑 成功从 {auth_path} 加载了 {len(cookies)} 个身份凭证 Cookies 用于音频下载")
                return cookies
        except Exception as e:
            logger.warning(f"⚠️ 读取 auth.json 失败: {e}")
    else:
        logger.warning(f"⚠️ 找不到授权凭证文件: {auth_path}，下载音频时可能会因无凭证跳转登录页")
    return {}


async def call_tool_with_retry(client, tool_name: str, arguments: dict, max_retries: int = 2, delay: int = 3) -> str:
    """带退避和错误捕获的工具调用封装，重试最多 max_retries 次 (共尝试 max_retries + 1 次)."""
    for attempt in range(1, max_retries + 2):
        try:
            res = await client.call_tool(tool_name, arguments)
            # 如果返回的是错误状态的 JSON，抛出以触发重试
            if '"status":"error"' in res or '"status": "error"' in res:
                raise ValueError(f"MCP 端返回接口错误: {res}")
            return res
        except Exception as e:
            if attempt == max_retries + 1:
                logger.error(f"❌ 调用 {tool_name} 彻底失败，已达最大重试次数: {e}")
                raise e
            logger.warning(f"⚠️ 调用 {tool_name} 失败 (第 {attempt} 次尝试): {e}，将在 {delay * attempt}s 后重试...")
            await asyncio.sleep(delay * attempt)


ACTIVE_PODCAST_JSON = "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/scratch/active_podcast.json"


async def start_podcast_generation(debug_mode: bool = False) -> Optional[str]:
    """错峰异步第一阶段：扫描笔记并向 Google 发起音频播客生成任务，持久化状态机."""
    logger.info("🌅 启动亮哥专属学习播客生成流程 (阶段一：发起生成)...")
    
    # 检查是否已有活跃任务
    if os.path.exists(ACTIVE_PODCAST_JSON):
        try:
            with open(ACTIVE_PODCAST_JSON, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("status") == "generating":
                created_at = state.get("created_at", 0)
                # 2小时内视为依然活跃
                if time.time() - created_at < 7200:
                    logger.info("⚠️ 检测到已有正在进行的播客生成任务，跳过重复发起")
                    return "already_running"
        except Exception as e:
            logger.warning(f"读取 active_podcast.json 失败 (将被覆盖): {e}")

    # 1. 扫描变动笔记
    notes = scan_obsidian_notes()
    if not notes:
        logger.info("✨ 过去 48 小时内没有变动的笔记，跳过播客生成。")
        return None
        
    logger.info(f"📚 成功扫描到 {len(notes)} 篇最近变动笔记。")
    
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
        
        create_res = await call_tool_with_retry(client, "notebook_create", {"title": title})
        logger.info(f"创建结果: {create_res}")
        
        import re
        match_id = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', create_res, re.IGNORECASE)
        if not match_id:
            raise ValueError(f"无法解析创建成功的 notebook_id: {create_res}")
        
        notebook_id = match_id.group(0)
        logger.info(f"✅ 获取到 Notebook ID: {notebook_id}")
        
        # 3. 添加笔记文本作为 Source
        logger.info("⏳ 正在上传笔记内容至 NotebookLM...")
        add_res = await call_tool_with_retry(client, "notebook_add_text", {
            "notebook_id": notebook_id,
            "text": combined_content,
            "title": f"过去48小时笔记精选"
        })
        logger.info(f"上传结果: {add_res}")
        
        # 4. 请求生成音频播客
        logger.info("🎙️ 正在向 Google 发起音频播客生成任务...")
        overview_res = await call_tool_with_retry(client, "audio_overview_create", {
            "notebook_id": notebook_id,
            "format": "deep_dive",
            "language": "zh",
            "focus_prompt": "请用幽默、极其硬核且富有极客精神的中文，深度提炼并剖析亮哥的这几篇关于 Agent 与架构设计的学习笔记。两个主持人的语气要自然、如专业技术对谈，核心在于为亮哥（收听者）提供具有启发和实战学习价值的技术观点，避免流于表面或像 AI 的自我记录，拒绝刻板翻译感。",
            "confirm": True
        })
        logger.info(f"音频创建请求已接收: {overview_res}")
        
        # 5. 写入持久化状态机状态文件
        state = {
            "notebook_id": notebook_id,
            "query_count": 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "generating",
            "last_query_time": time.time(),
            "created_at": time.time(),
            "debug_mode": debug_mode
        }
        
        os.makedirs(os.path.dirname(ACTIVE_PODCAST_JSON), exist_ok=True)
        with open(ACTIVE_PODCAST_JSON, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            
        logger.info(f"💾 状态机状态已持久化至 {ACTIVE_PODCAST_JSON}")
        return "initiated"
        
    except Exception as e:
        logger.error(f"❌ 播客阶段一发起失败: {e}", exc_info=True)
        if notebook_id:
            try:
                await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
            except Exception:
                pass
        raise e
    finally:
        await client.close()


async def check_and_download_podcast() -> Optional[str]:
    """错峰异步第二阶段：单次查询云端生成状态。若生成完则下载、清理并返回本地音频路径."""
    if not os.path.exists(ACTIVE_PODCAST_JSON):
        logger.warning("⚠️ 找不到活跃的 active_podcast.json，无法查询")
        return None
        
    try:
        with open(ACTIVE_PODCAST_JSON, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        logger.error(f"读取 active_podcast.json 失败: {e}")
        return None
        
    notebook_id = state.get("notebook_id")
    if not notebook_id:
        logger.error("⚠️ active_podcast.json 中没有 notebook_id 字段")
        return None
        
    logger.info(f"🔄 启动阶段二：查询云端笔记本 {notebook_id} 生成状态...")
    
    client = NotebookLMMCPClient()
    await client.start()
    
    try:
        status_res = await call_tool_with_retry(client, "studio_status", {"notebook_id": notebook_id})
        logger.info(f"查询结果片段: {status_res[:200]}...")
        
        # 判断是否生成失败
        if "failed" in status_res.lower() or "error" in status_res.lower():
            raise ValueError(f"Google 云端生成状态异常: {status_res}")
            
        audio_url = None
        # 尝试解析 JSON 或正则匹配
        try:
            data = json.loads(status_res)
            artifacts = data.get("artifacts", [])
            for art in artifacts:
                val = art.get("audio_url") or art.get("url")
                if art.get("type") == "audio" and val:
                    audio_url = val
                    break
        except Exception:
            import re
            urls = re.findall(r'https?://[^\s"\'\]]+', status_res)
            for u in urls:
                if "audio" in u.lower() or "google" in u.lower():
                    audio_url = u
                    break
                    
        if not audio_url:
            logger.info("⏳ 播客仍未生成完毕，等待下次查询。")
            return "pending"
            
        # 已经生成好！开始下载
        logger.info(f"🎉 识别到音频已生成完毕！URL: {audio_url}")
        today_str = state.get("date") or datetime.now().strftime("%Y-%m-%d")
        
        output_dir = "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/scratch"
        os.makedirs(output_dir, exist_ok=True)
        local_filename = f"daily_podcast_{today_str}.wav"
        local_path = os.path.join(output_dir, local_filename)
        
        logger.info(f"📥 正在将音频下载到本地: {local_path}...")
        download_cookies = load_notebooklm_cookies()
        
        # 将 cookies 转换为 Cookie 头拼接字符串，避免 aiohttp 跨域重定向时带上 cookie 导致死循环 (TooManyRedirects 报错)
        cookie_str = "; ".join([f"{k}={v}" for k, v in download_cookies.items()])
        headers = {
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            proxy_url = NOTEBOOKLM_ENV["HTTP_PROXY"]
            async with session.get(audio_url, headers=headers, proxy=proxy_url) as resp:
                if resp.status == 200:
                    with open(local_path, "wb") as f:
                        f.write(await resp.read())
                    logger.info(f"✅ 音频下载成功：{local_path}")
                else:
                    raise ConnectionError(f"音频下载失败，HTTP 状态码: {resp.status}")
                    
        # 销毁临时笔记本，清理云端
        logger.info(f"🧹 正在清理云端临时笔记本: {notebook_id}...")
        await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
        
        # 删除状态文件
        if os.path.exists(ACTIVE_PODCAST_JSON):
            os.remove(ACTIVE_PODCAST_JSON)
            logger.info("🗑️ 清理本地 active_podcast.json 成功")
            
        return local_path
        
    except Exception as e:
        logger.error(f"❌ 播客阶段二查询与下载发生异常: {e}", exc_info=True)
        raise e
    finally:
        await client.close()


async def force_cleanup_podcast():
    """强制清理早报播客在云端的临时笔记本并删除本地状态文件."""
    if not os.path.exists(ACTIVE_PODCAST_JSON):
        return
        
    try:
        with open(ACTIVE_PODCAST_JSON, "r", encoding="utf-8") as f:
            state = json.load(f)
        notebook_id = state.get("notebook_id")
        if notebook_id:
            logger.info(f"🧹 强行清理云端笔记本: {notebook_id}...")
            client = NotebookLMMCPClient()
            await client.start()
            try:
                await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
                logger.info("云端清理完成")
            except Exception as clean_err:
                logger.warning(f"云端强行清理失败: {clean_err}")
            finally:
                await client.close()
    except Exception as e:
        logger.error(f"force_cleanup_podcast 发生异常: {e}")
    finally:
        if os.path.exists(ACTIVE_PODCAST_JSON):
            os.remove(ACTIVE_PODCAST_JSON)
            logger.info("🗑️ 清理本地 active_podcast.json 成功")


async def generate_podcast_workflow() -> Optional[str]:
    """保留兼容性入口，执行一站式同步阻塞生成流程（供历史单测脚本调用）."""
    init_res = await start_podcast_generation()
    if not init_res or init_res == "already_running":
        return None
        
    # 同步等待循环
    max_attempts = 30
    poll_interval = 20
    for attempt in range(1, max_attempts + 1):
        await asyncio.sleep(poll_interval)
        try:
            res = await check_and_download_podcast()
            if res and res != "pending":
                return res
        except Exception as e:
            await force_cleanup_podcast()
            raise e
            
    await force_cleanup_podcast()
    raise TimeoutError("播客音频生成超时 (已等待 10 分钟)")


if __name__ == "__main__":
    # 局部单机测试入口
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    loop = asyncio.get_event_loop()
    try:
        path = loop.run_until_complete(generate_podcast_workflow())
        print(f"Workflow test complete. Output: {path}")
    except Exception as exc:
        print(f"Workflow test failed: {exc}")
