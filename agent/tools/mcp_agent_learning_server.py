import os
import sys
import json
import time
import asyncio
import logging
import subprocess
import shutil
from datetime import datetime, timezone
from ddgs import DDGS

# 确保能正常导入项目中的其它模块 (如 agent.llm)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.llm import LLMClient
from agent.auto_podcast import NotebookLMMCPClient, ACTIVE_PODCAST_JSON, call_tool_with_retry

# 设置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mcp_agent_learning")

from mcp.server.fastmcp import FastMCP
mcp = FastMCP("mcp-agent-learning")

OBSIDIAN_AGENT_PATH = "/Users/xiaofeng/Desktop/学习笔记/02-Agent技术"

def _scan_all_notes(path: str = OBSIDIAN_AGENT_PATH) -> list[dict]:
    """递归扫描本地 Obsidian Agent 文件夹下的所有笔记."""
    notes = []
    if not os.path.exists(path):
        logger.warning(f"笔记目录不存在: {path}")
        return notes
        
    for root, _, files in os.walk(path):
        for f in files:
            if f.endswith(".md") and not f.startswith("."):
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, "r", encoding="utf-8") as file:
                        content = file.read().strip()
                    if content:
                        notes.append({
                            "title": os.path.splitext(f)[0],
                            "content": content,
                            "path": fpath,
                            "category": os.path.basename(root)
                        })
                except Exception as e:
                    logger.error(f"读取笔记失败 {f}: {e}")
    return notes

@mcp.tool()
async def list_agent_topics() -> str:
    """扫描本地 Obsidian Agent 学习笔记，智能提炼出 3 个具有深度启发、最适合亮哥温故知新的极客选题."""
    logger.info("🔍 正在扫描 Obsidian 笔记以提取选题...")
    notes = _scan_all_notes()
    if not notes:
        return json.dumps({
            "status": "success",
            "topics": [
                "1. 记忆系统的多级缓存与持久化架构",
                "2. 复杂环境下 Tool Calling 的安全校验机制",
                "3. 多智能体协同通讯协议与共识演进"
            ],
            "message": "未找到本地笔记，提供默认硬核选题"
        }, ensure_ascii=False, indent=2)

    # 抽取笔记标题与摘要，提交给 LLM
    note_summaries = []
    for n in notes[:15]: # 取最近前15篇用于分析
        note_summaries.append(f"- 标题: {n['title']} (分类: {n['category']})")
        
    summary_text = "\n".join(note_summaries)
    
    prompt = f"""
你是一个顶尖的极客 AI 导师。现在这里有亮哥本地的 Agent 技术学习笔记库列表：
{summary_text}

请你根据亮哥本地的笔记，并结合当前最前沿的 Agent (大模型智能体) 业界动态（如记忆检索、工具链鉴权、多智能体协同、状态机设计等），设计出 3 个极其有质量、硬核、能够吸引亮哥并且对他有巨大启发价值的学习选题。
选题应该把本地笔记的方向与当前前沿的技术问题结合。
例如：
1. 记忆系统的多级缓存与持久化架构：探讨本地内存与外部向量库的检索机制...

请以如下严格的 JSON 数组格式直接返回（不要包裹 markdown 块，只要纯 JSON）：
[
  "1. 选题名称：[一句话简短说明为什么该选题重要且吸引人]",
  "2. 选题名称：[一句话简短说明为什么该选题重要且吸引人]",
  "3. 选题名称：[一句话简短说明为什么该选题重要且吸引人]"
]
"""
    llm = LLMClient()
    try:
        res = await llm.chat([{"role": "user", "content": prompt}])
        content = res.get("content", "").strip()
        # 清理可能的 markdown 标记
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        topics = json.loads(content)
        return json.dumps({"status": "success", "topics": topics}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"LLM 提炼选题失败: {e}")
        # 兜底选项
        fallback = [
            "1. 记忆系统的多级缓存与持久化设计 (根据您记忆系统笔记拓展)",
            "2. 工具系统调用安全与沙箱隔离策略 (根据您工具系统笔记拓展)",
            "3. 复杂工作流下多智能体拓扑通讯设计 (根据您多智能体设计笔记拓展)"
        ]
        return json.dumps({"status": "success", "topics": fallback}, ensure_ascii=False, indent=2)

