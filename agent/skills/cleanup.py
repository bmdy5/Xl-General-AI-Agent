import os
import re
import json
import shutil
import logging
from pathlib import Path
from agent.skills.manager import get_skills_root, _parse_skill_yaml, _rule_categorize

logger = logging.getLogger("skills.cleanup")

DISTILL_PROMPT = """你是一个高阶智能体技能蒸馏引擎。现在物理磁盘中积攒了 {count} 个严重的冗余技能目录。它们全部在做重复的“暗号校验”、“状态巡检”等事情。
请帮我将这些凌乱的旧技能精炼、蒸馏，合成为最核心的 3 个高纯度技能：

1. 技能一：【安全验证与暗号核验】(目录名: identity_verification_lock, 分类: verification)
   - 核心任务：整合所有暗号确认、唤醒验证、强制唤醒、亮哥暗号核验的步骤与逻辑。
2. 技能二：【系统状态巡检与防冲突】(目录名: system_status_check, 分类: system_status)
   - 核心任务：整合所有系统状态检查、日志归集巡检、QQ网关防双进程并发冲突的巡检步骤与自愈保障逻辑。
3. 技能三：【亮哥特制暗号回答】(目录名: liang_custom_response, 分类: personal_assistant)
   - 核心任务：整合亮哥专门交代的特制好玩暗号（例如亮哥专属的特定趣味回答或行为）。

下面是磁盘中扫描到的所有旧技能信息（包括名称、触发词和内容）：
{skills_data}

请全局优化整合它们，剔除一切重复的表述，把最优的步骤提炼为上述 3 个高纯度技能文档。

只输出 JSON 格式，必须且只能为以下格式：
{{
  "identity_verification_lock": {{
    "trigger": "合并后的触发词",
    "md_content": "合并重写后的完整 SKILL.md 文档（包含 name: 安全验证与暗号核验, description, trigger, created, version: 1.0, category: verification 等 frontmatter 标签，以及步骤 markdown）"
  }},
  "system_status_check": {{
    "trigger": "合并后的触发词",
    "md_content": "合并重写后的完整 SKILL.md 文档（包含 name: 系统状态巡检与防冲突, description, trigger, created, version: 1.0, category: system_status 等 frontmatter 标签，以及步骤 markdown）"
  }},
  "liang_custom_response": {{
    "trigger": "合并后的触发词",
    "md_content": "合并重写后的完整 SKILL.md 文档（包含 name: 亮哥特制暗号回答, description, trigger, created, version: 1.0, category: personal_assistant 等 frontmatter 标签，以及步骤 markdown）"
  }}
}}
不要输出任何其他内容。"""

async def distill_legacy_skills(agent) -> bool:
    """对已有的冗余物理技能目录进行一次性聚类合并与高纯度蒸馏"""
    skills_root = get_skills_root()
    if not skills_root.exists():
        logger.warning("Skills root directory does not exist.")
        return False

    # 1. 扫描所有 legacy 技能
    legacy_skills = []
    for item in skills_root.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    legacy_skills.append({
                        "folder": item.name,
                        "content": content
                    })
                except Exception as e:
                    logger.error(f"Failed to read skill {item.name}: {e}")

    if not legacy_skills:
        logger.info("No legacy skills found to cleanup.")
        return True

    logger.info(f"Scanning found {len(legacy_skills)} legacy skills. Starting LLM distillation...")

    # 2. 构造数据
    skills_data_str = ""
    for idx, sk in enumerate(legacy_skills):
        skills_data_str += f"\n--- [技能 #{idx+1} (目录: {sk['folder']})] ---\n{sk['content']}\n"

    # 3. 提交给 LLM
    try:
        prompt = DISTILL_PROMPT.format(count=len(legacy_skills), skills_data=skills_data_str)
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.error("LLM failed to return a valid JSON response for distillation.")
            return False

        distilled_data = json.loads(json_match.group(0))
        required_keys = ["identity_verification_lock", "system_status_check", "liang_custom_response"]
        if not all(k in distilled_data for k in required_keys):
            logger.error("LLM response is missing required core keys.")
            return False

        # 4. 物理重组：创建新技能，彻底清除老技能
        temp_skills = {}
        for key in required_keys:
            skill_info = distilled_data[key]
            temp_skills[key] = skill_info["md_content"]

        # 清理原目录下所有文件夹
        for item in skills_root.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                try:
                    shutil.rmtree(item)
                except Exception as e:
                    logger.error(f"Failed to delete legacy dir {item.name}: {e}")

        # 重新写入蒸馏后的 3 个核心技能
        for folder_name, md_content in temp_skills.items():
            folder_path = skills_root / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)
            skill_file = folder_path / "SKILL.md"
            skill_file.write_text(md_content, encoding="utf-8")
            logger.info(f"Core distilled skill saved: {folder_name}")

        logger.info("Successfully completed physical legacy skills distillation!")
        return True

    except Exception as e:
        logger.error(f"Failed to distill legacy skills: {e}")
        return False


