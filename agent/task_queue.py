"""任务队列 — 文件持久化，支持定时/重复任务.

用法:
  queue = TaskQueue()
  queue.add("清理旧日志", cron="daily")       # 每日执行
  queue.add("备份记忆", cron="0 */6 * * *")  # 每 6 小时
  queue.list()                                 # 列出待办
  queue.process_due()                          # 处理到期的
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUE_DIR = Path("/Users/xiaofeng/bot-我的自搭建agent/agent培养/xl进化/任务队列")


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

    def add(self, description: str, action: str, cron: str = "", priority: int = 0) -> dict:
        """添加任务."""
        task = {
            "id": f"task_{int(time.time())}_{len(self._tasks)}",
            "description": description,
            "action": action,  # 自然语言描述要执行的操作
            "cron": cron,      # cron 表达式或 "once" / "daily" / "hourly"
            "priority": priority,
            "created": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
            "next_run": None,
            "done": False,
        }
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
        """处理到期的任务（被动：返回到期任务列表，由外部决定是否执行）. """
        now = datetime.now(timezone.utc)
        due = []
        for t in self._tasks:
            if t.get("done"):
                continue
            cron = t.get("cron", "") or "once"
            if cron == "once" and t.get("last_run") is None:
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
        return due

    def stats(self) -> dict:
        return {
            "total": len(self._tasks),
            "pending": len([t for t in self._tasks if not t.get("done")]),
            "done": len([t for t in self._tasks if t.get("done")]),
        }
