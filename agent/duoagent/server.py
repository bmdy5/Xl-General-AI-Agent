"""DuoAgent — FastAPI 服务器."""

import asyncio
import json
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.duoagent import Discussion, AGENT_TEMPLATES
from agent.core.llm import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8899
HTML_DIR = os.path.join(os.path.dirname(__file__), "ui")

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="DuoAgent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

discussions: dict[str, Discussion] = {}
active_streams: dict[str, asyncio.Queue] = {}

# 使用与 XL 相同的 LLM 配置
from agent.core.llm import LLMClient as _LLM
llm = _LLM(
    model=os.getenv("MYAGENT_MODEL", "openai/gpt-4o"),
    api_key=os.getenv("MYAGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
    api_base=os.getenv("MYAGENT_API_BASE"),
)


@app.get("/")
async def index():
    """提供前端页面."""
    html_path = os.path.join(HTML_DIR, "index.html")
    if os.path.exists(html_path):
        return HTMLResponse(open(html_path).read())
    return {"error": "UI not found. Run 'xl stitch_generate' first."}


@app.get("/api/templates")
async def get_templates():
    """获取可用 agent 模板."""
    return {
        "templates": {k: {"emoji": v["emoji"], "color": v["color"]} for k, v in AGENT_TEMPLATES.items()}
    }


@app.post("/api/start")
async def start_discussion(request: Request):
    """开始新讨论."""
    body = await request.json()
    topic = body.get("topic", "")
    agents = body.get("agents", ["主持人", "正方", "反方"])
    rounds = min(int(body.get("rounds", 3)), 10)

    if not topic:
        raise HTTPException(400, "topic required")

    disc = Discussion(topic, agents, rounds)
    discussions[disc.id] = disc
    active_streams[disc.id] = asyncio.Queue()

    # 后台启动讨论
    asyncio.create_task(_run_discussion(disc.id))

    return {"id": disc.id, "agents": disc.agents, "rounds": rounds}


async def _run_discussion(disc_id: str):
    """后台运行讨论，事件入队."""
    disc = discussions.get(disc_id)
    if not disc:
        return
    queue = active_streams.get(disc_id)

    try:
        async for event in disc.start(llm):
            if queue:
                await queue.put(event)
    except Exception as e:
        logger.error(f"Discussion {disc_id} error: {e}")
        if queue:
            await queue.put({"type": "error", "content": str(e)})
    finally:
        if queue:
            await queue.put({"type": "done"})


@app.get("/api/stream/{disc_id}")
async def stream_discussion(disc_id: str):
    """SSE 流 — 实时推送讨论事件."""
    queue = active_streams.get(disc_id)
    if not queue:
        raise HTTPException(404, "Discussion not found")

    async def event_generator():
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("done", "error"):
                break
        # Cleanup
        active_streams.pop(disc_id, None)
        discussions.pop(disc_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/history/{disc_id}")
async def get_history(disc_id: str):
    """获取讨论历史."""
    disc = discussions.get(disc_id)
    if not disc:
        raise HTTPException(404, "Not found")
    return {
        "id": disc.id,
        "topic": disc.topic,
        "agents": disc.agents,
        "messages": disc.messages,
        "rounds": disc.rounds,
        "current_round": disc.current_round,
    }


def start():
    """启动服务器."""
    print(f"\n  DuoAgent 圆桌讨论服务器")
    print(f"  打开 http://{HOST}:{PORT}")
    print()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    start()
