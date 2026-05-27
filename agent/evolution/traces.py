"""执行轨迹录音 — 结构化 JSONL 记录工具调用 + 用户纠正。

数据飞轮阶段 1：每次工具调用结果和用户纠正信号写入
~/.my-agent/evolution/traces/YYYY-MM-DD.jsonl
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TRACES_DIR = Path.home() / ".my-agent" / "evolution" / "traces"


def _traces_file() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    return TRACES_DIR / f"{today}.jsonl"


def record_tool_call(
    tool_name: str,
    args: dict,
    result: str,
    had_error: bool = False,
    user_correction: Optional[str] = None,
):
    """记录一次工具调用。user_correction 非空表示用户随后纠正了这次调用。"""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": tool_name,
            "args": _safe_args(args),
            "result_snippet": str(result)[:200],
            "had_error": had_error,
            "user_correction": user_correction,
        }
        with open(_traces_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"Trace record failed: {e}")


def _read_traces(filepath: Path) -> list[dict]:
    lines = []
    try:
        for line in filepath.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                lines.append(json.loads(line))
    except Exception:
        pass
    return lines


def _write_traces(filepath: Path, entries: list[dict]):
    filepath.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


def _safe_args(args: dict) -> dict:
    """截断过长参数值，避免 traces 文件膨胀。"""
    safe = {}
    for k, v in (args or {}).items():
        s = str(v)
        if len(s) > 300:
            s = s[:300] + "..."
        safe[k] = s
    return safe
