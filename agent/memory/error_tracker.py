"""Error tracking with L1/L2/L3 classification + recipe matching."""
import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ERROR_INDICATORS = ["Error", "Traceback", "Exception", "failed", "失败", "报错", "异常"]

# Error levels
L1_TRANSIENT = 1   # SSL timeout, connection reset — retry with backoff
L2_SELF_HEAL = 2   # Path error, missing module — can auto-fix
L3_FATAL = 3       # Permission denied, disk full — escalate immediately

# L1 patterns (retryable)
L1_PATTERNS = [
    "SSL: UNEXPECTED_EOF", "UNEXPECTED_EOF_WHILE_READING",
    "Connection reset by peer", "ConnectError", "errno 54",
    "Temporary failure in name resolution", "timeout",
    "empty response from server",
]

# L2 patterns (self-healable)
L2_PATTERNS = [
    "file not found", "No such file or directory",
    "ModuleNotFoundError", "No module named",
    "directory not empty",
]

# L3 patterns (fatal — escalate)
L3_PATTERNS = [
    "Permission denied", "PermissionError",
    "Disk full", "No space left on device",
    "Access denied",
]


def classify_error(error_text: str) -> int:
    """Classify error into L1/L2/L3 based on pattern matching."""
    lower = error_text.lower()
    for pat in L3_PATTERNS:
        if pat.lower() in lower:
            return L3_FATAL
    for pat in L2_PATTERNS:
        if pat.lower() in lower:
            return L2_SELF_HEAL
    for pat in L1_PATTERNS:
        if pat.lower() in lower:
            return L1_TRANSIENT
    return L2_SELF_HEAL  # unknown errors default to self-heal


class ErrorTracker:
    """Tracks errors, deduplicates, and suggests recovery recipes."""

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir:
            self.base_dir = Path(storage_dir)
        else:
            self.base_dir = Path.home() / ".my-agent" / "memory"
        self.error_log = self.base_dir / "error_log.md"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._recent: dict[str, float] = {}  # error_key → last_timestamp
        self._counts: dict[str, int] = defaultdict(int)  # error_key → count
        self._recipes: dict[str, str] = {}  # error_key → fix_recipe

    def _key(self, error_text: str) -> str:
        """Generate dedup key from error text (first 80 chars, replace IDs/URLs)."""
        clean = re.sub(r'[0-9a-f]{8,}', '<ID>', error_text)
        clean = re.sub(r'https?://\S+', '<URL>', clean)
        return clean[:80]

    def should_report(self, error_text: str) -> tuple:
        """Check if error should be reported. Returns (should_report: bool, level: int).
        Same error within 5 minutes is silently dedup'd."""
        key = self._key(error_text)
        level = classify_error(error_text)
        now = datetime.now(timezone.utc).timestamp()

        self._counts[key] += 1

        # L3 always report immediately
        if level == L3_FATAL:
            return True, level

        # Dedup: within 5 minutes, don't report
        last = self._recent.get(key, 0)
        if now - last < 300:
            logger.info(f"Error dedup'd: {key[:60]}...")
            return False, level

        self._recent[key] = now

        # Pattern detection: 3+ of same type triggers pattern analysis
        if self._counts[key] >= 3:
            return True, level

        return True, level

    def save_recipe(self, error_text: str, fix_description: str):
        """Save a successful fix recipe for future matching."""
        key = self._key(error_text)
        self._recipes[key] = fix_description
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {ts}\n**错误**: {error_text[:200]}\n**修复方案**: {fix_description}\n"
        try:
            with open(self.error_log, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            logger.warning(f"Failed to save error recipe: {e}")

    def find_recipe(self, error_text: str) -> Optional[str]:
        """Try to find a known fix recipe for this error."""
        key = self._key(error_text)
        return self._recipes.get(key)

    def get_pattern_alert(self) -> Optional[str]:
        """If any error type has 3+ occurrences, return alert message."""
        alerts = [k for k, v in self._counts.items() if v >= 3]
        if alerts:
            return f"检测到 {len(alerts)} 种重复错误模式，建议分析根因并根治。"
        return None

    async def load_recipes(self):
        """Load known recipes from error_log.md at startup."""
        if not self.error_log.exists():
            return
        try:
            content = self.error_log.read_text(encoding="utf-8")
            blocks = re.findall(
                r'\*\*错误\*\*: (.+?)\n\*\*修复方案\*\*: (.+?)(?:\n##|\n\Z)',
                content, re.DOTALL,
            )
            for err, fix in blocks:
                key = self._key(err.strip())
                self._recipes[key] = fix.strip()
                self._counts[key] = 1
            logger.info(f"Loaded {len(blocks)} error recipes from log")
        except Exception:
            pass
