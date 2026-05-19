"""自测验证 — 数据飞轮阶段 3。

从纠正事件生成测试 prompt，验证 AI 是否已学会避坑。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TEST_PROMPT = """你是一个 Agent 行为测试官。以下是之前用户纠正过的一个错误场景。

请判断：如果现在再次遇到这个场景，小萤（根据她的 EVOLVED_RULES 和系统设定）会怎么做？

## 纠正场景
工具: {tool}
错误: {correction}
期望行为: {expected}

## 当前规则
{current_rules}

## 请判断
只输出 JSON:
{{
  "would_repeat": true/false,
  "confidence": 1-10,
  "reasoning": "一句话说明为什么"
}}

如果 would_repeat=true，说明纠正还没被系统学会。"""


async def run_self_test(llm, memory, days: int = 3) -> dict:
    """从纠正事件生成测试，用 expected_behavior 做标准答案验证。"""
    from .evo_traces import get_recent_corrections

    corrections = get_recent_corrections(days=days)
    if not corrections:
        logger.info("Tester: no recent corrections to test")
        return {"total": 0, "passed": 0, "failed": 0, "details": []}

    rules_content = ""
    rules_file = memory.base_dir / "EVOLVED_RULES.md"
    if rules_file.exists():
        rules_content = rules_file.read_text(encoding="utf-8")[:2000]

    results = []
    for c in corrections[-10:]:
        try:
            prompt = TEST_PROMPT.format(
                tool=c.get("tool", "?"),
                correction=c.get("user_correction", "?")[:150],
                expected=c.get("expected_behavior", "?")[:150],
                current_rules=rules_content,
            )
            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            text = response.get("content", "").strip()
            import re as _re
            json_match = _re.search(r'\{[\s\S]*\}', text)
            if not json_match:
                continue

            result = json.loads(json_match.group(0))
            result["tool"] = c.get("tool", "?")
            result["correction"] = c.get("user_correction", "?")[:80]
            result["expected_behavior"] = c.get("expected_behavior", "?")[:80]
            results.append(result)
        except Exception as e:
            logger.debug(f"Self-test item failed: {e}")
            continue

    passed = sum(1 for r in results if not r.get("would_repeat", True))
    failed = sum(1 for r in results if r.get("would_repeat", False))

    report = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "details": results,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(f"Self-test: {passed}/{len(results)} passed, {failed} still at risk")
    return report


def save_test_report(report: dict):
    """保存测试报告到 pending_review/。"""
    from .evo_coach import PENDING_DIR
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = PENDING_DIR / f"自测报告-{today}.md"

    lines = [
        f"# 自测验证报告 — {today}",
        "",
        f"**结果**: {report['passed']}/{report['total']} 通过, {report['failed']} 仍有风险",
        "",
    ]

    for r in report.get("details", []):
        status = "✅ 已学会" if not r.get("would_repeat") else "❌ 仍有风险"
        lines.append(f"### {status}")
        lines.append(f"- 工具: {r.get('tool', '?')}")
        lines.append(f"- 纠正: {r.get('correction', '?')}")
        lines.append(f"- 置信度: {r.get('confidence', '?')}/10")
        lines.append(f"- 分析: {r.get('reasoning', '?')}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"测试时间: {report['tested_at']}")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Test report saved: {report_path}")
    return str(report_path)
