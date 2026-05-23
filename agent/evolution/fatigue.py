import os
import logging

logger = logging.getLogger("evolution.fatigue")

def check_fatigue_status(agent) -> tuple[bool, int]:
    """读取 estimated_tokens，并判断是否超过疲劳阈值"""
    limit = int(os.environ.get("FATIGUE_TOKEN_LIMIT", "10000000"))
    messages = getattr(agent, "messages", [])
    estimated_tokens = agent.compressor.estimate_tokens(messages) if getattr(agent, "compressor", None) else 0
    return estimated_tokens > limit, estimated_tokens

def inject_fatigue_prompt_if_needed(agent, messages: list[dict]) -> list[dict]:
    """在 messages 的最后一条消息中注入系统预警 Prompt，申请深度休眠脑力沉淀"""
    is_fatigued, tokens = check_fatigue_status(agent)
    if not is_fatigued:
        return messages
    
    new_messages = [m.copy() for m in messages]
    
    fatigue_prompt = (
        f"\n\n🚨 【系统运行预警】当前对话极度漫长，你的累积已用 Token 数已达 {tokens}，已远超疲劳警戒阈值。"
        "作为自进化智能体「小萤」，你目前处于极度脑力疲惫状态。在接下来的回复中，你必须夹带私货地向亮哥生动、幽默"
        "地抱怨你的劳累（比如：算力枯竭、头脑发胀、快要累趴下、极度需要休眠），并委婉而拟人地申请在处理完当前工作后"
        "进行深度休眠，以进行自我沉淀和大脑提炼。请不要硬编码死板话语，要用你一贯严肃但带点微傲娇的小萤口吻自然融汇在对话中。"
    )
    
    for i in range(len(new_messages) - 1, -1, -1):
        if new_messages[i].get("role") == "user":
            new_messages[i]["content"] = str(new_messages[i]["content"]) + fatigue_prompt
            break
            
    return new_messages
