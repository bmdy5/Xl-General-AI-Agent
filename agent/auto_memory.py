"""自动检测纠正 — XL 被用户纠正时自动记忆教训."""

import re
import logging

logger = logging.getLogger(__name__)

# 纠正信号：用户说这些词时可能是在纠正 XL
CORRECTION_PATTERNS = [
    r"不对",
    r"错了",
    r"不是[这样这的]",
    r"应该[是有的]",
    r"你看[一下看]?",
    r"你看看",
    r"不行",
    r"不对啊",
    r"没[有用]",
    r"问题[还在是]",
    r"根本[就不]?对",
    r"你怎么[搞做]",
    r"一团糟",
    r"乱七八糟",
    r"完全不[对行]",
    r"说了[几次多遍]",
    r"不是这样",
    r"反了",
    r"方向[不对错]",
    r"理解[错误不对]",
    r"做错了",
    r"搞[错砸]了",
    r"这不是我[要想要说]的",
]


def is_correction(user_input: str) -> bool:
    """检测用户输入是否包含纠正信号."""
    for pattern in CORRECTION_PATTERNS:
        if re.search(pattern, user_input):
            return True
    return False


def extract_lesson(user_input: str, last_response: str = "") -> str:
    """从纠正中提取教训（精简版，实际应由 LLM 做）. """
    # 简洁版：直接返回纠正内容，让 LLM 后续分析
    return user_input[:200]


async def auto_remember_correction(agent, user_input: str, last_response: str = ""):
    """自动记忆纠正 —— 检测到纠正信号就保存."""
    if not is_correction(user_input):
        return

    lesson = extract_lesson(user_input, last_response)
    desc = f"[auto] 纠正: {user_input[:60]}"

    try:
        await agent.memory.save(
            filename=f"auto_correction_{hash(user_input) % 10000}.txt",
            description=desc,
            content=f"用户纠正: {lesson}\n\n"
                    f"上下文: {last_response[:300] if last_response else 'N/A'}\n\n"
                    f"教训: 待后续分析",
        )
        logger.info(f"📝 自动记忆纠正: {desc}")
    except Exception as e:
        logger.warning(f"记忆纠正失败: {e}")
