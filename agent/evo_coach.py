"""夜间教练分析 — 数据飞轮阶段 2。

读取今日执行轨迹，用 LLM 分析失败模式，生成改进提案。
输出到 ~/.my-agent/skills/pending_review/ 等待人工审核。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PENDING_DIR = Path.home() / ".my-agent" / "skills" / "pending_review"

COACH_PROMPT = """你是一个 Agent 进化教练。以下是今天小萤的执行轨迹（工具调用+用户纠正）。

请分析：

1. 有哪些反复出现的失败模式？
2. 哪些用户纠正反映了系统性的问题？
3. 有哪些可以在 EVOLVED_RULES.md 中增加或修改的规则？
4. 有哪些 skill 文件需要创建或更新？

## 今日执行轨迹
{traces_text}

## 输出格式
只输出 JSON:
{{
  "summary": "一句话总结今日发现",
  "patterns": [
    {{"pattern": "失败模式描述", "evidence": ["trace证据1"], "severity": "high/medium/low"}}
  ],
  "rule_updates": [
    {{"target_file": "EVOLVED_RULES.md", "new_rule": "规则文本", "reason": "添加理由"}}
  ],
  "skill_proposals": [
    {{"skill_name": "技能名", "description": "描述", "trigger": "何时触发"}}
  ]
}}

如果没有发现，patterns/rule_updates/skill_proposals 为空数组。只输出 JSON。"""


async def run_coach_analysis(llm, today_str: str = "") -> dict | None:
    """读取今日 traces，LLM 分析，生成改进提案。返回分析结果 dict。"""
    from .evo_traces import get_today_traces, get_recent_corrections, TRACES_DIR

    if not today_str:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 读取今日 traces
    traces = get_today_traces()
    corrections = get_recent_corrections(days=1)

    if not traces:
        logger.info("Coach: no traces today, skipping analysis")
        return None

    # 构建分析文本
    lines = []
    lines.append(f"## 工具调用 ({len(traces)}次)")
    for t in traces[-50:]:  # 最多50条
        corr = f" ⚠️ 用户纠正: {t['user_correction'][:60]}" if t.get("user_correction") else ""
        err = " [失败]" if t.get("had_error") else ""
        lines.append(f"- [{t['tool']}]{err} {t['result_snippet'][:80]}{corr}")

    if corrections:
        lines.append(f"\n## 今日纠正事件 ({len(corrections)}次)")
        for c in corrections[-10:]:
            lines.append(f"- [{c['tool']}] {c['user_correction'][:120]}")

    traces_text = "\n".join(lines)

    # LLM 分析
    try:
        prompt = COACH_PROMPT.format(traces_text=traces_text[:4000])
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        text = response.get("content", "").strip()
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.warning("Coach: LLM did not return valid JSON")
            return None

        result = json.loads(json_match.group(0))
        logger.info(f"Coach analysis: {result.get('summary', '?')[:80]}")
        return result
    except Exception as e:
        logger.error(f"Coach analysis failed: {e}")
        return None


def save_coach_report(analysis: dict):
    """将教练分析报告保存到 pending_review/ 目录。"""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = PENDING_DIR / f"教练分析-{today}.md"

    # 构建 Markdown 报告
    lines = [
        f"# 教练分析报告 — {today}",
        "",
        f"## 摘要",
        analysis.get("summary", "(无)"),
        "",
    ]

    patterns = analysis.get("patterns", [])
    if patterns:
        lines.append("## 发现的问题模式")
        for i, p in enumerate(patterns, 1):
            severity = p.get("severity", "medium")
            lines.append(f"### {i}. [{severity}] {p.get('pattern', '?')}")
            for ev in p.get("evidence", []):
                lines.append(f"  - {ev[:150]}")
            lines.append("")

    rule_updates = analysis.get("rule_updates", [])
    if rule_updates:
        lines.append("## 建议规则更新")
        for r in rule_updates:
            lines.append(f"- 目标: {r.get('target_file', 'EVOLVED_RULES.md')}")
            lines.append(f"  规则: {r.get('new_rule', '?')}")
            lines.append(f"  理由: {r.get('reason', '?')}")
        lines.append("")

    skill_proposals = analysis.get("skill_proposals", [])
    if skill_proposals:
        lines.append("## 建议创建技能")
        for s in skill_proposals:
            lines.append(f"- **{s.get('skill_name', '?')}**: {s.get('description', '?')}")
            lines.append(f"  触发: {s.get('trigger', '?')}")
        lines.append("")

    lines.append("---")
    lines.append(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
    lines.append("状态: pending_review — 等待亮哥审核")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Coach report saved: {report_path}")
    return str(report_path)


async def auto_apply_rules(analysis: dict, memory):
    """自动应用低风险的规则更新到 EVOLVED_RULES.md。"""
    rule_updates = analysis.get("rule_updates", [])
    if not rule_updates:
        return 0

    count = 0
    for r in rule_updates:
        if r.get("target_file") == "EVOLVED_RULES.md":
            new_rule = r.get("new_rule", "")
            if new_rule:
                await memory.append_to_core(
                    "EVOLVED_RULES.md",
                    f"教练自动更新: {new_rule[:40]}",
                    f"- [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')}] {new_rule}",
                )
                count += 1
                logger.info(f"Auto-applied rule: {new_rule[:60]}")

    return count
