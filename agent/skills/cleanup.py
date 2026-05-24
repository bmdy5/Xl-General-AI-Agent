import os
import re
import json
import shutil
import logging
from pathlib import Path
from agent.skills.manager import get_skills_root

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
