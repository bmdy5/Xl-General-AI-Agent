"""自动记忆工具 — 组合 CC + hermes + openclaw 三家设计.

CC 贡献：5类记忆分类 + 禁止清单
hermes 贡献：action 参数模式 (add/replace/remove/read)
openclaw 贡献：强制召回步骤（Mandatory recall step）
"""

import logging
import re
_re = re
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

MEMORY_TYPES = ["user", "feedback", "project", "reference", "learn"]

FORBIDDEN_LIST = [
    "代码结构/架构信息 — 读代码就知道",
    "Git 历史/提交记录 — git log 就知道",
    "单次调试方案/临时修复 — 修好了就不用记",
    "CLAUDE.md 或 system prompt 已有的内容 — 别存两份",
    "临时任务详情 — 过期的信息",
    "一次性问答 — 没有复用价值的信息",
]


class MemoryTool(BaseTool):
    """自动记忆工具 — agent 自己决定何时调用，存入跨会话记忆."""

    @property
    def name(self) -> str:
        return "save_memory"

    async def description(self) -> str:
        return "Save, replace, remove, or read persistent memories that survive across sessions."

    def is_read_only(self) -> bool:
        return False

    def is_concurrency_safe(self) -> bool:
        return False

    def needs_permissions(self, input_args: Optional[dict] = None) -> bool:
        return True  # 记忆修改需要用户审批

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "⚠️ MANDATORY RECALL STEP: Use this tool to manage persistent memories "
                    "that survive across sessions. You MUST check existing memories before "
                    "answering questions about the user's preferences or past conversations.\n\n"
                    "## When to save (5 types of memory)\n"
                    "- **user**: user role, preferences, goals, tech stack, communication style\n"
                    "- **feedback**: user corrections, work style preferences, things to avoid\n"
                    "- **project**: project facts, decisions, constraints, deadlines\n"
                    "- **reference**: external resource pointers (API docs, repo URLs, service addresses)\n"
                    "- **learn**: patterns discovered across sessions, things that went wrong before\n\n"
                    "## What NOT to save (forbidden list)\n"
                    f"- {FORBIDDEN_LIST[0]}\n- {FORBIDDEN_LIST[1]}\n- {FORBIDDEN_LIST[2]}\n"
                    f"- {FORBIDDEN_LIST[3]}\n- {FORBIDDEN_LIST[4]}\n- {FORBIDDEN_LIST[5]}\n\n"
                    "## When to trigger\n"
                    "- User says 'remember...', '以后...', 'always...', 'never...'\n"
                    "- User corrects you: '不对...', '应该...', '不要...'\n"
                    "- You discover a project fact or constraint worth keeping\n"
                    "- You complete a complex task and notice a reusable pattern\n"
                    "- Similar correction appears 2+ times → suggest saving as a rule"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "replace", "remove", "read"],
                            "description": "add=save new memory, replace=update existing, remove=delete, read=list all",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": MEMORY_TYPES,
                            "description": "Type of memory (user/feedback/project/reference/learn)",
                        },
                        "filename": {
                            "type": "string",
                            "description": "File name for this memory (e.g. 'coding_prefs'). Only for 'add' action.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-line summary for MEMORY.md index. Only for 'add' action.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The memory content. For 'add'=full text, for 'replace'=new text.",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "Text to match for replace/remove. Substring match is OK.",
                        },
                    },
                    "required": ["action"],
                },
            },
        }

    async def validate_input(self, input_args: dict, context: Any = None) -> dict:
        action = input_args.get("action", "")
        if action not in ("add", "replace", "remove", "read"):
            return {"result": False, "message": f"Invalid action: {action}"}
        if action in ("add", "replace") and not input_args.get("content"):
            return {"result": False, "message": "content is required for add/replace"}
        if action == "add":
            if not input_args.get("filename"):
                return {"result": False, "message": "filename is required for add"}
            if not input_args.get("description"):
                return {"result": False, "message": "description is required for add"}
        return {"result": True, "message": ""}

    async def call(
        self, input_args: dict, context: Any = None
    ) -> AsyncGenerator[ToolResult, None]:
        action = input_args["action"]
        memory_type = input_args.get("memory_type", "user")

        try:
            if context and hasattr(context, "memory"):
                mm = context.memory
            else:
                from agent.memory.manager import MemoryManager
                mm = MemoryManager()

            if action == "read":
                memories = mm.list_memories()
                if not memories:
                    yield ToolResult(type="result", data="(no memories yet)")
                    return
                yield ToolResult(
                    type="result",
                    data="\n".join(memories),
                    result_for_assistant=(
                        "Current memories (latest first):\n" + "\n".join(memories)
                    ),
                )
                return

            if action == "add":
                filename = input_args["filename"]
                desc = input_args["description"]
                content = input_args["content"]

                # ── 冲突检测：检查是否存在同主题旧记忆 ──
                existing = await _find_similar_memory(mm, desc, filename)
                if existing:
                    merged = await _merge_memories(context, existing, desc, content)
                    if merged:
                        content = merged
                        desc = f"{desc} (merged)"
                        # 不同文件名 → 删旧文件防重复
                        if existing["filename"] != filename + ".md":
                            await mm.remove(existing["filename"])

                timestamp = await mm.save(filename, f"[{memory_type}] {desc}", content)
                is_new = "新增" if "<!-- updated:" not in (await mm.get_entry(filename) or "") else "更新"
                yield ToolResult(
                    type="result",
                    data=f"Memory {is_new}: [{memory_type}] {desc} ({timestamp})",
                    result_for_assistant=(
                        f"✅ {is_new}记忆 [{memory_type}] → {filename}.md: {desc}\n"
                        f"时间戳: {timestamp}\n"
                        f"下次对话自动注入，同主题以最新时间戳为准。"
                    ),
                )
                return

            if action == "remove":
                old = input_args.get("old_text", "")
                filename = input_args.get("filename", "")
                if not old and not filename:
                    yield ToolResult(type="result", data="Error: old_text or filename required")
                    return

                if filename:
                    await mm.remove(filename)
                    yield ToolResult(type="result", data=f"Memory removed: {filename}")
                else:
                    # 通过匹配文本找到文件名
                    found = None
                    for m in mm.list_memories():
                        if old in m:
                            fname = _re.search(r'\(([^)]+\.md)\)', m)
                            if fname:
                                found = fname.group(1)
                                break
                    if found:
                        await mm.remove(found)
                        yield ToolResult(type="result", data=f"Memory removed: {found}")
                    else:
                        yield ToolResult(type="result", data=f"No match for: {old}")
                return

            if action == "replace":
                old_text = input_args.get("old_text", "")
                filename = input_args.get("filename", "")
                content = input_args.get("content", "")
                desc = input_args.get("description", "updated memory")

                if not content:
                    yield ToolResult(type="result", data="Error: content required")
                    return

                # 找到要替换的文件名
                target_file = filename
                if not target_file and old_text:
                    for m in mm.list_memories():
                        if old_text in m:
                            fname = _re.search(r'\(([^)]+\.md)\)', m)
                            if fname:
                                target_file = fname.group(1).replace(".md", "")
                                break

                if not target_file:
                    yield ToolResult(
                        type="result",
                        data="No matching memory found. Use 'add' to create new, or check old_text/filename.",
                    )
                    return

                # 用 save 更新（自动加时间戳 + 保留旧版本）
                timestamp = await mm.save(target_file, f"[{memory_type}] {desc}", content)
                yield ToolResult(
                    type="result",
                    data=f"Memory evolved: {target_file} ({timestamp})",
                    result_for_assistant=(
                        f"✅ 记忆进化完成 → {target_file}.md\n"
                        f"时间戳: {timestamp}\n"
                        f"旧版本保留在文件底部（<!-- previous version -->），最新版本优先。"
                    ),
                )
                return

        except Exception as e:
            logger.error(f"Memory tool error: {e}")
            yield ToolResult(type="result", data=f"Error: {e}")


