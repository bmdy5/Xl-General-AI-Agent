"""项目全局路径常量，统一管理所有目录路径解析，消除各处重复的 parents[2] 拼接。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_MEMORY_DIR = PROJECT_ROOT / "agent_memory"
SKILLS_DIR = AGENT_MEMORY_DIR / "skills"
EXPERIENCES_DIR = AGENT_MEMORY_DIR / "experiences"
CONTEXT_DIR = AGENT_MEMORY_DIR / "context"
SELF_EVOLUTION_DIR = SKILLS_DIR / "自学习技能"
