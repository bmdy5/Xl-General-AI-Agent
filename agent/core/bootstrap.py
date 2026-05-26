import os
import sys
import logging
import warnings
from .config import settings

logger = logging.getLogger("agent.bootstrap")

def _load_dotenv():
    """从项目根目录 .env 文件加载环境变量（若存在）。"""
    from pathlib import Path
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)

def setup_system():
    """初始化系统环境：加载 .env、警告过滤与统一日志格式"""
    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

def build_agent(session_id: str = "default"):
    """规范化组装 Agent：LLM + 物理子包工具集 + 记忆 + 会话"""
    from agent.core.llm import LLMClient
    from agent.core.agent import Agent
    from agent.memory.manager import MemoryManager
    from agent.session.handler import SessionHandler
    
    # 从最新的 7 大独立解耦物理包导入 21 个核心工具
    from agent.tools.filesystem.read import ReadFileTool
    from agent.tools.filesystem.write import WriteFileTool
    from agent.tools.filesystem.edit import EditFileTool
    from agent.tools.filesystem.bash import BashTool
    
    from agent.tools.agent.swarm import SwarmTool
    from agent.tools.agent.sequence import RunSequenceTool
    from agent.tools.agent.spawn import SpawnAgentTool
    from agent.tools.agent.schedule import ScheduleTaskTool
    
    from agent.tools.meta.manage import ManageToolTool
    from agent.tools.meta.memory import MemoryTool
    from agent.tools.meta.organize import OrganizeNotesTool
    
    from agent.tools.web.search import WebSearchTool
    from agent.tools.web.fetch import WebFetchTool
    
    from agent.tools.media.image_gen import Image2GenerateTool
    from agent.tools.media.image_read import ReadImageTool
    from agent.tools.media.send_image_tool import SendImageToQqTool
    
    from agent.tools.mcp.xiaohongshu import XiaohongshuTool
    from agent.tools.mcp.notebooklm import NotebookLMTool
    from agent.tools.mcp.stitch import StitchTool
    from agent.tools.mcp.client import MCPClientTool
    
    from agent.tools.qq.status import GetQQStatusTool
    from agent.tools.qq.send_message import SendQQMessageTool
    from agent.tools.visual_tools import BrowserScreenshotTool, BrowserClickTool, BrowserTypeTool, BrowserScrollTool, BrowserAgentTool
    
    from agent.tools.registry import registry

    model_vision = os.environ.get("MYAGENT_MODEL", "openai/gpt-4o")
    api_key = os.environ.get("MYAGENT_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    api_base = os.environ.get("MYAGENT_API_BASE") or os.environ.get("OPENAI_API_BASE")

    model_flash = os.environ.get("MYAGENT_MODEL_FLASH", "deepseek/deepseek-v4-flash")
    model_pro = os.environ.get("MYAGENT_MODEL_PRO", "deepseek/deepseek-v4-pro")

    max_tokens = int(os.environ.get("MYAGENT_MAX_TOKENS", "8192"))
    llm = LLMClient(
        model=model_flash,
        api_key=api_key,
        api_base=api_base,
        max_tokens=max_tokens,
        model_vision=model_vision,
        model_pro=model_pro,
    )

    # 注册工具（避免重复注册）
    if not registry.list_names():
        registry.register(ReadFileTool())
        registry.register(WriteFileTool())
        registry.register(EditFileTool())
        registry.register(SwarmTool())
        registry.register(RunSequenceTool())
        registry.register(ManageToolTool())
        registry.register(BashTool(work_dir=os.getcwd()))
        registry.register(WebSearchTool())
        registry.register(WebFetchTool())
        registry.register(ReadImageTool())
        registry.register(Image2GenerateTool())
        registry.register(SendImageToQqTool())
        registry.register(SpawnAgentTool())
        registry.register(StitchTool())
        registry.register(MCPClientTool())
        registry.register(MemoryTool())
        registry.register(OrganizeNotesTool())
        registry.register(ScheduleTaskTool())
        registry.register(XiaohongshuTool())
        registry.register(NotebookLMTool())
        registry.register(GetQQStatusTool())
        registry.register(SendQQMessageTool())
        registry.register(BrowserScreenshotTool())
        registry.register(BrowserClickTool())
        registry.register(BrowserTypeTool())
        registry.register(BrowserScrollTool())
        registry.register(BrowserAgentTool())

    memory = MemoryManager()
    session = SessionHandler(session_id)

    agent_instance = Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        session=session,
        max_turns=int(os.environ.get("MYAGENT_MAX_TURNS", "40"))
    )

    agent_instance.session_key = session_id  # 显式绑定，用于短期记忆持久化与自愈定位
    return agent_instance
