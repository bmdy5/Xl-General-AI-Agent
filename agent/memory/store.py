import os
import re
import sqlite3
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("agent.memory.store")

PROTECTED_FILES = {
    "persona_profile.json",
}

CORE_FILES = {
    "user_profile.md":           ["用户", "偏好", "亮哥", "称呼", "模型配置", "情绪", "表达偏好", "个人", "profile"],
    "communication_rules.md":    ["沟通", "格式", "开场", "消息", "回复", "星号", "markdown", "纯文本", "拆分", "对话"],
    "operation_rules.md":        ["操作", "代码纪律", "搜索验证", "执行纪律", "费用", "工作流程", "workaround", "根因"],
    "xl_tool_guide.md":          ["bash", "read_file", "write_file", "edit_file", "read_image",
                                   "image2", "web_fetch", "web_search", "save_memory",
                                   "schedule_task", "避坑", "成本", "工具", "命令", "超时", "审计"],
    "xl_architecture.md":        ["架构", "系统设计", "模块", "组件", "缓存", "DeepSeek", "FTS5", "索引"],
    "xl_code_review.md":         ["代码审查", "review", "bug", "代码质量", "代码"],
    "xl_identity.md":            ["身份", "人格", "小萤", "自我认知", "agent定义"],
    "xl_debugging.md":           ["调试", "debug", "排查", "日志", "traceback", "错误", "报错"],
    "xl_requirement_analysis.md": ["需求分析", "方案设计", "需求理解", "需求"],
}

KB_DIR = os.getenv("MYAGENT_KB_DIR", "/Users/xiaofeng/Documents/个人博客/学习笔记/agent自主学习的东西")
KNOWLEDGE_INDEX = Path(KB_DIR) / "知识索引.md"

DEFAULT_ROUTING_RULES = """# 知识路由规则

学习笔记根目录: /Users/xiaofeng/Desktop/学习笔记

## 路由判断
1. 跟小萤自身相关（人格、行为、成长、自学习）→ 01-小萤/自学习笔记/
2. Agent 通用技术（工具、记忆、多智能体、循环）→ 02-Agent技术/记忆系统/
3. 具体项目经验、踩坑记录、bug修复 → 06-工作记录/工程实践/
4. 用户知识（亮哥教给我的）→ 01-小萤/自学习笔记/

## 记忆类型路由
- feedback/behavior → core memory (不写笔记)
- learn/technical → 知识索引 → 上述笔记目录
- project/experience → 06-工作记录/工程实践/
- personal/identity → core memory (不写笔记)
"""

def update_knowledge_index(section: str, entry: str):
    """Append to knowledge index (non-blocking)."""
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        marker = f"<!-- AUTO: {section} -->"
        line = f"| {now} | {entry} |"
        content = KNOWLEDGE_INDEX.read_text(encoding="utf-8")
        if marker in content:
            content = content.replace(marker, f"{marker}\n{line}")
            KNOWLEDGE_INDEX.write_text(content, encoding="utf-8")
    except Exception:
        pass

def _match_core_file(description: str, content: str) -> Optional[str]:
    """根据描述和内容关键词匹配核心文件. 返回文件名或 None."""
    text = f"{description} {content[:200]}"
    for fname, keywords in CORE_FILES.items():
        score = sum(1 for kw in keywords if kw in text)
        if score >= 2:
            return fname
    return None

async def append_to_core(manager, target_file: str, description: str, content: str) -> str:
    """追加到核心文件."""
    if target_file in PROTECTED_FILES:
        logger.warning(f"append_to_core blocked: {target_file} is protected")
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filepath = manager.base_dir / target_file

    if not filepath.exists():
        filepath.write_text(
            f"# {target_file.replace('.md','').replace('_',' ').title()}\n\n",
            encoding="utf-8"
        )

    existing = filepath.read_text(encoding="utf-8")

    content_hash = hashlib.md5(content.strip().encode('utf-8')).hexdigest()
    hash_tag = f"<!-- hash:{content_hash} -->"
    if hash_tag in existing:
        return timestamp

    append_entry = (
        f"\n\n---\n"
        f"<!-- {timestamp} -->\n"
        f"{hash_tag}\n"
        f"### {description}\n"
        f"{content}\n"
    )
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(append_entry)

    manager._upsert_index(target_file, f"- [{description}]({target_file}) `{timestamp}`")

    try:
        db = manager._get_db()
        from .fts_index import populate as fts_populate
        fts_populate(db, [{
            "content": content[:5000],
            "description": description[:200],
            "memory_type": "merged",
            "filename": target_file,
            "timestamp": timestamp,
        }])
        db.commit()
    except Exception:
        pass

    manager._mem_cache.invalidate_all()
    manager._note_cache.invalidate_all()
    return timestamp