@mcp.tool()
async def synthesize_agent_notes(topic: str, use_web_search: bool = True) -> str:
    """接受选定的主题，深度检索本地 Obsidian 笔记并联网补充前沿知识，融合成一篇约 2000 字的高质量极客深度笔记."""
    logger.info(f"📝 开始围绕专题【{topic}】融合高价值深度笔记...")
    
    # 1. 扫描本地相关笔记
    notes = _scan_all_notes()
    related_notes = []
    # 提取主题的关键词进行模糊匹配
    keywords = [topic]
    if "：" in topic:
        keywords.extend(topic.split("："))
    if " " in topic:
        keywords.extend(topic.split())
        
    for n in notes:
        match_score = sum(1 for kw in keywords if kw.lower() in n["title"].lower() or kw.lower() in n["content"].lower())
        if match_score > 0:
            related_notes.append((match_score, n))
            
    # 按匹配度降序
    related_notes.sort(key=lambda x: x[0], reverse=True)
    local_context = ""
    for _, rn in related_notes[:5]: # 最多融合 5 篇
        local_context += f"=== 笔记: {rn['title']} ===\n{rn['content']}\n\n"

    # 2. 进行联网搜索补充
    web_context = ""
    if use_web_search:
        logger.info(f"🌐 正在执行联网搜索: {topic}")
        try:
            with DDGS() as ddgs:
                # 限制搜索后端为极速高可用的 duckduckgo,brave，彻底规避 mojeek 等慢速引擎
                results = list(ddgs.text(topic, backend="duckduckgo,brave", max_results=4))
                for i, r in enumerate(results, 1):
                    web_context += f"搜索参考 {i}: {r['title']}\n来源: {r['href']}\n摘要: {r['body']}\n\n"
        except Exception as se:
            logger.warning(f"联网搜索失败: {se}，将仅使用本地笔记融合。")

    # 3. LLM 融合成 2000 字
    prompt = f"""
你是一个资深的极客 AI 架构师和资深 Agent 研究员。现在需要你围绕专题【{topic}】，融合同学亮哥的本地学习笔记和最新的互联网参考资料，合成为一篇约 2000 字的、极其硬核、条理清晰、且具有极高学习和温故知新价值的 Markdown 格式深度笔记。

本地学习笔记输入：
{local_context or "（未找到直接相关的本地笔记，请基于该主题提供业界标准方案）"}

前沿互联网参考输入：
{web_context or "（未获取到网络前沿信息）"}

请按照以下架构和规范合成：
1. **深度与干货**：拒绝空话和科普，直切技术本质，提供清晰的代码逻辑伪代码、架构时序图（Mermaid）、或详细的技术对比。
2. **语气风格**：幽默且富有极客精神，就像在和亮哥面对面对谈。要多剖析“实践中的痛点”和“避坑指南”。
3. **结构大纲**：
   - 🎯 专题核心痛点与业界现状
   - 🛠️ 本地实践剖析（融合亮哥的笔记精髓）
   - 🚀 业界前沿演进与方案借鉴
   - 💡 亮哥专属温故知新架构设计 (给出高保真演进思路或 Mermaid 时序/逻辑图)
   - ⚠️ 踩坑点防范与未来技术趋势

请直接输出高质量的 Markdown 文本。
"""
    llm = LLMClient()
    try:
        res = await llm.chat([{"role": "user", "content": prompt}], model_override="openai/gpt-4o")
        synthesized_text = res.get("content", "").strip()
        
        # 写入本地 scratch 目录保存
        output_dir = "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/scratch"
        os.makedirs(output_dir, exist_ok=True)
        note_path = os.path.join(output_dir, "synthesized_note.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(synthesized_text)
            
        logger.info(f"🎉 2000字极客笔记合成完毕，已保存至: {note_path}")
        return json.dumps({
            "status": "success",
            "note_path": note_path,
            "length": len(synthesized_text),
            "preview": synthesized_text[:300] + "..."
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"合成笔记失败: {e}")
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False, indent=2)

@mcp.tool()
async def launch_podcast_generation(note_path: str, topic: str, debug_mode: bool = False) -> str:
    """将指定的极客笔记投喂给 NotebookLM 并发起音频播客的云端生成."""
    logger.info(f"🎙️ 正在将 {note_path} 的内容上传至 NotebookLM 并发起播客生成...")
    if not os.path.exists(note_path):
        return json.dumps({"status": "error", "message": f"找不到极客笔记文件: {note_path}"})

    with open(note_path, "r", encoding="utf-8") as f:
        combined_content = f.read()

    client = NotebookLMMCPClient()
    await client.start()
    
    notebook_id = None
    try:
        # 1. 创建 Notebook
        today_str = datetime.now().strftime("%Y-%m-%d")
        title = f"亮哥极客播客-专题-{topic}-{today_str}"
        logger.info(f"📓 正在云端创建笔记本: {title}...")
        
        create_res = await call_tool_with_retry(client, "notebook_create", {"title": title})
        import re
        match_id = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', create_res, re.IGNORECASE)
        if not match_id:
            raise ValueError(f"无法解析 notebook_id: {create_res}")
        notebook_id = match_id.group(0)
        logger.info(f"✅ 获取到 Notebook ID: {notebook_id}")
        
        # 2. 添加 2000 字极客笔记
        logger.info("⏳ 正在上传 2000 字融合笔记到云端...")
        await call_tool_with_retry(client, "notebook_add_text", {
            "notebook_id": notebook_id,
            "text": combined_content,
            "title": f"专题深度笔记"
        })
        
        # 3. 发起双人中文技术播客生成
        logger.info("🎙️ 正在向 Google 发起音频播客生成任务...")
        focus_prompt = f"请针对本篇关于【{topic}】的深度极客研究笔记，展开一场极富思考深度、专业硬核且言语幽默自然的中文技术对谈。两个主持人要像真正的顶尖架构师和高级开发员那样，通过互相探讨、质疑和案例印证，为听众亮哥（收听者）提供醍醐灌顶的技术启发，拒绝AI腔调、翻译腔和浅尝辄止。"
        
        overview_res = await call_tool_with_retry(client, "audio_overview_create", {
            "notebook_id": notebook_id,
            "format": "deep_dive",
            "language": "zh",
            "focus_prompt": focus_prompt,
            "confirm": True
        })
        logger.info(f"云端生成已成功拉起: {overview_res}")
        
        # 4. 写入持久化状态机
        state = {
            "notebook_id": notebook_id,
            "query_count": 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "generating",
            "last_query_time": time.time(),
            "created_at": time.time(),
            "debug_mode": debug_mode,
            "topic": topic
        }
        
        os.makedirs(os.path.dirname(ACTIVE_PODCAST_JSON), exist_ok=True)
        with open(ACTIVE_PODCAST_JSON, "w", encoding="utf-8") as sf:
            json.dump(state, sf, ensure_ascii=False, indent=2)
            
        logger.info(f"💾 状态机已保存至: {ACTIVE_PODCAST_JSON}")
        return json.dumps({"status": "success", "notebook_id": notebook_id, "message": "云端生成已成功拉起，本地状态已持久化"})
    except Exception as e:
        logger.error(f"❌ 发起云端播客生成失败: {e}", exc_info=True)
        if notebook_id:
            try:
                logger.info(f"🧹 发生异常，清理已创建的笔记本: {notebook_id}...")
                await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
            except Exception as clean_err:
                logger.warning(f"清理临时笔记本失败: {clean_err}")
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False, indent=2)
    finally:
        await client.close()

