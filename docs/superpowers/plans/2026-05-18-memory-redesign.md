# Memory System Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two-tier memory (core personality + knowledge index to notes) with error-tracking loop that auto-saves compressed summaries, routes knowledge to learning notes, and self-heals broken links.

**Architecture:** Add three new modules: error_tracker.py for error classification/recipes, enhance MemoryManager with routing/self-healing, and hook compressor to auto-persist summaries. No hardcoded knowledge paths.

**Tech Stack:** Python 3.14, SQLite FTS5, aiofiles

---

### Task 1: Compressor auto-save summary to memory

**Files:**
- Modify: `agent/compressor.py:140-144`

After LLM generates the summary, auto-call `memory.save()` to persist it.

- [ ] **Step 1: Add memory save call after summary generation**

In `agent/compressor.py`, after line 140 where `summary_msg` is built:

```python
# line ~140, after summary_msg = {...}
summary_msg = {
    "role": "system",
    "content": f"[历史对话摘要]\n{summary}",
}

# NEW: auto-persist compressed summary
if memory and hasattr(memory, 'save'):
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await memory.save(
            filename=f"compressed_{date_str}",
            description=f"[compressed] 对话压缩摘要 {date_str}",
            content=f"# 对话压缩摘要\n\n日期: {date_str}\n\n{summary}",
        )
        logger.info(f"Compressed summary saved to memory")
    except Exception as e:
        logger.warning(f"Failed to save compressed summary: {e}")
```

- [ ] **Step 2: Add datetime import at top of compressor.py**

```python
# Add to existing imports at line ~1-10
from datetime import datetime, timezone
```

- [ ] **Step 3: Restart gateway and verify**

```bash
pkill -f "main.py --gateway" && sleep 1
nohup venv/bin/python main.py --gateway >> gateway.log 2>&1 &
```

- [ ] **Step 4: Commit**

```bash
git add agent/compressor.py
git commit -m "feat(compressor): auto-save compressed summary to memory"
```

---

### Task 2: Routing rules — bot-driven knowledge routing

**Files:**
- Create: `~/.my-agent/memory/routing_rules.md`
- Modify: `agent/memory/manager.py:13-65`

Add a `routing_rules.md` file that the bot reads to decide where knowledge goes. The MemoryManager loads it and makes routing suggestions available.

- [ ] **Step 1: Create routing_rules.md template**

Create `~/.my-agent/memory/routing_rules.md`:

```markdown
# 知识路由规则

学习笔记根目录: /Users/xiaofeng/Desktop/学习笔记

## 路由判断
1. 跟小萤自身相关（人格、行为、成长、自学习）→ 01-小萤/自学习笔记/
2. Agent 通用技术（工具、记忆、多智能体、循环）→ 02-Agent技术/记忆系统/
3. 具体项目经验、踩坑记录、bug修复 → 06-工作记录/工程实践/
4. 用户知识（亮哥教给我的）→ 01-小萤/自学习笔记/

## 记忆类型路由
- feedback/behavior → core memory (不写笔记)
- learn/technical → 知识索引 → 上述笔记目录
- project/experience → 06-工作记录/工程实践/
- personal/identity → core memory (不写笔记)
```

- [ ] **Step 2: Add routing_rules loading to MemoryManager**

In `agent/memory/manager.py`, add to `__init__`:

```python
# After line 39 (self.base_dir = ...)
self.rules_file = self.base_dir / "routing_rules.md"
if not self.rules_file.exists():
    self.rules_file.write_text(DEFAULT_ROUTING_RULES, encoding="utf-8")
```

At the top of the file, add the default template:

```python
DEFAULT_ROUTING_RULES = """\
# 知识路由规则

学习笔记根目录: /Users/xiaofeng/Desktop/学习笔记

## 路由判断
1. 跟小萤自身相关（人格、行为、成长、自学习）→ 01-小萤/自学习笔记/
2. Agent 通用技术（工具、记忆、多智能体、循环）→ 02-Agent技术/记忆系统/
3. 具体项目经验、踩坑记录、bug修复 → 06-工作记录/工程实践/
4. 用户知识（亮哥教给我的）→ 01-小萤/自学习笔记/

## 记忆类型路由
- feedback/behavior → core memory (不写笔记)
- learn/technical → 知识索引 → 上述笔记目录
- project/experience → 06-工作记录/工程实践/
- personal/identity → core memory (不写笔记)
"""
```

- [ ] **Step 3: Add get_routing_rules() and save_to_notes() methods**

In `agent/memory/manager.py`, add after `get_entry()`:

```python
def get_routing_rules(self) -> str:
    """返回当前路由规则，供 bot 读取和修改."""
    if self.rules_file.exists():
        return self.rules_file.read_text(encoding="utf-8")
    return ""

async def save_to_notes(self, dir_path: str, filename: str, content: str) -> Optional[str]:
    """Save knowledge to learning notes directory. Returns the full path or None."""
    from pathlib import Path as _Path
    base = _Path(self.rules_file.read_text(encoding="utf-8").split("学习笔记根目录: ")[1].split("\n")[0].strip())
    target_dir = base / dir_path
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = filename.replace("/", "_").replace("\\", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    filepath = target_dir / safe_name
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)
```

