import os
import re
import logging
import operator
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("skills.manager")

def get_skills_root() -> Path:
    """自适应获取项目的 skills 物理根目录"""
    return Path(__file__).resolve().parents[2] / "skills"

def create_skill(name: str, trigger: str, steps: list) -> Path:
    """自适应创建或更新一个自进化技能"""
    try:
        skills_root = get_skills_root()
        safe_name = re.sub(r'[^\w一-鿿-]', '_', name)[:40]
        skill_dir = skills_root / safe_name.lower()
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            content = (
                f"---\nname: {name}\ndescription: 会话自动检测的重复模式\n"
                f"trigger: {trigger}\ncreated: {now}\nversion: 1.0\n"
                f"usage_count: 0\nsuccess_count: 0\n---\n\n"
                f"# {name}\n\n## 触发\n{trigger}\n\n## 步骤\n"
                + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            )
            skill_path.write_text(content, encoding="utf-8")
            logger.info(f"Auto-skill created via SkillsManager: {name} at {skill_path}")
        return skill_path
    except Exception as e:
        logger.error(f"Failed to create skill in SkillsManager: {e}")
        return Path()

def register_skill_evolution(folder_name: str, md_content: str, script_name: str = None, script_code: str = None) -> Path:
    """自进化大模型突变注册技能"""
    try:
        skills_root = get_skills_root()
        clean_folder = re.sub(r'[^\w-]', '_', folder_name.strip().lower())
        skill_dir = skills_root / clean_folder
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_path.write_text(md_content, encoding="utf-8")
        
        if script_name and script_code:
            clean_script = re.sub(r'[^\w.-]', '_', script_name)
            script_path = skill_dir / clean_script
            script_path.write_text(script_code, encoding="utf-8")
            logger.info(f"Helper script {clean_script} created at {script_path}")
            
        return skill_md_path
    except Exception as e:
        logger.error(f"Failed to register evolved skill: {e}")
        return Path()

def track_skill_usage(skill_path_str: str, success: bool = True):
    """记录和更新指定技能的使用频率、成功率和突变小版本版本号"""
    try:
        skill_path = Path(skill_path_str)
        if not skill_path.exists():
            return
        
        content = skill_path.read_text(encoding="utf-8")
        
        usage_match = re.search(r'usage_count:\s*(\d+)', content)
        success_match = re.search(r'success_count:\s*(\d+)', content)
        
        usage = (int(usage_match.group(1)) if usage_match else 0) + 1
        success_count = (int(success_match.group(1)) if success_match else 0) + (1 if success else 0)
        
        if 'usage_count:' in content:
            content = re.sub(r'usage_count:\s*\d+', f'usage_count: {usage}', content)
        else:
            content = content.replace('---\n', f'---\nusage_count: {usage}\n', 1)
            
        if 'success_count:' in content:
            content = re.sub(r'success_count:\s*\d+', f'success_count: {success_count}', content)
        else:
            content = content.replace('usage_count:', f'usage_count: {usage}\nsuccess_count: {success_count}', 1)
            
        ver_match = re.search(r'version:\s*([\d.]+)', content)
        if ver_match:
            old_ver = float(ver_match.group(1))
            new_ver = old_ver + 0.1
            content = re.sub(r'version:\s*[\d.]+', f'version: {new_ver:.1f}', content)
        else:
            content = content.replace('---\n', '---\nversion: 1.0\n', 1)
            
        skill_path.write_text(content, encoding="utf-8")
        logger.info(f"Successfully tracked skill usage for {skill_path.name}")
    except Exception as e:
        logger.error(f"Failed to track skill usage: {e}")
