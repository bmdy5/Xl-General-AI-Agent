"""NotebookLM MCP 客户端接口与高保真静默下载器 (自 auto_podcast 解耦)"""

import os
import json
import logging
import asyncio
import requests
import urllib3
from agent.config import settings

logger = logging.getLogger(__name__)

# 禁用 requests 的不安全连接警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_notebooklm_config():
    """获取动态解析的 notebooklm 集中配置项"""
    cfg = settings.get("notebooklm", {})
    vpath = cfg.get("vault_path", "/Users/xiaofeng/Documents/个人博客/学习笔记")
    bpath = cfg.get("bin_path", "/Users/xiaofeng/.gemini/antigravity/scratch/venv/bin/notebooklm-mcp")
    env = cfg.get("env", {
        "HOME": "/Users/xiaofeng/.gemini/antigravity/scratch",
        "HTTP_PROXY": "http://127.0.0.1:7897",
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "ALL_PROXY": "http://127.0.0.1:7897",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    })
    return vpath, bpath, env

class NotebookLMMCPClient:
    """高保真、维持长连接的 NotebookLM MCP 客户端."""
    
    def __init__(self):
        _, bpath, env = get_notebooklm_config()
        self.cmd = [bpath]
        self.env = env
        self.proc = None
        self.msg_id = 1
        
    async def start(self):
        env_copy = os.environ.copy()
        env_copy.update(self.env)
        
        # 采用 cmd 列表首个元素传参，绝不使用含星号的解包
        self.proc = await asyncio.create_subprocess_exec(
            self.cmd[0],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env_copy
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

def load_notebooklm_cookies() -> dict:
    """加载 ~/.notebooklm-mcp/auth.json 中的 Cookies，用于高保真文件下载的身份验证."""
    _, _, n_env = get_notebooklm_config()
    auth_path = "/Users/xiaofeng/.notebooklm-mcp/auth.json"
    if not os.path.exists(auth_path):
        auth_path = os.path.join(n_env["HOME"], ".notebooklm-mcp/auth.json")
    if not os.path.exists(auth_path):
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

def download_podcast_silently_sync(audio_url: str, local_path: str, proxies: dict = None) -> bool:
    """使用绝对高保真 Cookie 跨域隔离机制，在 Python 同步上下文中下载音频."""
    cookies = load_notebooklm_cookies()
    if not cookies:
        logger.warning("⚠️ 未加载到有效 cookies，静默下载可能失败")
        
    # 重构 accept 头，彻底消灭星号
    headers_template = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "audio/mpeg,audio/wav,application/octet-stream",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://notebooklm.google.com/",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "audio",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }
    
    session = requests.Session()
    session.headers.update(headers_template)
    if proxies:
        session.proxies.update(proxies)
        
    # 精确分配 Cookie 隔离域名，防止 Clear cache & cookies 冲突和 403 Forbidden
    for k, v in cookies.items():
        if k in ["_gcl_au", "_ga", "_ga_W0LDH41ZCB", "SEARCH_SAMESITE", "AEC", "NID", "__Secure-BUCKET", "ACCOUNT_CHOOSER"]:
            continue
            
        if "OSID" in k:
            session.cookies.set(k, v, domain=".googleusercontent.com", path="/")
        elif k.startswith("__Host-"):
            session.cookies.set(k, v, domain="notebooklm.google.com", path="/")
        else:
            session.cookies.set(k, v, domain=".google.com", path="/")
            
    try:
        logger.info(f"🚀 [静默下载] 启动隔离 Cookie 下载流，目标 URL: {audio_url}")
        r = session.get(audio_url, verify=False, allow_redirects=True, timeout=60)
        
        is_html = b"<!doctype html>" in r.content.lower() or b"<html" in r.content.lower()
        if r.status_code == 200 and not is_html and len(r.content) > 100000:
            with open(local_path, "wb") as f:
                f.write(r.content)
            logger.info(f"🎯 [静默下载] 隔离 Cookie 后台下载 100% 成功！保存至: {local_path} | 大小: {len(r.content)} 字节")
            return True
        else:
            logger.warning(f"⚠️ [静默下载] 校验失败。状态码: {r.status_code}, 大小: {len(r.content)}, 是否为 HTML: {is_html}")
            if is_html:
                logger.warning(f"内容前 200 字节: {r.content[:200]}")
            return False
    except Exception as e:
        logger.error(f"❌ [静默下载] 隔离 Cookie 下载期间发生错误: {e}")
        return False