- [ ] **Step 4: Commit**

```bash
git add agent/memory/manager.py
git commit -m "feat(memory): add routing_rules and save_to_notes for knowledge indexing"
```

---

### Task 3: Smart index — MEMORY.md as pointer hub

**Files:**
- Modify: `agent/memory/manager.py:77-109` (save method)
- Modify: `agent/memory/manager.py:50-75` (load_context)

Enhance `save()` to accept an optional `note_path` parameter. When set, the MEMORY.md entry becomes a pointer to the learning notes file instead of storing full content.

- [ ] **Step 1: Modify save() to support note pointers**

In `agent/memory/manager.py`, modify the `save()` method signature:

```python
async def save(self, filename: str, description: str, content: str, 
               note_path: Optional[str] = None) -> str:
    """Save memory. If note_path given, stores pointer instead of full content."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"

    if note_path:
        # Knowledge index mode: store pointer, not content
        index_content = f"<!-- pointer -->\n# {description}\n\n→ 笔记位置: {note_path}\n\n_内容存储在{note_path}，这里只做索引。_"
        topic_file = self.base_dir / safe_name
        is_update = topic_file.exists()
        topic_file.write_text(index_content, encoding="utf-8")
        index_line = f"- [{description}]({note_path}) `{timestamp}`"
    else:
        # Core memory mode: store full content
        topic_file = self.base_dir / safe_name
        is_update = topic_file.exists()
        if is_update:
            old_content = topic_file.read_text(encoding="utf-8")
            content = (
                f"<!-- updated: {timestamp} -->\n{content}\n\n"
                f"---\n<!-- previous version -->\n{old_content[:500]}"
            )
        topic_file.write_text(content, encoding="utf-8")
        index_line = f"- [{description}]({safe_name}) `{timestamp}`"
    
    self._upsert_index(safe_name, index_line)
    # ...
    return timestamp
```

- [ ] **Step 2: Self-healing — verify_index() checks for broken links**

Add method to MemoryManager:

```python
async def verify_index(self) -> list[dict]:
    """Check MEMORY.md for broken pointers. Returns list of broken entries."""
    broken = []
    entries = self._parse_index()
    for e in entries:
        fname = e.get("filename", "")
        # Check if it's a pointer (looks like a path with directories)
        if "/" in fname or fname.startswith("0"):
            from pathlib import Path as _Path
            # Try to read rules for base path
            base = Path.home() / "Desktop" / "学习笔记"
            full_path = base / fname
            if not full_path.exists():
                broken.append({**e, "expected_path": str(full_path)})
    return broken
```

- [ ] **Step 3: Commit**

```bash
git add agent/memory/manager.py
git commit -m "feat(memory): add note pointers and self-healing index verification"
```

---

### Task 4: Error tracker module

**Files:**
- Create: `agent/memory/error_tracker.py`

New module for error classification, dedup, and recipe matching.

- [ ] **Step 1: Create error_tracker.py**

```python
"""Error tracking with L1/L2/L3 classification + recipe matching."""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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
    for pat in L3_PATTERNS:
        if pat.lower() in error_text.lower():
            return L3_FATAL
    for pat in L2_PATTERNS:
        if pat.lower() in error_text.lower():
            return L2_SELF_HEAL
    for pat in L1_PATTERNS:
        if pat.lower() in error_text.lower():
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
        """Generate dedup key from error text (first 80 chars, cleaned)."""
        import re
        clean = re.sub(r'[0-9a-f]{8,}', '<ID>', error_text)  # strip IDs
        clean = re.sub(r'https?://\S+', '<URL>', clean)       # strip URLs
        return clean[:80]

    def should_report(self, error_text: str) -> tuple[bool, int]:
        """Check if error should be reported. Returns (should_report, level).
        
        Dedup: same error within 5 minutes is silent.
        """
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

        # Pattern detection: 3+ of same type → suggest root cause fix
        if self._counts[key] >= 3:
            return True, level  # triggers pattern analysis

        return True, level

    def save_recipe(self, error_text: str, fix_description: str):
        """Save a successful fix recipe for future matching."""
        key = self._key(error_text)
        self._recipes[key] = fix_description
        # Append to error_log.md
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        entry = f"\n## {ts}\n**错误**: {error_text[:200]}\n**修复方案**: {fix_description}\n"
        with open(self.error_log, "a", encoding="utf-8") as f:
            f.write(entry)

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
        # Simple parse: look for ## date / **错误** / **修复方案** blocks
        try:
            content = self.error_log.read_text(encoding="utf-8")
            import re
            blocks = re.findall(
                r'\*\*错误\*\*: (.+?)\n\*\*修复方案\*\*: (.+?)(?:\n##|\n\Z)',
                content, re.DOTALL
            )
            for err, fix in blocks:
                key = self._key(err.strip())
                self._recipes[key] = fix.strip()
                self._counts[key] = 1  # mark as known
            logger.info(f"Loaded {len(blocks)} error recipes from log")
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add agent/memory/error_tracker.py
git commit -m "feat(error): add error tracker with L1/L2/L3 classification and recipe matching"
```

