"""Agent 核心外观 Facade 类.

所有的复杂逻辑已被物理拆分到 prompt_builder.py, history_repair.py, react_loop.py.
本文件仅保留核心状态管理与对外的 run() / close() 公开接口.
"""

import asyncio
import enum
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator, Optional
from .config import settings

logger = logging.getLogger(__name__)

from .llm import LLMClient
from ..memory.manager import MemoryManager
from ..session.handler import SessionHandler
from ..tools.registry import ToolRegistry
from .compressor import ContextCompressor
from ..memory.error_tracker import ErrorTracker

# 导入静态提示词以维持后向兼容性
from .prompt_builder import STATIC_PROMPT
from ..memory.error_tracker import ERROR_INDICATORS

class AgentMode(enum.Enum):
    NORMAL = "normal"
    DEEP = "deep"

class PermissionCategory(enum.Enum):
    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"

NORMAL_TIMEOUT = 300
DEEP_TIMEOUT = 7200
DEBUG_KEYWORDS = ["报错", "异常", "不对", "错误", "失败", "bug", "怎么回事", "为啥不行"]

def quick_transition(user_input: str) -> Optional[str]:
    """短输入跳过，其余由LLM自然生成第一句."""
    return None

class Agent:
    """通用 Agent — 编排与状态管理 Facade."""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        memory: Optional[MemoryManager] = None,
        session: Optional[SessionHandler] = None,
        system_prompt: str = "",
        max_turns: int = 30,
    ):
        self.llm = llm
        self.registry = registry
        self.memory = memory or MemoryManager()
        self.session = session
        self.static_prompt = system_prompt or STATIC_PROMPT
        self.max_turns = max_turns

        # 初始化画像缓存
        import json as _json
        profile_file = self.memory.base_dir / "persona_profile.json"
        if not profile_file.exists():
            template_file = Path(__file__).resolve().parents[1] / "resources" / "default_persona.json"
            if template_file.exists():
                default_profile = _json.loads(template_file.read_text(encoding="utf-8"))
            else:
                default_profile = {"name": "小萤", "gender": "女", "user_address": "亮哥",
                                   "tone_style": "", "preferences": [], "avoid_list": []}
            try:
                profile_file.write_text(_json.dumps(default_profile, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to init persona_profile.json: {e}")
        try:
            self._persona_cache = _json.loads(profile_file.read_text(encoding="utf-8"))
        except Exception:
            self._persona_cache = {"name": "小萤", "gender": "女", "user_address": "亮哥",
                                   "tone_style": "", "preferences": [], "avoid_list": []}

        self.compressor = ContextCompressor(llm=llm, max_tokens=80000)

        self.messages: list[dict] = []
        self._history_loaded = False
        self._abort = asyncio.Event()
        self._permission_granted = asyncio.Event()
        self._turn_count = 0
        self._total_tokens = 0  # 健全性声明：确保任何测试或调用场景下属性均具备
        self._mode = AgentMode.NORMAL
        self._task_write_approved = False
        self._task_start_time = 0.0
        self._original_goal = None
        self.role = "admin"
        self.current_user_id = "未知"
        self.error_tracker = ErrorTracker()
        self.is_maintenance = False
        self._sandbox_violation_dict = {}
        self._active_tasks: set[asyncio.Task] = set()

    @property
    def sandbox_violation_count(self) -> int:
        user_id = getattr(self, "current_user_id", "未知")
        return self._sandbox_violation_dict.get(user_id, 0)

    @sandbox_violation_count.setter
    def sandbox_violation_count(self, value: int):
        user_id = getattr(self, "current_user_id", "未知")
        self._sandbox_violation_dict[user_id] = value

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """创建强引用的异步任务."""
        task = asyncio.create_task(coro)
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    async def close(self):
        """优雅关闭 Agent，安全取消并等待所有追踪的任务."""
        if self._active_tasks:
            logger.info(f"Closing Agent: Cancelling {len(self._active_tasks)} active background tasks...")
            for task in list(self._active_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.wait(list(self._active_tasks))
            self._active_tasks.clear()

    async def run(
        self,
        user_input: str,
        stream: bool = False,
        state_prefix: str = "",
        real_sender_id: str = "",
        real_sender_name: str = "",
        group_id: str = ""
    ) -> AsyncGenerator[dict, None]:
        self.current_state_prefix = state_prefix
        self.current_group_id = group_id
        
        if real_sender_id:
            self.current_user_id = real_sender_id
            admin_id = os.getenv("QQ_ADMIN_ID", os.getenv("ADMIN_ID", "1705919142"))
            if real_sender_id == admin_id:
                self.role = "admin"
            else:
                self.role = "coworker"
        
        if getattr(self, "role", "coworker") == "coworker" and self.sandbox_violation_count >= 2:
            yield {
                "type": "error",
                "content": "⚠️ [安全保护] 抱歉，由于涉及亮哥的隐私和系统安全，您的沙箱会话已被限制。如需继续交流，请联系亮哥。"
            }
            return

        self._abort.clear()
        self._permission_granted.clear()
        self._turn_count = 0
        self._total_tokens = 0
        self._task_write_approved = False
        self._task_start_time = asyncio.get_event_loop().time()

        if self.memory:
            try:
                self._create_tracked_task(self.memory.gc_and_merge_fragmented_memories())
            except Exception as e:
                logger.warning(f"Failed to trigger background Memory GC: {e}")

        if self.session and not self._history_loaded:
            self._history_loaded = True
            try:
                history = await self.session.initialize()
                system_msgs = [m for m in history if m.get("role") == "system"]
                recent = [m for m in history if m.get("role") != "system"][-15:]
                self.messages = system_msgs + recent
                
                for msg in system_msgs:
                    content = msg.get("content", "")
                    if "## 原始目标" in content:
                        goal_part = content.split("## 原始目标\n")[-1].split("##")[0].strip()
                        if goal_part:
                            self._original_goal = {"role": "user", "content": goal_part}
                            break
                if not self._original_goal:
                    for msg in history:
                        if msg.get("role") == "user":
                            c = msg.get("content", "")
                            if len(c) > 10 and not any(kw in c for kw in DEBUG_KEYWORDS):
                                self._original_goal = {"role": "user", "content": c}
                                break
                                
                if self.messages:
                    logger.info(f"Session restored: {len(self.messages)} msgs (RAG handles full context)")
            except Exception as e:
                logger.warning(f"Failed to load session context: {e}")

        await self.error_tracker.load_recipes()

        user_msg = {
            "role": "user",
            "content": user_input
        }
        if real_sender_id:
            user_msg["real_sender_id"] = real_sender_id
        if real_sender_name:
            user_msg["real_sender_name"] = real_sender_name

        is_duplicate = False
        if self.messages:
            user_msgs = [m for m in self.messages if m.get("role") == "user"][-3:]
            for old_msg in reversed(user_msgs):
                if old_msg.get("content") == user_input:
                    old_sender = str(old_msg.get("real_sender_id", "")).strip()
                    new_sender = str(real_sender_id or "").strip()
                    if old_sender == new_sender:
                        try:
                            old_idx = self.messages.index(old_msg)
                            has_reply = any(m.get("role") == "assistant" for m in self.messages[old_idx + 1:])
                        except ValueError:
                            has_reply = False

                        if not has_reply:
                            is_duplicate = True
                            if real_sender_id and not old_msg.get("real_sender_id"):
                                old_msg["real_sender_id"] = real_sender_id
                            if real_sender_name and not old_msg.get("real_sender_name"):
                                old_msg["real_sender_name"] = real_sender_name
                            break

        if not is_duplicate:
            self.messages.append(user_msg)
            if self.session:
                await self.session.append_message(user_msg)

        is_explicit_new_task = any(user_input.startswith(prefix) for prefix in ["新任务：", "新任务:", "重新开始：", "重新开始:", "新需求：", "新需求:"])
        if self._original_goal is None or is_explicit_new_task:
            clean_input = user_input
            if is_explicit_new_task:
                for prefix in ["新任务：", "新任务:", "重新开始：", "重新开始:", "新需求：", "新需求:"]:
                    if clean_input.startswith(prefix):
                        clean_input = clean_input[len(prefix):].strip()
            
            min_len = 5 if is_explicit_new_task else 15
            if len(clean_input) > min_len and not any(kw in clean_input for kw in DEBUG_KEYWORDS):
                self._original_goal = {"role": "user", "content": clean_input}

        turn = 0
        try:
            async for event in self._run_loop(user_input, turn, stream=stream):
                yield event
        except asyncio.CancelledError:
            yield {"type": "aborted"}
        finally:
            self._abort.clear()

    async def _handle_tool_error(self, tool_name: str, error_text: str):
        """工具错误分类记录."""
        should_report, level = self.error_tracker.should_report(error_text)
        from ..memory.error_tracker import L2_SELF_HEAL
        if level == L2_SELF_HEAL:
            recipe = self.error_tracker.find_recipe(error_text)
            if recipe:
                self.error_tracker.save_recipe(error_text, recipe)

        if should_report:
            err_key = self.error_tracker._key(error_text)
            count = self.error_tracker._counts.get(err_key, 0)
            if count >= 3:
                logger.warning(f"Error pattern [{tool_name}]: {error_text[:100]} (x{count})")

    async def _repair_history(self):
        """历史对话结构自动健壮修护."""
        from .history_repair import repair_history
        await repair_history(self)

    async def _apply_sliding_window_and_scratchpad(self):
        """滑动窗口自适应截断."""
        from .history_repair import apply_sliding_window_and_scratchpad
        await apply_sliding_window_and_scratchpad(self)

    async def _run_loop(self, user_input: str, turn: int, stream: bool = False) -> AsyncGenerator[dict, None]:
        """代理执行推理循环."""
        from .react_loop import run_loop
        async for event in run_loop(self, user_input, turn, stream=stream):
            yield event

    async def _llm_chat(self, messages: list[dict], tools: list[dict]) -> tuple:
        """代理非流式 LLM 调用."""
        from .react_loop import llm_chat
        return await llm_chat(self, messages, tools)

    async def _llm_stream(self, messages: list[dict], tools: list[dict]) -> AsyncGenerator[dict, None]:
        """代理流式 LLM 调用."""
        from .react_loop import llm_stream
        async for event in llm_stream(self, messages, tools):
            yield event

    def set_mode(self, mode: AgentMode) -> None:
        self._mode = mode

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def approve_permission(self) -> None:
        self._permission_granted.set()

    def deny_permission(self) -> None:
        self._abort.set()
        self._permission_granted.set()

    def abort(self):
        self._abort.set()
        self._permission_granted.set()

    def clear_history(self):
        self.messages.clear()

    def _classify_permission(self, tool_name: str, tool_args: dict) -> PermissionCategory:
        tool = self.registry.get(tool_name)
        if tool is None:
            return PermissionCategory.SAFE
        if tool_name == "save_memory" and (tool_args or {}).get("action") == "merge_to_core":
            if self.is_maintenance:
                return PermissionCategory.SAFE
            return PermissionCategory.WRITE
        if not tool.needs_permissions(tool_args):
            return PermissionCategory.SAFE
        if tool_name == "bash":
            from ..tools.bash_tool import BashTool
            category_str = BashTool.classify_command(tool_args.get("command", ""))
            return {
                "safe": PermissionCategory.SAFE,
                "write": PermissionCategory.WRITE,
                "dangerous": PermissionCategory.DANGEROUS,
            }[category_str]
        return PermissionCategory.WRITE

    async def _extract_keywords(self, user_input: str) -> list[str]:
        """分词提取关键词."""
        from .prompt_builder import extract_keywords
        return await extract_keywords(user_input)

    def _quick_transition(self, user_input: str) -> Optional[str]:
        return quick_transition(user_input)

    async def _build_system_prompt(self) -> str:
        """代理组装 system prompt."""
        from .prompt_builder import build_system_prompt
        return await build_system_prompt(self)

    async def _build_memory_block(self, user_input: str, turn: int) -> Optional[str]:
        """代理构建 memory block."""
        from .prompt_builder import build_memory_block
        return await build_memory_block(self, user_input, turn)
