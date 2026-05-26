import os
import re
import logging
import operator
import json
import asyncio
import threading
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("skills.manager")

def run_async_handler(coro):
    """鲁棒的同步执行协程包装器，完美处理运行中与非运行的 event loop"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        import queue
        q = queue.Queue()
        def _target():
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                res = new_loop.run_until_complete(coro)
                q.put((True, res))
            except Exception as ex:
                q.put((False, ex))
        t = threading.Thread(target=_target)
        t.start()
        t.join()
        success, val = q.get()
        if success:
            return val
        else:
            raise val
    else:
        return loop.run_until_complete(coro)

def get_skills_root() -> Path:
    """自适应获取项目的 skills 物理根目录"""
    return Path(__file__).resolve().parents[2] / "skills"

def _rule_categorize(name: str, trigger: str) -> str:
    """基于物理规则归类技能"""
    text = f"{name} {trigger}".lower()
    if any(k in text for k in ["暗号", "唤醒", "核验", "验证", "密码", "身份"]):
        return "verification"
    if any(k in text for k in ["巡检", "状态", "健康", "并发", "进程", "自愈", "清理"]):
        return "system_status"
    if any(k in text for k in ["特制", "亮哥特制", "问答", "搞笑", "回复"]):
        return "personal_assistant"
    return "development"

async def _llm_categorize(agent, name: str, trigger: str) -> str:
    """利用 LLM 自动归类"""
    prompt = f"""你是一个技能分类引擎。请将以下技能归类为这四个大类之一：
- verification (安全核验与暗号验证)
- system_status (日常状态检查与巡检)
- personal_assistant (亮哥专属特制趣味问答与助手)
- development (代码开发与具体工具执行步骤)

新技能信息：
名称: {name}
触发词: {trigger}

只输出分类的英文单词(verification/system_status/personal_assistant/development)，不要输出其他任何字符。"""
    try:
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        cat = response.get("content", "").strip().lower()
        if cat in ["verification", "system_status", "personal_assistant", "development"]:
            return cat
    except Exception:
        pass
    return _rule_categorize(name, trigger)

def _parse_skill_yaml(content: str) -> dict:
    """解析 SKILL.md 的 YAML Frontmatter"""
    meta = {}
    yaml_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if yaml_match:
        yaml_text = yaml_match.group(1)
        for line in yaml_text.split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                meta[k.strip()] = v.strip()
    return meta

async def _llm_find_similar_skill(agent, category: str, new_name: str, new_trigger: str, new_steps: list) -> str:
    """大模型进行同类别技能查重"""
    skills_root = get_skills_root()
    existing_skills = []
    
    if not skills_root.exists():
        return None
        
    for item in skills_root.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            sf = item / "SKILL.md"
            if sf.exists():
                try:
                    content = sf.read_text(encoding="utf-8")
                    meta = _parse_skill_yaml(content)
                    if meta.get("category", "") == category:
                        existing_skills.append({
                            "folder": item.name,
                            "name": meta.get("name", item.name),
                            "trigger": meta.get("trigger", ""),
                            "content": content
                        })
                except Exception:
                    continue

    if not existing_skills:
        return None

    list_str = ""
    for idx, sk in enumerate(existing_skills):
        list_str += f"\n- 技能 #{idx+1} [目录: {sk['folder']}]\n  名称: {sk['name']}\n  触发词: {sk['trigger']}\n"

    prompt = f"""你是一个高阶智能体技能归类查重引擎。现在同大类 {category} 下有以下已存在的技能：
{list_str}

当前试图创建或演进一个新技能：
名称: {new_name}
触发词: {new_trigger}
步骤: {new_steps}

请判断：
当前新技能是否是已有技能列表中的某一个的“语义变体”或“相似场景操作”？
如果是，请务必指出其目录名。
只输出 JSON 格式，必须且只能为：
{{
  "is_similar": true/false,
  "similar_skill_folder": "如果相似，填其对应的 [目录] 名称(如 identity_verification_lock)；否则为 null"
}}
不要输出任何其他内容。"""

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
            if data.get("is_similar") and data.get("similar_skill_folder"):
                return data.get("similar_skill_folder")
    except Exception:
        pass
    return None

async def _llm_merge_skills(agent, old_content: str, new_trigger: str, new_steps: list) -> str:
    """利用大模型进行智能融合并重写"""
    prompt = f"""你是一个高阶智能体技能进化整合引擎。现在需要将一个相似的新操作步骤（New Version）合并入已有的技能文档（Old Version）中，使之更严密、无冗余。

已有技能文档 (Old Version):
{old_content}

新操作步骤 (New Version):
触发词: {new_trigger}
步骤: {new_steps}