# ── 记忆合并辅助 ─────────────────────────────────────────────

MERGE_PROMPT = """You are merging two memories on the same topic. Produce a single, concise version that keeps all unique info from both.

## Old memory
{old_content}

## New memory
{new_content}

## Rules
- Keep everything unique from both
- Remove duplicate info
- Keep the most recent version when they conflict
- Output only the merged content, no explanation."""


async def _find_similar_memory(mm, new_desc: str, new_filename: str) -> dict | None:
    """Find an existing memory with similar topic (not just filename match)."""
    try:
        entries = mm._parse_index()
        new_keywords = set(new_desc.lower().split())
        new_stem = new_filename.replace(".md", "").lower()

        for e in entries:
            fname = e.get("filename", "").replace(".md", "").lower()
            desc = e.get("description", "").lower()

            # Same filename → exact match
            if fname == new_stem:
                content = await mm.get_entry(e["filename"])
                if content:
                    return {
                        "filename": e["filename"],
                        "content": content.split("<!-- previous version -->")[0].strip(),
                        "description": e["description"],
                    }

            # Same topic: ≥2 keyword overlap in description
            desc_kw = set(desc.split())
            overlap = new_keywords & desc_kw
            if len(overlap) >= 2:
                content = await mm.get_entry(e["filename"])
                if content:
                    return {
                        "filename": e["filename"],
                        "content": content.split("<!-- previous version -->")[0].strip(),
                        "description": e["description"],
                    }

    except Exception:
        pass
    return None


async def _merge_memories(context, old: dict, new_desc: str, new_content: str) -> str | None:
    """Use LLM to merge old and new memory content."""
    if not context or not hasattr(context, "llm"):
        return None
    try:
        prompt = MERGE_PROMPT.format(
            old_content=old["content"][:800],
            new_content=new_content[:800],
        )
        resp = await context.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        merged = resp.get("content", "").strip()
        return merged if len(merged) > 20 else None
    except Exception:
        return None
