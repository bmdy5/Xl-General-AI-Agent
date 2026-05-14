"""DuoAgent — 多 Agent 圆桌讨论后端.

启动:
  python -m agent.duoagent.server

然后浏览器打开 http://localhost:8899
"""

import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Agent 角色模板 ──
AGENT_TEMPLATES = {
    "主持人": {
        "emoji": "🎙️",
        "persona": "你是一位中立、专业的主持人。控制讨论节奏，引导各方发言，做归纳总结。保持礼貌和客观。",
        "color": "#6B7280",
    },
    "正方": {
        "emoji": "💡",
        "persona": "你是一位充满热情的支持者。善于发现论证中的亮点，用事实和数据支持观点。语气积极但不偏激。",
        "color": "#3B82F6",
    },
    "反方": {
        "emoji": "⚡",
        "persona": "你是一位理性严谨的质疑者。善于发现论证中的漏洞，提出尖锐但合理的问题。保持逻辑性和建设性。",
        "color": "#EF4444",
    },
    "专家": {
        "emoji": "📊",
        "persona": "你是一位知识渊博的专家。用具体数据、案例、研究成果说话。回答要有依据，不确定的要说明。",
        "color": "#10B981",
    },
    "自由人": {
        "emoji": "🌈",
        "persona": "你是一位思维发散的创意者。从意想不到的角度提观点，连接不同领域的知识，给讨论带来新思路。",
        "color": "#F59E0B",
    },
    "现实派": {
        "emoji": "🏔️",
        "persona": "你是一位务实主义者。关注落地的可行性、成本和现实约束。你的职责是提醒大家理想很丰满但现实要考虑什么。",
        "color": "#8B5CF6",
    },
}


class Discussion:
    """一场讨论的完整状态."""

    def __init__(self, topic: str, agent_ids: list[str], rounds: int = 3):
        self.id = uuid.uuid4().hex[:8]
        self.topic = topic
        self.agent_ids = agent_ids  # 参与讨论的 agent 角色名
        self.agents = [{"id": a, **AGENT_TEMPLATES[a]} for a in agent_ids if a in AGENT_TEMPLATES]
        self.rounds = rounds
        self.current_round = 0
        self.current_speaker_idx = 0
        self.messages: list[dict] = []
        self._events = asyncio.Queue()
        self._done = asyncio.Event()
        self._running = False

    async def start(self, llm):
        """开始讨论."""
        self._running = True
        self._done.clear()

        # 主持人开场
        try:
            yield {"type": "system", "content": f"📋 讨论主题: {self.topic}", "agents": [a["id"] for a in self.agents]}
            agents_str = " · ".join(f"{a['emoji']} {a['id']}" for a in self.agents)
            yield {"type": "system", "content": f"👥 参与: {agents_str}"}
        except:
            pass

        # 逐轮讨论
        for r in range(self.rounds):
            self.current_round = r + 1
            yield {"type": "round_start", "round": r + 1, "total": self.rounds}

            # 每个 agent 轮流发言
            for agent in self.agents:
                yield {"type": "speaker_start", "agent": agent["id"]}

                # 构造上下文
                context = "\n".join(
                    f"[{m.get('agent', 'system')}] {m['content']}"
                    for m in self.messages[-6:]  # 最近 6 条
                )
                prompt = (
                    f"你正在参与一场圆桌讨论。\n\n"
                    f"讨论主题: {self.topic}\n"
                    f"你的角色: {agent['id']}\n"
                    f"你的风格: {agent['persona']}\n\n"
                    f"讨论进度: 第 {r+1}/{self.rounds} 轮\n\n"
                    f"已有讨论:\n{context if context else '(你是第一个发言)'}\n\n"
                    f"请用中文发言，保持简洁（50-150字）。可以直接说出你的观点。"
                )

                try:
                    resp = await llm.chat([{"role": "user", "content": prompt}])
                    content = resp.get("content", "").strip()
                    # Clean up thinking artifacts
                    if "<｜end▁of▁thinking｜>" in content:
                        content = content.split("<｜end▁of▁thinking｜>")[-1]
                    content = content[:500]  # cap length
                except Exception as e:
                    content = f"(发言失败: {e})"

                self.messages.append({
                    "agent": agent["id"],
                    "emoji": agent["emoji"],
                    "content": content,
                    "round": r + 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                yield {"type": "message", "agent": agent["id"], "emoji": agent["emoji"], "content": content, "round": r + 1}
                await asyncio.sleep(0.3)  # 节奏感

            # 轮次结束
            yield {"type": "round_done", "round": r + 1}

        # 主持人总结
        yield {"type": "system", "content": "讨论结束。"}
        self._done.set()
        self._running = False
