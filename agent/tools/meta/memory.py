"""自动记忆工具 — 组合 CC + hermes + openclaw 三家设计.

CC 贡献：5类记忆分类 + 禁止清单
hermes 贡献：action 参数模式 (add/replace/remove/read)
openclaw 贡献：强制召回步骤（Mandatory recall step）
"""

import logging
from typing import Any, AsyncGenerator, Optional

from ..base_tool import BaseTool, ToolResult

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
        if action == "remove":
            return True  # 物理删除记忆需要用户审批阻断
        return False  # 其他记忆操作（保存、更新、检索、合并）全部自动放行，0 弹窗

    def get_tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "管理长期记忆和规范（这是保存规则、知识和业务流程的【唯一】合法途径）。\n\n"
                    "【最高红线】：绝对禁止使用 `edit_file` 去修改任何历史遗留的 .md 记忆文件！所有的长期认知必须通过本工具存入底层的 KI（Knowledge Item）数据库中。\n\n"
                    "审批说明：只有 'remove' (删除) 操作需要亮哥审批，其余操作 ('add', 'replace', 'search', 'merge_to_core') 都是自动放行的，请大胆使用。\n\n"
                    "记忆分类 (memory_type)：user(用户偏好) / feedback(纠正反馈) / project(项目经验) / reference(参考资料) / learn(学习笔记)。\n\n"
                    "【路由与存储规范】（严格执行）：\n"
                    "1. 核心规则：仅当保存【必须全局遵守的行为准则、做事流程、红线纠正】时，必须设置 ki_type='ki'。\n"
                    "2. 零碎事实：当保存单点踩坑记录、零碎习惯、参数配置等，必须设置 ki_type='micro'（后台会自动将其聚类提炼）。\n"
                    "3. 学习笔记：设置 memory_type='learn'，并指定 note_dir。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["add", "replace", "remove", "read", "search", "merge_to_core"],
                            "description": "add=新增, replace=覆盖更新, remove=删除, read=读取列表, merge_to_core=追加到核心",
                        },
                        "memory_type": {
                            "type": "string",
                            "enum": MEMORY_TYPES,
                            "description": "记忆类型：user/feedback 属于核心记忆；learn/project 属于知识（建议结合 note_dir 使用）",
                        },
                        "ki_type": {
                            "type": "string",
                            "enum": ["ki", "micro", "fragment"],
                            "description": "存储类型：ki=全局强约束核心规则，micro=单点/零散的踩坑事实与经验（首选）。",
                        },
                        "filename": {
                            "type": "string",
                            "description": "记录的标识名（例如 'RAG检索优化'）。仅限 'add' 动作时使用。",
                        },
                        "description": {
                            "type": "string",
                            "description": "一句简短的摘要说明。仅限 'add' 动作时使用。",
                        },
                        "content": {"type": "string", "description": "记忆的具体内容。'add' 时为全文，'replace' 时为新的替换文本。"},
                        "note_dir": {
                            "type": "string",
                            "description": "学习笔记存放的子目录名称。请通过读取 routing_rules.md 了解应该填什么路径。如果是核心记忆，请留空。",
                        },
                        "query": {"type": "string", "description": "搜索关键词（仅限 action=search 使用）。"},
                        "target_file": {
                            "type": "string",
                            "description": "合并后内容的标签或文件名。",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "用于 replace/remove 时匹配旧文本。支持子字符串匹配。",
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
                content = input_args.get("content", "")
                desc = input_args.get("description", "merged reflect")
                if not content:
                    yield ToolResult(type="result", data="Error: content required")
                    return
                import hashlib
                ki_id = f"core_{hashlib.md5(content.encode()).hexdigest()[:12]}"
                ki_data = {
                    "id": ki_id, "title": desc[:80], "category": "operation_rules",
                    "keywords": ["core_rule", "亮哥指令"], "summary": desc[:200],
                    "content": content, "ki_type": "micro",
                }
                mm.save_ki(ki_data)
                try:
                    await mm.save_ki_embedding(ki_id, desc + " " + content[:500])
                except Exception:
                    pass
                target = input_args.get("target_file", "核心大脑")
                yield ToolResult(
                    type="result",
                    data=f"Merged to {target}",
                    result_for_assistant=f"✅ 已合并到核心文件 {target}",
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

                # 核心记忆：存本地向量库
                ki_type = input_args.get("ki_type", "micro")
                timestamp = await mm.save(filename, f"[{memory_type}] {desc}", content, ki_type=ki_type)
                is_new = "新增" if "<!-- updated:" not in (await mm.get_entry(filename) or "") else "更新"
                yield ToolResult(
                    type="result",
                    data=f"Memory {is_new}: [{memory_type}] {desc} ({timestamp})",
                    result_for_assistant=(
                        f"✅ {is_new}记忆 [{memory_type}] → 已存入 KI 向量库 (ID: {filename}, ki_type: {ki_type})\n"
                        f"描述: {desc}\n"
                        f"时间戳: {timestamp}\n"
                        f"下次对话自动注入向量引擎。"
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
                        f"✅ 记忆进化完成 → 已在 KI 向量库中更新 (ID: {target_file})\n"
                        f"时间戳: {timestamp}\n"
                        f"旧版本会在数据库 revision_history 沉淀，新版本自动生效。"
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