def _safe_int(val, default=0) -> int:
    try:
        if val is None:
            return default
        match = re.search(r'\d+', str(val))
        if match:
            return int(match.group(0))
        return int(float(val))
    except Exception:
        return default


def _safe_float(val, default=1.0) -> float:
    try:
        if val is None:
            return default
        match = re.search(r'\d+(\.\d+)?', str(val))
        if match:
            return float(match.group(0))
        return float(val)
    except Exception:
        return default


async def incremental_cleanup_skills(agent) -> bool:
    """物理技能增量聚类查重与自演进无损合并合并引擎"""
    skills_root = get_skills_root()
    if not skills_root.exists():
        logger.warning("Skills root directory does not exist.")
        return False

    # 1. 扫描所有技能目录并解析
    skills_list = []
    for item in skills_root.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            skill_md = item / "SKILL.md"
            if skill_md.exists():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    meta = _parse_skill_yaml(content)
                    category = meta.get("category", "")
                    if not category:
                        # 兜底分类
                        from agent.skills.manager import _rule_categorize
                        category = _rule_categorize(meta.get("name", item.name), meta.get("trigger", ""))
                    skills_list.append({
                        "folder": item.name,
                        "name": meta.get("name", item.name),
                        "trigger": meta.get("trigger", ""),
                        "category": category,
                        "content": content,
                        "path": skill_md,
                        "usage_count": _safe_int(meta.get("usage_count", 0)),
                        "success_count": _safe_int(meta.get("success_count", 0)),
                        "version": _safe_float(meta.get("version", 1.0)),
                    })
                except Exception as e:
                    logger.error(f"Failed to read skill {item.name}: {e}")

    if len(skills_list) < 2:
        logger.info("Fewer than 2 skills exist. No deduplication needed.")
        return True

    # 2. 按 category 聚类分组
    grouped_skills = {}
    for sk in skills_list:
        cat = sk["category"]
        grouped_skills.setdefault(cat, []).append(sk)

    merged_folders = set()
    has_changes = False

    # 3. 在每个类别内部，进行两两比对查重
    for cat, sks in grouped_skills.items():
        if len(sks) < 2:
            continue

        # 两两比对
        for i in range(len(sks)):
            sk_a = sks[i]
            if sk_a["folder"] in merged_folders:
                continue

            for j in range(i + 1, len(sks)):
                sk_b = sks[j]
                if sk_b["folder"] in merged_folders:
                    continue

                # 大模型进行两两比对
                prompt = f"""你是一个高阶智能体技能归类查重引擎。现在需要对同大类 {cat} 下的两个已存在的物理技能进行查重比对：

技能 A [目录: {sk_a['folder']}]:
名称: {sk_a['name']}
触发词: {sk_a['trigger']}
步骤:
{sk_a['content']}

技能 B [目录: {sk_b['folder']}]:
名称: {sk_b['name']}
触发词: {sk_b['trigger']}
步骤:
{sk_b['content']}

请判断：
这两个技能在语义和具体操作场景上是否高度重复或属于同一个技能的不同演化版本？
例如，它们都在实现“暗号校验与唤醒流程”，或是“系统网关的并发检查”。
只输出 JSON 格式，必须且只能为以下格式：
{{
  "is_similar": true/false,
  "reason": "简述判断依据"
}}
不要输出任何其他解释。"""

                try:
                    response = await agent.llm.chat(
                        messages=[{"role": "user", "content": prompt}],
                        tools=None,
                        model_override=agent.llm.model,
                    )
                    text = response.get("content", "").strip()
                    json_match = re.search(r'\{[\s\S]*\}', text)
                    if json_match:
                        data = json.loads(json_match.group(0))
                        if data.get("is_similar"):
                            logger.info(f"LLM determined skill B ({sk_b['folder']}) is duplicate of skill A ({sk_a['folder']})")
                            
                            # 执行合并：将 B 合并进 A
                            from agent.skills.manager import _llm_merge_skills
                            steps_text = re.findall(r'\d+\.\s*(.*)', sk_b["content"])
                            
                            # 调用 merge
                            merged_content = await _llm_merge_skills(agent, sk_a["content"], sk_b["trigger"], steps_text)
                            
                            # 更新 A 的使用统计
                            total_usage = sk_a["usage_count"] + sk_b["usage_count"]
                            total_success = sk_a["success_count"] + sk_b["success_count"]
                            new_version = max(sk_a["version"], sk_b["version"]) + 0.1
                            
                            # 更新 merged_content 中的 metadata
                            if "usage_count:" in merged_content:
                                merged_content = re.sub(r'usage_count:\s*\d+', f'usage_count: {total_usage}', merged_content)
                            if "success_count:" in merged_content:
                                merged_content = re.sub(r'success_count:\s*\d+', f'success_count: {total_success}', merged_content)
                            if "version:" in merged_content:
                                merged_content = re.sub(r'version:\s*[\d.]+', f'version: {new_version:.1f}', merged_content)
                            
                            # 覆盖写入 A
                            sk_a["path"].write_text(merged_content, encoding="utf-8")
                            sk_a["content"] = merged_content
                            sk_a["usage_count"] = total_usage
                            sk_a["success_count"] = total_success
                            sk_a["version"] = new_version
                            
                            # 物理销毁 B 文件夹
                            b_dir = skills_root / sk_b["folder"]
                            if b_dir.exists():
                                shutil.rmtree(b_dir)
                                logger.info(f"Successfully destroyed redundant skill folder: {sk_b['folder']}")
                                
                            merged_folders.add(sk_b["folder"])
                            has_changes = True
                except Exception as ex:
                    logger.error(f"Error comparing skills {sk_a['folder']} and {sk_b['folder']}: {ex}")

    if has_changes:
        logger.info("Skills incremental cleanup finished and successfully merged duplicates.")
    else:
        logger.info("Skills incremental cleanup finished. No duplicates found to merge.")
    return True


async def run_incremental_cleanup(agent):
    """异步安全运行物理增量去重自演进"""
    try:
        if not agent or not hasattr(agent, "llm"):
            from agent.core.bootstrap import build_agent
            agent = build_agent()
        await incremental_cleanup_skills(agent)
    except Exception as e:
        logger.error(f"Error running incremental cleanup daemon: {e}")


if __name__ == "__main__":
    import sys
    import asyncio
    
    if len(sys.argv) > 1 and sys.argv[1] == "--incremental":
        # 命令行执行
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        from agent.core.bootstrap import build_agent
        agent = build_agent()
        print("🔄 正在以命令行模式跑物理增量查重合并...")
        asyncio.run(incremental_cleanup_skills(agent))
        print("✅ 一键物理技能增量去重完成")