async def check_and_push_podcast() -> str:
    """查询播客生成状态。若生成完毕，触发常规 Chrome 静默下载，后台轮询 Downloads 捕获音频，清理环境并返回本地 WAV 绝对路径."""
    if not os.path.exists(ACTIVE_PODCAST_JSON):
        return json.dumps({"status": "no_active_task"})
        
    try:
        with open(ACTIVE_PODCAST_JSON, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"读取状态机失败: {e}"})
        
    notebook_id = state.get("notebook_id")
    topic = state.get("topic", "未命名专题")
    if not notebook_id:
        return json.dumps({"status": "error", "message": "状态机中缺失 notebook_id"})
        
    logger.info(f"🔄 正在查询笔记本 {notebook_id} (专题: {topic}) 生成状态...")
    
    client = NotebookLMMCPClient()
    await client.start()
    
    try:
        status_res = await call_tool_with_retry(client, "studio_status", {"notebook_id": notebook_id})
        logger.info("云端状态查询结果返回。")
        
        if "failed" in status_res.lower() or "error" in status_res.lower():
            raise ValueError(f"Google 侧生成发生错误: {status_res}")
            
        audio_url = None
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
            urls = re.findall(r"https?://[^\s\"'\]]+", status_res)
            for u in urls:
                if "audio" in u.lower() or "google" in u.lower():
                    audio_url = u
                    break
                    
        if not audio_url:
            logger.info("⏳ 云端播客仍在生成中...")
            return json.dumps({"status": "pending"})
            
        # 已经生成好！开始静默流式下载
        logger.info(f"🎉 识别到音频已生成完毕！URL: {audio_url}")
        today_str = state.get("date") or datetime.now().strftime("%Y-%m-%d")
        
        output_dir = "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/scratch"
        os.makedirs(output_dir, exist_ok=True)
        local_path = os.path.join(output_dir, f"daily_podcast_{today_str}.wav")
        
        # 优先使用 Python HTTP 拼接 Cookie 进行静默后台下载
        logger.info(f"📥 正在尝试使用 Cookie 静默后台下载至: {local_path}...")
        download_success = False
        
        try:
            from agent.auto_podcast import download_podcast_silently_sync, NOTEBOOKLM_ENV
            proxy_url = NOTEBOOKLM_ENV.get("HTTP_PROXY") or "http://127.0.0.1:7897"
            logger.info(f"使用代理: {proxy_url} 进行隔离 Cookie 静默下载...")
            
            download_success = await asyncio.to_thread(
                download_podcast_silently_sync,
                audio_url,
                local_path,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None
            )
            if not download_success:
                raise ConnectionError("高保真 Cookie 隔离静默下载返回失败")
        except Exception as silent_err:
            logger.error(f"❌ 静默下载失败: {silent_err}。已彻底废除 Chrome 物理兜底，防止桌面弹窗打扰。")
            raise silent_err
            
        # 销毁云端 Notebook
        logger.info(f"🧹 正在云端销毁临时 Notebook: {notebook_id}...")
        await client.call_tool("notebook_delete", {"notebook_id": notebook_id, "confirm": True})
        
        # 清理状态机
        if os.path.exists(ACTIVE_PODCAST_JSON):
            os.remove(ACTIVE_PODCAST_JSON)
            
        return json.dumps({
            "status": "success",
            "local_path": local_path,
            "topic": topic,
            "message": "音频极速下载捕获成功，环境清理完毕"
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"❌ 播客捕获时发生异常: {e}", exc_info=True)
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False, indent=2)
    finally:
        await client.close()

if __name__ == "__main__":
    # 启动 FastMCP stdio server
    mcp.run()
