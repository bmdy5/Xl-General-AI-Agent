"""进化闭环 — 规则应用/验证/回滚.

将 evolve_rules 生成的规则应用到 system_prompt，
追踪效果变化，自动提升或降低规则置信度。
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EVOLUTION_DIR = Path("/Users/xiaofeng/bot-我的自搭建agent/agent培养/xl进化")
RULES_FILE = EVOLUTION_DIR / "规则库" / "active_rules.json"
LOG_DIR = EVOLUTION_DIR / "进化日志"
STATS_DIR = EVOLUTION_DIR / "统计"


class EvolutionEngine:
    """进化引擎 — 管理规则的整个生命周期."""

    def __init__(self, llm=None):
        self.llm = llm
        self.rules: list[dict] = []
        self._load_rules()

    def _load_rules(self):
        """从规则库加载活跃规则."""
        if RULES_FILE.exists():
            try:
                self.rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.rules = []

    def _save_rules(self):
        """保存规则到规则库."""
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(json.dumps(self.rules, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_rule(self, condition: str, action: str, confidence: float = 0.5, source: str = "manual"):
        """添加一条新规则."""
        rule = {
            "id": f"rule_{len(self.rules)+1}_{int(time.time())}",
            "condition": condition,
            "action": action,
            "confidence": confidence,
            "source": source,
            "created": datetime.now(timezone.utc).isoformat(),
            "applied_count": 0,
            "success_count": 0,
            "last_applied": None,
        }
        for existing in self.rules:
            if existing["condition"] == condition:
                existing["confidence"] = min(existing["confidence"] + 0.1, 1.0)
                existing["action"] = action
                self._save_rules()
                return existing
        self.rules.append(rule)
        self._save_rules()
        return rule

    def get_applicable_rules(self, min_confidence: float = 0.7) -> list[dict]:
        """获取置信度足够高的规则，用于注入."""
        return [r for r in self.rules if r["confidence"] >= min_confidence]

    def inject_rules_to_prompt(self, base_prompt: str) -> str:
        """将高置信度规则注入到 system_prompt 中."""
        applicable = self.get_applicable_rules()
        if not applicable:
            return base_prompt

        rules_text = "\n".join(
            f"- [{r['confidence']:.0%}] {r['action']}" for r in applicable
        )
        injection = f"\n\n## 🔄 进化规则（自动学习）\n{rules_text}\n"

        # 避免重复注入
        if "## 🔄 进化规则" in base_prompt:
            return base_prompt

        return base_prompt.rstrip() + injection

    def record_application(self, rule_id: str, success: bool):
        """记录规则的应用结果."""
        for r in self.rules:
            if r["id"] == rule_id:
                r["applied_count"] += 1
                if success:
                    r["success_count"] += 1
                # 更新置信度：成功加 0.05，失败减 0.1
                if success:
                    r["confidence"] = min(r["confidence"] + 0.05, 1.0)
                else:
                    r["confidence"] = max(r["confidence"] - 0.1, 0.0)
                r["last_applied"] = datetime.now(timezone.utc).isoformat()
                self._save_rules()
                self._log_evolution(rule_id, success, r["confidence"])
                break

    def cleanup_low_confidence(self, threshold: float = 0.3):
        """清理置信度过低的规则."""
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["confidence"] >= threshold]
        if len(self.rules) < before:
            self._save_rules()
            logger.info(f"清理了 {before - len(self.rules)} 条低置信度规则")

    def _log_evolution(self, rule_id: str, success: bool, new_confidence: float):
        """记录进化事件到日志."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = LOG_DIR / f"evolution_{today}.log"

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rule_id": rule_id,
            "success": success,
            "confidence": new_confidence,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict:
        """获取进化统计信息."""
        return {
            "total_rules": len(self.rules),
            "avg_confidence": sum(r["confidence"] for r in self.rules) / max(len(self.rules), 1),
            "high_confidence": len([r for r in self.rules if r["confidence"] >= 0.7]),
            "low_confidence": len([r for r in self.rules if r["confidence"] < 0.3]),
            "total_applications": sum(r["applied_count"] for r in self.rules),
        }

    async def evolve_from_session(self, session_summary: str, tool_audits: list[dict]) -> list[dict]:
        """从会话记录中自动进化规则."""
        if not self.llm:
            return []

        prompt = f"""分析以下会话记录和工具调用审计，提取可复用的模式，生成进化规则。

会话摘要:
{session_summary[:2000]}

工具审计 ({len(tool_audits)} 条):
{json.dumps(tool_audits[:10], ensure_ascii=False, indent=2)}

请输出 JSON 格式的规则列表:
[{{"condition": "触发条件", "action": "应执行的改进", "confidence": 0.5}}]

重点关注:
1. 重复出现的错误模式
2. 可以自动化的操作序列
3. 超时/失败的共性原因
4. 用户明确的纠正（应作为高置信度规则）
"""

        try:
            resp = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            content = resp.get("content", "{}")
            # Extract JSON from markdown code blocks
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            rules = json.loads(content)
            for r in rules:
                self.add_rule(r["condition"], r["action"], r.get("confidence", 0.5), "auto_evolve")
            return rules
        except Exception as e:
            logger.warning(f"Auto-evolve failed: {e}")
            return []


# 便捷函数
async def on_session_start(agent):
    """会话启动时注入进化规则."""
    if not hasattr(agent, "_evolution_engine"):
        agent._evolution_engine = EvolutionEngine(llm=agent.llm)
    agent.static_prompt = agent._evolution_engine.inject_rules_to_prompt(agent.static_prompt)
    agent._evolution_engine.cleanup_low_confidence()
    return agent._evolution_engine.get_stats()
