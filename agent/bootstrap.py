import os
import sys
import logging
import warnings
from .config import settings

logger = logging.getLogger("agent.bootstrap")

def setup_system():
    """初始化系统环境：警告过滤与统一日志格式"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

def build_agent(session_id: str = "default"):
    """规范化组装 Agent：LLM + 物理子包工具集 + 记忆 + 会话"""
    from .llm import LLMClient
    from .core import Agent
    from .memory.manager import MemoryManager
    from .session.handler import SessionHandler
    
    # 从最新的 7 大独立解耦物理包导入 21 个核心工具
    from .tools.filesystem.read import ReadFileTool
    from .tools.filesystem.write import WriteFileTool
    from .tools.filesystem.edit import EditFileTool
    from .tools.filesystem.bash import BashTool
    
    from .tools.agent.swarm import SwarmTool
    from .tools.agent.sequence import RunSequenceTool
    from .tools.agent.spawn import SpawnAgentTool
    from .tools.agent.schedule import ScheduleTaskTool
    
    from .tools.meta.manage import ManageToolTool
    from .tools.meta.memory import MemoryTool
    from .tools.meta.organize import OrganizeNotesTool
    
    from .tools.web.search import WebSearchTool
    from .tools.web.fetch import WebFetchTool
    
    from .tools.media.image_gen import Image2GenerateTool
    from .tools.media.image_read import ReadImageTool
    
    from .tools.mcp.xiaohongshu import XiaohongshuTool
    from .tools.mcp.notebooklm import NotebookLMTool
    from .tools.mcp.stitch import StitchTool
    from .tools.mcp.client import MCPClientTool
    
    from .tools.qq.status import GetQQStatusTool
    from .tools.qq.send_message import SendQQMessageTool
    
    from .tools.registry import registry

    model_vision = os.environ.get("MYAGENT_MODEL", "openai/gpt-4o")
    api_key = os.environ.get("MYAGENT_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
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

    memory = MemoryManager()
    session = SessionHandler(session_id)

    return Agent(
        llm=llm,
        registry=registry,
        memory=memory,
        session=session,
        max_turns=int(os.environ.get("MYAGENT_MAX_TURNS", "40"))
    )
