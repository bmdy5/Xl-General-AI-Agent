"""自动记忆工具 — 组合 CC + hermes + openclaw 三家设计.

CC 贡献：5类记忆分类 + 禁止清单
hermes 贡献：action 参数模式 (add/replace/remove/read)
openclaw 贡献：强制召回步骤（Mandatory recall step）
"""

import logging
from typing import Any, AsyncGenerator, Optional

from .base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

MEMORY_TYPES = ["user", "feedback", "project", "reference", "learn"]

try:
    from agent.memory.manager import CORE_FILES
except ImportError:
    CORE_FILES = {}  # fallback for tests




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
        action = (input_args or {}).get("action", "")
        if action == "merge_to_core":
            return False  # 合并操作自动放行
        return True  # 其他记忆修改需要用户审批

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Manage persistent memories. Check before answering about preferences.\n\n"
                    "Types: user/feedback/project/reference/learn.\n\n"
                    "ROUTING: user/feedback → core memory (always keep). "
                    "learn/project/knowledge → set note_dir to archive content to learning notes. "
                    "Read routing_rules.md (via read_file) to find the right directory path, "
                    "e.g. '02-Agent技术/记忆系统' or '01-小萤/自学习笔记'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "replace", "remove", "read", "search", "merge_to_core"],
                            "description": "add=save new, replace=update, remove=delete, read=list, merge_to_core=append to core file (auto-approved)",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": MEMORY_TYPES,
                            "description": "Type: user/feedback=core memory, learn/project=knowledge (use note_dir)",
                        },
                        "filename": {
                            "type": "string",
                            "description": "File name (e.g. 'RAG检索优化'). Only for 'add' action.",
                        },
                        "description": {
                            "type": "string",
                            "description": "One-line summary for MEMORY.md index. Only for 'add' action.",
                        },
                        "content": {"type": "string", "description": "The memory content. For 'add'=full text, for 'replace'=new text."},
                        "note_dir": {
                            "type": "string",
                            "description": "Learning notes subdirectory for knowledge memories. Read routing_rules.md to find the right path. Leave empty for core memories.",
                        },
                        "query": {"type": "string", "description": "Search query for action=search."},
                        "target_file": {
                            "type": "string",
                            "description": "Target core filename for merge_to_core. One of: user_profile.md, communication_rules.md, operation_rules.md, xl_tool_guide.md, xl_architecture.md, xl_code_review.md, xl_identity.md, xl_debugging.md, xl_requirement_analysis.md",
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
        if action not in ("add", "replace", "remove", "read", "search", "merge_to_core"):
            return {"result": False, "message": f"Invalid action: {action}"}
        if action == "merge_to_core":
            if not input_args.get("target_file") or not input_args.get("content"):
                return {"result": False, "message": "target_file and content required for merge_to_core"}
        if action == "search" and not input_args.get("query"):
            return {"result": False, "message": "query required for search action"}
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

            if action == "search":
                query = input_args.get("query", "")
                results = mm.search_memories(query, limit=5)
                if not results:
                    yield ToolResult(type="result", data=f"No memories found for: {query}")
                else:
                    lines = [f"Found {len(results)} memories:"]
                    for r in results:
                        lines.append(f"- [{r.get('memory_type','?')}] {r.get('description','')[:80]}")
                    yield ToolResult(type="result", data="\n".join(lines))
                return

            if action == "merge_to_core":
                target = input_args.get("target_file", "")
                content = input_args.get("content", "")
                desc = input_args.get("description", "merged reflect")
                if not target or not content:
                    yield ToolResult(type="result", data="Error: target_file and content required")
                    return
                if target not in CORE_FILES:
                    opts = ", ".join(CORE_FILES.keys())
                    yield ToolResult(type="result", data=f"Error: {target} not in core files. Options: {opts}")
                    return
                timestamp = await mm.append_to_core(target, desc, content)
                yield ToolResult(
                    type="result",
                    data=f"Merged to {target} ({timestamp})",
                    result_for_assistant=f"已合并到核心文件 {target}，时间戳 {timestamp}",
                )
                return

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

                # ── 冲突检测 ──
                existing = await _find_similar_memory(mm, desc, filename)
                if existing:
                    merged = await _merge_memories(context, existing, desc, content)
                    if merged:
                        content = merged
                        desc = f"{desc} (merged)"
                        if existing["filename"] != filename + ".md":
                            await mm.remove(existing["filename"])

                # ── 路由：bot 指定 note_dir → 存学习笔记+指针；否则存核心记忆 ──
                note_dir = input_args.get("note_dir", "").strip()
                if note_dir:
                    note_path = await mm.save_to_notes(note_dir, filename, content)
                    if note_path:
                        await mm.save(filename, f"[{memory_type}] {desc}",
                                      f"→ 笔记位置: {note_path}", note_path=note_path)
                        yield ToolResult(
                            type="result",
                            data=f"📖 已归档到学习笔记: {note_path}",
                            result_for_assistant=(
                                f"✅ 知识已归档: {note_path}\n"
                                f"类型: {memory_type}, 描述: {desc}\n"
                                f"MEMORY.md 已建立指针索引指向学习笔记。"
                            ),
                        )
                        return
                    else:
                        # save_to_notes 失败 → 降级为核心记忆
                        logger.warning(f"save_to_notes failed for {note_dir}, falling back to core memory")

                # 核心记忆：存本地文件
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
                            import re as _re
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
                desc = input_args.get("description") or "updated memory"

                if not content:
                    yield ToolResult(type="result", data="Error: content required")
                    return

                # 找到要替换的文件名
                target_file = filename
                if not target_file and old_text:
                    import re as _re
                    for m in mm.list_memories():
                        if old_text in m:
                            fname = _re.search(r'\(([^)]+\.md)\)', m)
                            if fname:
                                target_file = fname.group(1).replace(".md", "")
                                break

                if not target_file:
                    yield ToolResult(
                        type="result",
                        data="No matching memory find. Use 'add' to create new, or check old_text/filename.",
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
        new_keywords = set((new_desc or "").lower().split())
        new_stem = (new_filename or "").replace(".md", "").lower()

        for e in entries:
            fname = (e.get("filename") or "").replace(".md", "").lower()
            desc = (e.get("description") or "").lower()

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
