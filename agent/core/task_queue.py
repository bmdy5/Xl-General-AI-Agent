"""任务队列 — 文件持久化，支持定时/重复任务.

Agent 通过 schedule_task tool 自主管理任务，无需硬编码。
用法:
  queue = TaskQueue()
  queue.add(description, action, cron)         # cron: daily / hourly / once / cron expr
  queue.list()                                 # 列出待办
  queue.process_due()                          # 处理到期的
"""

import json
import re
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUE_DIR = Path("/Users/xiaofeng/bot-我的自搭建agent/agent培养/xl进化/任务队列")


def parse_natural_time(text: str):
    """Parse natural-language time into (cron_expression, next_run_iso).

    Returns tuple: (cron: str, next_run: Optional[str])
    - ("daily", None) for daily
    - ("hourly", None) for hourly
    - ("once", "<iso_timestamp>") for one-shot tasks
    - ("cron expr", None) for cron expressions
    """
    text = text.strip().lower()

    # "in N minutes/hours"
    m = re.match(r'in\s+(\d+)\s*(min|minute|minutes|h|hour|hours)', text)
    if m:
        n = int(m.group(1))
        seconds = n * 60 if 'min' in m.group(2) else n * 3600
        run_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        return ("once", run_at)

    # "tomorrow [H:MM]"
    m = re.match(r'tomorrow\s*(\d{1,2}):(\d{2})?', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        run_at = tomorrow.replace(hour=h, minute=mi, second=0, microsecond=0).isoformat()
        return ("once", run_at)

    # "daily [HH:MM]"
    if text.startswith("daily"):
        m = re.search(r'(\d{1,2}):(\d{2})', text)
        if m:
            return (f"{m.group(2)} {m.group(1)} * * *", None)
        return ("daily", None)

    # "hourly"
    if text == "hourly":
        return ("hourly", None)

    # "every Monday 8am" / "every Mon 08:00"
    day_map = {"mon": 1, "monday": 1, "tue": 2, "tuesday": 2,
               "wed": 3, "wednesday": 3, "thu": 4, "thursday": 4,
               "fri": 5, "friday": 5, "sat": 6, "saturday": 6,
               "sun": 7, "sunday": 7}
    m = re.match(r'every\s+(\w+)\s*(\d{1,2}):(\d{2})', text)
    if m:
        day_name = m.group(1).lower()
        if day_name in day_map:
            day = day_map[day_name]
            mi, h = m.group(3), m.group(2)
            return (f"{mi} {h} * * {day}", None)

    # Already a cron expression? Pass through
    if re.match(r'^[\d\*,/\-\s]+$', text):
        return (text, None)

    # Default: once, run now
    run_at = datetime.now(timezone.utc).isoformat()
    return ("once", run_at)


class TaskQueue:
    """文件持久化任务队列."""

    def __init__(self, queue_dir: Path = QUEUE_DIR):
        self.dir = queue_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self.dir / "tasks.json"
        self._tasks: list[dict] = []
        self._load()

    def _load(self):
        if self._tasks_file.exists():
            try:
                self._tasks = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            except Exception:
                self._tasks = []

    def _save(self):
        self._tasks_file.write_text(
            json.dumps(self._tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, description: str, action: str, cron: str = "",
            priority: int = 0, auto_execute: bool = False) -> dict:
        """Add task with optional auto_execute flag."""
        parsed_cron, once_at = parse_natural_time(cron)

        task = {
            "id": f"task_{int(time.time())}_{len(self._tasks)}",
            "description": description,
            "action": action,
            "cron": parsed_cron,
            "priority": priority,
            "auto_execute": auto_execute,
            "created": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
            "next_run": once_at,
            "done": False,
        }

        # Dedup: same description + same cron → skip
        for existing in self._tasks:
            if not existing.get("done") and existing["description"] == description \
               and existing.get("cron") == parsed_cron:
                logger.info(f"Dedup: skipping duplicate task '{description}'")
                return existing

        # Max pending limit (20)
        pending = [t for t in self._tasks if not t.get("done")]
        if len(pending) >= 20:
            oldest = min(pending, key=lambda t: t.get("created", ""))
            self.mark_done(oldest["id"])
            logger.info(f"Task limit reached, auto-closed oldest: {oldest['description']}")

        self._tasks.append(task)
        self._save()
        return task

    def list(self, include_done: bool = False) -> list[dict]:
        """列出任务."""
        if include_done:
            return self._tasks
        return [t for t in self._tasks if not t.get("done")]

    def mark_done(self, task_id: str):
        for t in self._tasks:
            if t["id"] == task_id:
                cron = t.get("cron", "") or "once"
                if cron in ("once", ""):
                    t["done"] = True
                t["last_run"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return True
        return False

    def remove(self, task_id: str):
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        self._save()

    def clear_done(self):
        self._tasks = [t for t in self._tasks if not t.get("done")]
        self._save()

    def process_due(self, agent=None) -> list[dict]:
        """Process due tasks with support for exact next_run timestamps."""
        now = datetime.now(timezone.utc)
        due = []
        for t in self._tasks:
            if t.get("done"):
                continue
            cron = t.get("cron", "") or "once"

            if cron == "once":
                next_run = t.get("next_run")
                if next_run:
                    run_at = datetime.fromisoformat(next_run)
                    if now >= run_at:
                        due.append(t)
                elif t.get("last_run") is None:
                    due.append(t)
            elif cron == "daily":
                last = t.get("last_run")
                if last:
                    last_dt = datetime.fromisoformat(last)
                    if (now - last_dt).total_seconds() > 86400:
                        due.append(t)
                else:
                    due.append(t)
            elif cron == "hourly":
                last = t.get("last_run")
                if last:
                    last_dt = datetime.fromisoformat(last)
                    if (now - last_dt).total_seconds() > 3600:
                        due.append(t)
                else:
                    due.append(t)
            else:
                # Cron expression -- 高精度时间滑窗与星期校对判定
                last = t.get("last_run")
                m_cron = re.match(r'^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+(\*|\d)$', cron)
                if m_cron:
                    target_minute = int(m_cron.group(1))
                    target_hour = int(m_cron.group(2))
                    target_day_of_week = m_cron.group(3)
                    
                    # 构造今日的绝对触发时间点 (继承 now 的 UTC 时区信息)
                    try:
                        trigger_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                    except ValueError as val_err:
                        logger.error(f"Task {t['id']} has invalid cron values: {cron}. Error: {val_err}")
                        continue
                    
                    # 校验星期数是否匹配
                    if target_day_of_week != "*":
                        target_day = int(target_day_of_week)
                        target_day = 7 if target_day == 0 else target_day
                        if now.isoweekday() != target_day:
                            continue
                            
                    # 如果今天还没到触发的分钟/小时，则直接跳过，绝对不提前剧透执行
                    if now < trigger_today:
                        continue
                        
                    # 如果已过今日触发点，且今天尚未执行过，则判定为 due 到期执行
                    if not last or datetime.fromisoformat(last) < trigger_today:
                        due.append(t)
                else:
                    # 兜底非标准复杂 cron 表达式：进行简单的跨天检验，防止漏跑
                    if not last or datetime.fromisoformat(last).date() < now.date():
                        due.append(t)
        return due

    def stats(self) -> dict:
        return {
            "total": len(self._tasks),
            "pending": len([t for t in self._tasks if not t.get("done")]),
            "done": len([t for t in self._tasks if t.get("done")]),
        }
