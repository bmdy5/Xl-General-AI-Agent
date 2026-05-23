import json
import re

TASK_SKILL_PROMPT = """分析以下对话，判断是否有重复的多步操作模式。

## 最近对话
{conversation}

问: 用户是否重复执行了类似的多步操作？如果有，这些步骤可以抽象为一个可复用技能吗？

只输出 JSON: {{"pattern_detected": true/false, "pattern_name": "技能名", "steps": ["步骤1", "步骤2"], "trigger": "触发关键词"}}
没有就 pattern_detected=false。不要输出其他内容。"""

async def detect_task_pattern(agent):
    """检测重复任务模式，建议创建技能"""
    if len(agent.messages) < 8:
        return None

    conversation = "\n".join(
        f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
        for m in agent.messages[-20:]
    )

    try:
        prompt = TASK_SKILL_PROMPT.format(conversation=conversation[:4000])
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            return None
        return json.loads(json_match.group(0))
    except Exception:
        return None