请通盘考虑两者的触发条件和执行步骤，完全智能整合重写出一份最高纯度、步骤逻辑清晰、无冗余的全新 SKILL.md 文本。
规范要求：
1. 必须包含完整的 YAML frontmatter（其中 category 字段必须保留旧版大类，且版本 version 需自动累加，如原 1.0 变为 1.1，usage_count 与 success_count 需合并保留）。
2. 使用简洁、优美、直观的中文步骤表述。
不要输出任何 Markdown 外包裹的解释。只输出生成的 markdown 文档本身。"""

    try:
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        merged = response.get("content", "").strip()
        if merged.startswith("```"):
            lines = merged.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].strip() == "```":
                lines = lines[:-1]
            merged = "\n".join(lines).strip()
        return merged
    except Exception:
        return old_content

def create_skill(name: str, trigger: str, steps: list, agent=None) -> Path:
    """自适应创建或智能更新一个自进化技能"""
    try:
        skills_root = get_skills_root()
        skills_root.mkdir(parents=True, exist_ok=True)
        
        # 1. 归类
        if agent:
            try:
                category = run_async_handler(_llm_categorize(agent, name, trigger))
            except Exception:
                category = _rule_categorize(name, trigger)
        else:
            category = _rule_categorize(name, trigger)
            
        # 2. 查重
        similar_folder = None
        if agent:
            try:
                similar_folder = run_async_handler(_llm_find_similar_skill(agent, category, name, trigger, steps))
            except Exception:
                pass
                
        # 3. 智能演进合并
        if similar_folder:
            # similar_folder 这里实际上应该是文件名(去掉.md)，为了兼容先处理
            safe_sim = similar_folder.replace(".md", "")
            skill_path = skills_root / f"{safe_sim}.md"
            if skill_path.exists():
                try:
                    old_content = skill_path.read_text(encoding="utf-8")
                    new_content = run_async_handler(_llm_merge_skills(agent, old_content, trigger, steps))
                    skill_path.write_text(new_content, encoding="utf-8")
                    logger.info(f"Smart evolved existing skill: {similar_folder} (avoided duplicates)")
                    return skill_path
                except Exception as e:
                    logger.error(f"Failed to merge skill {similar_folder}: {e}")

        # 4. 全新落盘
        safe_name = re.sub(r'[^\w一-鿿-]', '_', name)[:40]
        skill_path = skills_root / f"{safe_name.lower()}.md"
        
        if not skill_path.exists():
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            content = (
                f"---\nname: {name}\ndescription: 会话自动检测的重复模式\n"
                f"trigger: {trigger}\ncreated: {now}\nversion: 1.0\n"
                f"usage_count: 0\nsuccess_count: 0\ncategory: {category}\n---\n\n"
                f"# {name}\n\n## 触发\n{trigger}\n\n## 步骤\n"
                + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            )
            skill_path.write_text(content, encoding="utf-8")
            logger.info(f"Auto-skill created via SkillsManager: {name} at {skill_path}")
        return skill_path
    except Exception as e:
        logger.error(f"Failed to create skill in SkillsManager: {e}")
        return Path()

def register_skill_evolution(folder_name: str, md_content: str, script_name: str = None, script_code: str = None, agent=None) -> Path:
    """自进化突变注册或智能合并技能"""
    try:
        skills_root = get_skills_root()
        clean_folder = re.sub(r'[^\w-]', '_', folder_name.strip().lower())
        
        # 解析元数据
        meta = _parse_skill_yaml(md_content)
        category = meta.get("category", "")
        if not category:
            category = _rule_categorize(meta.get("name", folder_name), meta.get("trigger", ""))
            if "category:" not in md_content:
                md_content = md_content.replace("---\n", f"---\ncategory: {category}\n", 1)

        # 尝试语义查重合并
        similar_folder = None
        if agent:
            try:
                name = meta.get("name", folder_name)
                trigger = meta.get("trigger", "")
                steps_text = re.findall(r'\d+\.\s*(.*)', md_content)
                similar_folder = run_async_handler(_llm_find_similar_skill(agent, category, name, trigger, steps_text))
            except Exception:
                pass

        target_folder = similar_folder if similar_folder else clean_folder
        skill_dir = skills_root / target_folder
        skill_dir.mkdir(parents=True, exist_ok=True)
        
        skill_md_path = skill_dir / "SKILL.md"
        
        if similar_folder:
            try:
                old_content = skill_md_path.read_text(encoding="utf-8") if skill_md_path.exists() else ""
                if old_content:
                    trigger = meta.get("trigger", "")
                    steps_text = re.findall(r'\d+\.\s*(.*)', md_content)
                    md_content = run_async_handler(_llm_merge_skills(agent, old_content, trigger, steps_text))
                    logger.info(f"Smart evolved existing skill via register_skill_evolution: {similar_folder}")
            except Exception as e:
                logger.error(f"Failed to merge registration for {similar_folder}: {e}")

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
