import os
import sys
import json
import time
import asyncio
import logging
import urllib.request
import aiohttp
from datetime import datetime, timedelta, timezone

from ..tools.mcp.notebooklm_client import NotebookLMMCPClient, download_podcast_silently_sync, get_notebooklm_config

logger = logging.getLogger(__name__)


def scan_obsidian_notes(vault_path: str = None, hours: int = 48) -> list[dict]:
    """遍历 Obsidian 目录下过去 48 小时变动的所有笔记内容."""
    if not vault_path:
        vpath, _, _ = get_notebooklm_config()
        vault_path = vpath
        
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
        
        logger.info(f"📥 正在将音频下载到本地 (隔离 Cookie 静默下载): {local_path}...")
        proxy_url = NOTEBOOKLM_ENV.get("HTTP_PROXY") or "http://127.0.0.1:7897"
        download_success = await asyncio.to_thread(
            download_podcast_silently_sync,
            audio_url,
            local_path,
            proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None
        )
        if not download_success:
            raise ConnectionError("高保真 Cookie 跨域隔离静默下载失败")
                    
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
