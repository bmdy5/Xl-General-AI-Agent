import logging
from pathlib import Path

logger = logging.getLogger("agent.memory.session")

def list_memories(manager) -> list[str]:
    """List all memory entries, latest first."""
    entries = manager._parse_index()
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return [
        f"- [{e['description']}]({e['filename']}) `{e.get('timestamp', '')}`"
        for e in entries
    ]


async def build_user_profile(manager, llm) -> str:
    """合成用户画像（从长期记忆中自动聚合）."""
    entries = manager._parse_index()
    user_facts = []
    for e in entries:
        desc = e.get("description", "")
        fname = e.get("filename", "")
        if "[user]" in desc or "[feedback]" in desc:
            content = await manager.get_entry(fname)
            if content:
                clean = content.split("<!-- previous version -->")[0].strip()[:500]
                user_facts.append(clean)
    if not user_facts:
        return ""

    profile_file = manager.base_dir / "USER_PROFILE.md"
    prompt = (
        "从以下关于用户的事实和反馈中，合成一段深层用户画像（100字以内）。\n"
        "不是复述事实，而是描述'这是一个什么样的人'：\n"
        "工作风格、决策偏好、技术品味、沟通习惯、核心价值观。\n\n"
        + "\n---\n".join(user_facts)
    )
    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        profile = response.get("content", "").strip()
        if profile:
            profile_file.write_text(profile, encoding="utf-8")
            return f"\n\n## Who You Are (User Profile)\n{profile}\n"
    except Exception:
        pass

    if profile_file.exists():
        return f"\n\n## Who You Are (User Profile)\n{profile_file.read_text(encoding='utf-8')}\n"
    return ""