---

### Task 5: Integrate error tracker into agent core loop

**Files:**
- Modify: `agent/core.py:82-117` (Agent.__init__)
- Modify: `agent/core.py:340-395` (tool execution loop)

Wire ErrorTracker into the Agent's tool execution — catch errors, classify, retry or escalate.

- [ ] **Step 1: Initialize ErrorTracker in Agent.__init__**

In `agent/core.py`, add to imports and __init__:

```python
# Add import at top
from .memory.error_tracker import ErrorTracker, L1_TRANSIENT, L2_SELF_HEAL, L3_FATAL

# In __init__, after line 117 (_task_start_time):
self.error_tracker = ErrorTracker()
```

- [ ] **Step 2: Add self-heal logic in tool execution**

In `agent/core.py`, around the tool dispatch (line ~360-395), wrap with error handling:

```python
# Before tool dispatch, add:
tool_error = None
retry_count = 0
max_retries = 2

while retry_count <= max_retries:
    try:
        async for result in self.registry.dispatch(tool_name, tool_args, context=self):
            error_text = result.get("data", "")
            if "Error:" in error_text or "失败" in error_text or "fail" in error_text.lower():
                tool_error = error_text
                break
            yield result
        if not tool_error:
            break  # success, exit retry loop
    except Exception as e:
        tool_error = str(e)

    if tool_error:
        should_report, level = self.error_tracker.should_report(tool_error)
        
        if level == L1_TRANSIENT and retry_count < max_retries:
            retry_count += 1
            await asyncio.sleep(3 * retry_count)  # progressive backoff
            continue
        
        if level == L2_SELF_HEAL:
            recipe = self.error_tracker.find_recipe(tool_error)
            if recipe:
                yield {"type": "self_heal", "content": f"自动修复: {recipe}"}
                # bot can now use the recipe to fix
        
        if should_report or retry_count >= max_retries:
            yield {
                "type": "error_report",
                "level": level,
                "content": f"工具 {tool_name} 失败: {tool_error}",
                "retries": retry_count,
            }
            # Save for pattern analysis
            if level != L1_TRANSIENT:
                self.error_tracker.save_recipe(tool_error, f"auto-classified as L{level}")
        break
```

- [ ] **Step 3: Load error recipes at Agent startup**

In `Agent.run()`, after loading session history, also load error recipes:

```python
# After line ~142 (_history_loaded block)
await self.error_tracker.load_recipes()
```

- [ ] **Step 4: Commit**

```bash
git add agent/core.py
git commit -m "feat(core): integrate error tracker with retry and self-heal logic"
```

---

### Task 6: Wire gateway — inject error_log into memory context

**Files:**
- Modify: `agent/memory/manager.py:50-75` (load_context)

Ensure error_log.md patterns are included in the memory block injected each turn.

- [ ] **Step 1: Add recent errors to load_context output**

In `agent/memory/manager.py`, in `load_context()` method, append recent error patterns:

```python
# At end of load_context(), before the final return:
error_content = ""
error_log = self.base_dir / "error_log.md"
if error_log.exists():
    try:
        recent_errors = error_log.read_text(encoding="utf-8")
        # Take last 2000 chars (most recent)
        recent = recent_errors[-2000:] if len(recent_errors) > 2000 else recent_errors
        error_content = f"\n\n## 错误配方库\n{recent}\n"
    except Exception:
        pass

return f"\n\n{content}\n{error_content}"
```

- [ ] **Step 2: Commit**

```bash
git add agent/memory/manager.py
git commit -m "feat(memory): inject error recipes into memory context block"
```

---

### Task 7: Integration test — verify full flow

**Files:**
- No new files, manual verification

- [ ] **Step 1: Restart gateway**

```bash
pkill -f "main.py --gateway" && sleep 1
nohup venv/bin/python main.py --gateway >> gateway.log 2>&1 &
sleep 3
ps aux | grep "[m]ain.py --gateway"
```

- [ ] **Step 2: Verify routing_rules exists**

```bash
cat ~/.my-agent/memory/routing_rules.md
```

- [ ] **Step 3: Send a test message to bot on QQ**

"小萤你之前的错误日志能读到吗？"

- [ ] **Step 4: Check gateway log for error-free operation**

```bash
tail -20 gateway.log
```

- [ ] **Step 5: Commit any remaining changes**

```bash
git status
git add -A
git commit -m "feat(memory): complete memory system redesign with error tracking"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Compressor auto-save | `compressor.py` |
| 2 | Routing rules + save_to_notes | `manager.py` |
| 3 | Note pointers + index self-healing | `manager.py` |
| 4 | ErrorTracker module | `error_tracker.py` (new) |
| 5 | Error loop in agent core | `core.py` |
| 6 | Error recipes in memory context | `manager.py` |
| 7 | Integration test | manual |