async def save(manager, filename: str, description: str, content: str,
               note_path: Optional[str] = None) -> str:
    """保存记忆."""
    if filename in PROTECTED_FILES or Path(filename).name in PROTECTED_FILES:
        logger.warning(f"save blocked: {filename} is protected")
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    core_file = _match_core_file(description, content)
    
    if not core_file and not note_path:
        name_lower = filename.lower()
        desc_lower = description.lower()
        if name_lower.startswith("reflect_") or name_lower.startswith("audit_"):
            if "user" in name_lower or "feedback" in name_lower or "user" in desc_lower or "feedback" in desc_lower:
                core_file = "user_profile.md"
            elif "tool" in name_lower or "audit" in name_lower or "tool" in desc_lower or "audit" in desc_lower:
                core_file = "xl_tool_guide.md"
            elif "project" in name_lower or "project" in desc_lower:
                core_file = "xl_code_review.md"
            else:
                core_file = "xl_debugging.md"

    if core_file and not note_path:
        return await manager.append_to_core(core_file, description, content)

    safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    topic_file = manager.base_dir / safe_name
    is_update = topic_file.exists()

    if note_path:
        index_content = (
            f"<!-- pointer -->\n"
            f"# {description}\n\n"
            f"→ 笔记位置: {note_path}\n\n"
            f"_内容存储在{note_path}，这里只做索引。_"
        )
        topic_file.write_text(index_content, encoding="utf-8")
        index_line = f"- [{description}]({note_path}) `{timestamp}`"
        mtype = "knowledge"
        update_knowledge_index("knowledge", f"{mtype} | {description} | {note_path}")
    else:
        if is_update:
            old_content = topic_file.read_text(encoding="utf-8")
            content = (
                f"<!-- updated: {timestamp} -->\n{content}\n\n"
                f"---\n<!-- previous version -->\n{old_content[:500]}"
            )
        topic_file.write_text(content, encoding="utf-8")
        index_line = f"- [{description}]({safe_name}) `{timestamp}`"
        mtype = description.split("]")[0].replace("[", "") if "[" in description else "other"
        update_knowledge_index("memory", f"{mtype} | {description} | {safe_name}")

    manager._upsert_index(safe_name, index_line)

    try:
        db = manager._get_db()
        from .fts_index import populate as fts_populate
        fts_populate(db, [{
            "content": content[:5000],
            "description": description[:200],
            "memory_type": mtype,
            "filename": safe_name,
            "timestamp": timestamp,
        }])
        db.commit()
    except Exception:
        pass
    manager._mem_cache.invalidate_all()
    manager._note_cache.invalidate_all()
    return timestamp

async def remove(manager, filename: str):
    """删除记忆文件."""
    safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    topic_file = manager.base_dir / safe_name
    if topic_file.exists():
        topic_file.unlink()
    if manager.index_file.exists():
        lines = manager.index_file.read_text(encoding="utf-8").split("\n")
        new_lines = [l for l in lines if safe_name not in l]
        manager.index_file.write_text("\n".join(new_lines), encoding="utf-8")
    try:
        db = manager._get_db()
        db.execute("DELETE FROM memories_fts WHERE filename=?", (safe_name,))
        db.commit()
    except Exception:
        pass
    manager._mem_cache.invalidate_all()
    manager._note_cache.invalidate_all()

