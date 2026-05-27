import json
import re
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger("evolution.rules")

EVOLVE_RULES_PROMPT = """从以下用户反馈和偏好中，找出重复≥2次的模式，生成自进化规则。

## 反馈/偏好（来源 + 内容）
{feedbacks}

## 已有规则
{existing_rules}

## 要求
- 仅对同一主题≥2次的反馈生成规则
- 规则格式: "当用户要求X时→应该Y（不要Z）" 或 "用户偏好: ..."
- 每条≤40字，最多3条
- 已有规则覆盖的跳过

JSON: {{"new_rules": ["规则1"]}}，无则空数组。只输出 JSON。"""

async def evolve_rules(agent) -> list[str]:
    """从 feedback/user 记忆中提取自进化规则，写入 EVOLVED_RULES.md."""
    entries = agent.memory._parse_index()
    feedbacks = []
    for e in entries:
        desc = e.get("description", "")
        if "[feedback]" in desc or "[user]" in desc:
            content = await agent.memory.get_entry(e["filename"])
            if content:
                clean = content.split("<!-- previous version -->")[0][:300]
                feedbacks.append(f"{desc}\n{clean}")

    if len(feedbacks) < 2:
        return []

    skills_dir = Path(__file__).resolve().parents[2] / "skills" / "自学习技能"
    skills_dir.mkdir(parents=True, exist_ok=True)
    rules_file = skills_dir / "规则与偏好.md"
    existing = rules_file.read_text(encoding="utf-8") if rules_file.exists() else ""

    try:
        # 提取现有子规则（仅以 - 开头的行）
        existing_bullet_points = "\n".join([l for l in existing.split("\n") if l.strip().startswith("-")])
        prompt = EVOLVE_RULES_PROMPT.format(
            feedbacks="\n---\n".join(feedbacks[-15:]),
            existing_rules=existing_bullet_points[:1000] or "(无)",
        )
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return []
        result = json.loads(json_match.group(0))
        new_rules = result.get("new_rules", [])

        if new_rules:
            now = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
            lines = [l for l in existing.split("\n") if l.strip().startswith("-")]
            for rule in new_rules:
                lines.append(f"- [{now}] {rule}")
            lines = lines[-8:]
            
            # 重构包含标准 YAML Frontmatter 的技能文件内容
            new_content = (
                "---\n"
                "name: 自学习规则与偏好\n"
                "description: 会话反思与工具审计中自动提炼的自进化规则\n"
                "trigger: 规则, 偏好, 习惯, 编程, 开发, 调试, 代码, 修改, 提问\n"
                "created: 2026-05-27T02:00:00Z\n"
                "version: 1.0\n"
                "---\n\n"
                "# 自学习规则与偏好\n\n"
                + "\n".join(lines)
            )
            # 引入规则全局锁，防范深夜进化时前台并行读取发生文件破损冲突
            from agent.core.prompt_builder import rules_lock
            with rules_lock:
                rules_file.write_text(new_content, encoding="utf-8")
            logger.info(f"Evolved {len(new_rules)} rule(s) saved to dynamic skills")

        return new_rules
    except Exception as e:
        logger.debug(f"Rule evolution skipped: {e}")
        return []
