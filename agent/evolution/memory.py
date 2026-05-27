import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("evolution.memory")

MEMORY_SELECT_PROMPT = """从以下记忆列表中，选出与当前问题最相关的 5 条。

## 当前问题
{query}

## 记忆列表
{memories}

输出格式: 选中的记忆文件名列表，用逗号分隔。如: coding_prefs.md, deploy_info.md
只输出文件名，不要其他内容。"""

async def extract_coworker_memory(agent):
    """为同事（coworker）角色提取极简隔离记忆（不超过3条，每条不超过30字）"""
    user_id = getattr(agent, "current_user_id", None)
    if not user_id:
        return
    if len(agent.messages) < 4:
        return

    memory_file = Path(__file__).resolve().parents[2] / "agent_memory" / "context" / f"coworker_{user_id}.json"
    
    existing_memories = []
    if memory_file.exists():
        try:
            data = json.loads(memory_file.read_text(encoding="utf-8"))
            existing_memories = data.get("memories", [])
        except Exception:
            pass

    recent = agent.messages[-10:]
    conversation = "\n".join(
        f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:200]}"
        for m in recent
    )

    prompt = f"""你刚与亮哥的同事（QQ: {user_id}）完成了一次对话。请为该同事提取极简的记忆（最多3条，每条不超过30字，如对方的偏好、刚才讨论的核心问题、遗留任务等）。

## 现有记忆
{chr(10).join('- ' + m for m in existing_memories) if existing_memories else '暂无'}

## 最近对话 (最后 10 条消息)
{conversation}

请结合现有记忆和最近对话，输出更新后的极简记忆列表（不超过3条）。
只输出 JSON: {{"memories": ["记忆1", "记忆2", "记忆3"]}}
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
            result = json.loads(json_match.group(0))
            memories = result.get("memories", [])
            memories = [m[:30] for m in memories[:3] if m]
            
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            memory_file.write_text(json.dumps({
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "memories": memories
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Successfully saved coworker {user_id} isolated memory: {memories}")
    except Exception as e:
        logger.error(f"Failed to extract coworker memory: {e}")

async def select_relevant_memories(agent, query: str, max_count: int = 5) -> list[str]:
    """用 flash 模型选择最相关的记忆（替代纯时间戳排序）"""
    entries = agent.memory._parse_index()
    if len(entries) <= max_count:
        return [e["filename"] for e in entries]

    mem_list = "\n".join(
        f"- {e['filename']}: {e.get('description', '')}"
        for e in entries
    )

    try:
        prompt = MEMORY_SELECT_PROMPT.format(query=query[:200], memories=mem_list[:3000])
        response = await agent.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            model_override=agent.llm.model,
        )
        text = response.get("content", "").strip()
        filenames = re.findall(r'([\w一-鿿-]+\.md)', text)
        return filenames[:max_count]
    except Exception:
        pass

    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return [e["filename"] for e in entries[:max_count]]

def is_preference_query(user_input: str) -> bool:
    """判断用户是否在问偏好类问题"""
    signals = [
        "喜欢", "偏好", "习惯", "通常", "一般", "怎么",
        "prefer", "like", "usually", "normally", "how do I",
        "测试策略", "代码风格", "回复风格", "工作流",
    ]
    return any(s in user_input.lower() for s in signals)

def filter_memories_by_relevance(entries: list[dict], user_input: str) -> list[dict]:
    """根据用户问题类型过滤记忆"""
    if is_preference_query(user_input):
        preferred = [e for e in entries if "[user]" in e.get("description", "") or
                     "[feedback]" in e.get("description", "")]
        rest = [e for e in entries if e not in preferred]
        preferred.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        rest.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return preferred + rest
    return entries