async def get_entry(manager, filename: str) -> Optional[str]:
    """读取记忆文件内容."""
    if filename.startswith("ki_") and filename.endswith(".md"):
        ki_id = filename[3:-3]
        ki_data = manager.get_ki(ki_id)
        if ki_data:
            return ki_data.get("content", "")
        return None

    safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    topic_file = manager.base_dir / safe_name
    if topic_file.exists():
        return topic_file.read_text(encoding="utf-8")
    return None

def get_routing_rules(manager) -> str:
    """返回当前路由规则."""
    if manager.rules_file and manager.rules_file.exists():
        return manager.rules_file.read_text(encoding="utf-8")
    return DEFAULT_ROUTING_RULES

async def save_to_notes(manager, dir_path: str, filename: str, content: str) -> Optional[str]:
    """保存知识到学习笔记目录."""
    try:
        rules = manager.get_routing_rules()
        m = re.search(r'学习笔记根目录:\s*(.+)', rules)
        if not m:
            return None
        base = Path(m.group(1).strip())
        target_dir = base / dir_path
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = filename.replace("/", "_").replace("\\", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        filepath = target_dir / safe_name
        filepath.write_text(content, encoding="utf-8")
        manager._mem_cache.invalidate_all()
        manager._note_cache.invalidate_all()
        return str(filepath)
    except Exception as e:
        logger.warning(f"Failed to save to notes: {e}")
        return None

async def verify_index(manager) -> list[dict]:
    """检查 MEMORY.md 中的损坏笔记链接."""
    broken = []
    entries = manager._parse_index()
    rules = manager.get_routing_rules()
    m = re.search(r'学习笔记根目录:\s*(.+)', rules)
    base = Path(m.group(1).strip()) if m else Path.home() / "Desktop" / "学习笔记"

    for e in entries:
        fname = e.get("filename", "")
        if "/" in fname or (fname.startswith("0") and "-" in fname[:3]):
            full_path = base / fname
            if not full_path.exists():
                item = dict(e)
                item["expected_path"] = str(full_path)
                broken.append(item)
    return broken

async def gc_and_merge_fragmented_memories(manager) -> int:
    """碎片小记忆垃圾回收与归档合并."""
    if not manager.base_dir.exists():
        return 0

    fragments = []
    for p in manager.base_dir.iterdir():
        if p.is_file() and p.suffix == ".md":
            name = p.name.lower()
            if name.startswith("reflect_") or name.startswith("audit_"):
                fragments.append(p)

    if not fragments:
        return 0

    merged_count = 0
    db = manager._get_db()

    for path in fragments:
        filename = path.name
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue

        description = ""
        entries = manager._parse_index()
        for e in entries:
            if e.get("filename") == filename:
                description = e.get("description", "")
                break

        if not description:
            description = filename.replace(".md", "").replace("_", " ")

        name_lower = filename.lower()
        desc_lower = description.lower()
        core_file = None
        if "user" in name_lower or "feedback" in name_lower or "user" in desc_lower or "feedback" in desc_lower:
            core_file = "user_profile.md"
        elif "tool" in name_lower or "audit" in name_lower or "tool" in desc_lower or "audit" in desc_lower:
            core_file = "xl_tool_guide.md"
        elif "project" in name_lower or "project" in desc_lower:
            core_file = "xl_code_review.md"
        else:
            core_file = "xl_debugging.md"

        try:
            await manager.append_to_core(core_file, description, content)
            path.unlink()

            if manager.index_file.exists():
                idx_lines = manager.index_file.read_text(encoding="utf-8").split("\n")
                new_idx_lines = [l for l in idx_lines if filename not in l]
                manager.index_file.write_text("\n".join(new_idx_lines), encoding="utf-8")

            try:
                db.execute("DELETE FROM memories_fts WHERE filename=?", (filename,))
                db.commit()
            except Exception:
                pass

            merged_count += 1
            logger.info(f"Memory GC: Merged and removed fragment '{filename}' -> '{core_file}'")
        except Exception as e:
            logger.warning(f"Memory GC: Failed to merge '{filename}': {e}")

    if merged_count > 0:
        try:
            from .fts_index import rebuild
            rebuild(db)
        except Exception:
            pass
        manager._mem_cache.invalidate_all()
        manager._note_cache.invalidate_all()

    return merged_count
