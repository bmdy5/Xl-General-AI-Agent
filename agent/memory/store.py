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

def get_kb_dir() -> str:
    """动态获取 KB_DIR，避免模块载入时的循环导入"""
    from agent.core.config import settings
    kb_cfg = settings.get("knowledge_base") or {}
    kb_dir_cfg = kb_cfg.get("kb_dir")
    if kb_dir_cfg:
        return os.path.expanduser(kb_dir_cfg)
    return os.getenv("MYAGENT_KB_DIR", str(Path.home() / "Documents" / "个人博客" / "学习笔记" / "agent自主学习的东西"))

def get_knowledge_index() -> Path:
    return Path(get_kb_dir()) / "知识索引.md"

def get_default_routing_rules() -> str:
    from agent.core.config import settings
    kb_cfg = settings.get("knowledge_base") or {}
    notes_paths = kb_cfg.get("notes_paths") or []
    if notes_paths:
        notes_root = os.path.expanduser(notes_paths[0])
    else:
        notes_root = str(Path.home() / "Desktop" / "学习笔记")
    
    return f"""# 知识路由规则

学习笔记根目录: {notes_root}

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
        knowledge_index = get_knowledge_index()
        # 防空自愈：如果 KB_DIR 所在目录不存在，直接静默跳过更新，避免创建垃圾文件夹
        if not knowledge_index.parent.exists():
            logger.debug(f"ℹ️ [自愈] 知识库目录不存在，跳过更新索引: {knowledge_index.parent}")
            return
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        marker = f"<!-- AUTO: {section} -->"
        line = f"| {now} | {entry} |"
        
        # 自动创建索引文件
        if not knowledge_index.exists():
            knowledge_index.write_text("# 知识索引\n\n<!-- AUTO: knowledge -->\n\n<!-- AUTO: memory -->\n", encoding="utf-8")
            
        content = knowledge_index.read_text(encoding="utf-8")
        if marker in content:
            content = content.replace(marker, f"{marker}\n{line}")
            knowledge_index.write_text(content, encoding="utf-8")
    except Exception as err:
        logger.warning(f"Failed to update knowledge index: {err}")

def _match_core_file(description: str, content: str) -> Optional[str]:
    """根据描述和内容关键词匹配核心文件. 返回文件名或 None."""
    text = f"{description} {content[:200]}"
    for fname, keywords in CORE_FILES.items():
        score = sum(1 for kw in keywords if kw in text)
        if score >= 2:
            return fname
    return None

async def append_to_core(manager, target_file: str, description: str, content: str) -> str:
    """追加到核心文件 (重构为纯 SQLite 向量库)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ki_id = f"core_{hashlib.md5(target_file.encode()).hexdigest()[:12]}_{hashlib.md5(content.strip().encode('utf-8')).hexdigest()[:12]}"
    
    ki_data = {
        "id": ki_id,
        "title": target_file.replace('.md', ''),
        "category": "core",
        "keywords": [target_file.replace('.md', '')],
        "summary": description[:200],
        "content": content,
        "ki_type": "micro",
    }
    manager.save_ki(ki_data)
    
    manager._mem_cache.invalidate_keys(keywords=description, text=content)
    manager._note_cache.invalidate_keys(keywords=description, text=content)
    return timestamp

async def save(manager, filename: str, description: str, content: str,
               note_path: Optional[str] = None, ki_type: str = "ki") -> str:
    """保存记忆 (重构为纯 SQLite 向量库)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
    ki_id = f"mem_{hashlib.md5(safe_name.encode()).hexdigest()[:12]}_{hashlib.md5(content.strip().encode('utf-8')).hexdigest()[:12]}"
    mtype = description.split("]")[0].replace("[", "") if "[" in description else "other"
    
    if note_path:
        content = f"→ 笔记位置: {note_path}\n\n" + content

    ki_data = {
        "id": ki_id,
        "title": description[:80] if description else safe_name,
        "category": mtype,
        "keywords": [safe_name.replace('.md', '')],
        "summary": description[:200],
        "content": content,
        "ki_type": ki_type,
    }
    manager.save_ki(ki_data)
    
    manager._mem_cache.invalidate_keys(keywords=description, text=content)
    manager._note_cache.invalidate_keys(keywords=description, text=content)
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
    manager._mem_cache.invalidate_keys(keywords=safe_name.replace(".md", "").split("_"))
    manager._note_cache.invalidate_keys(keywords=safe_name.replace(".md", "").split("_"))

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
    return get_default_routing_rules()

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
        manager._mem_cache.invalidate_keys(keywords=filename, text=content)
        manager._note_cache.invalidate_keys(keywords=filename, text=content)
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
    """散落临时碎片小记忆物理蒸馏与核心归档 GC 回收引擎 (无损合并并物理销毁)"""
    if not manager.base_dir.exists():
        return 0

    fragments = []
    # 核心主脑文件和系统级管理文件排除列表
    core_files_set = {k.lower() for k in CORE_FILES.keys()}
    excluded_set = core_files_set.union({"memory.md", "routing_rules.md", "skill.md"})
    
    for p in manager.base_dir.iterdir():
        if p.is_file() and p.suffix == ".md":
            name = p.name.lower()
            if (name.startswith("reflect_") or name.startswith("audit_")) and name not in excluded_set and not name.startswith("."):
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
