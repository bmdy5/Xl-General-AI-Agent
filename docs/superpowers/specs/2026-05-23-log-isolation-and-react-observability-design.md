# System Design: Log Isolation and ReAct Observability Alignment

## 1. Background

During the decoupling and refactoring of the gateway, several critical features from the monolithic `gateway.py` were accidentally omitted when moving logic to `agent/net_gateway/bot.py` and `agent/net_gateway/dispatcher.py`. 
These missing components include:
- Capturing `tool_call` and `tool_result` events from the agent flow for logs.
- Capturing `permission_request` events to prompt for administrative authorization on critical operations.
- Intercepting other helper events like `exploring_start` and logging errors.
- Disambiguating the logs of the administrator (Liang Ge) from ordinary noise (non-admin private chats, other group chats).

Additionally, a vital bug was found in the memory subsystem: when a merged/new Knowledge Item (KI) is retrieved by RAG as a virtual `ki_xxx.md` file, the memory manager attempts to read it from the local filesystem where it does not exist. This results in the agent only seeing the virtual filename/link and completely losing the actual content of the merged KI.

This specification designs a robust isolation of main-branch logs (Liang Ge's sessions) from bypass sandbox logs (co-worker/group chat noise), resolves the KI content retrieval bug, and completely restores ReAct observability.

## 2. Goals & Success Criteria

### Goals
- Achieve pure physical isolation between `agent_activity.log` (Liang Ge's private logs and customized whitelist group events) and `coworker_activity.log` (ordinary sandbox logs and bypass traffic).
- 100% align dispatcher ReAct events (`exploring_start`, `tool_call`, `tool_result`, `permission_request`, `error`) to restore high-fidelity activity logging.
- Fix the KI content retrieval bug inside `get_entry()` so that the agent can read SQLite knowledge item content successfully via the `ki_xxx.md` filename pointer.

### Success Criteria
- Automated test suites (`pytest tests/`) pass with zero regressions.
- Merged KI content is successfully embedded inside RAG memory block contexts.
- `agent_activity.log` exclusively contains Liang Ge's high-purity conversation trail and related ReAct telemetry.
- `coworker_activity.log` cleanly archives all third-party interactions and their tool logs.

## 3. Proposed Changes

### Memory Subsystem

#### [MODIFY] [manager.py](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/memory/manager.py)
In `get_entry(self, filename: str)`:
- Check if `filename` matches the format `ki_*.md` (e.g. starts with `"ki_"` and ends with `".md"`).
- If so, parse out the `ki_id` (strip the prefix and suffix), retrieve the Knowledge Item via `self.get_ki(ki_id)` from the SQLite database, and return the `content`.
- Otherwise, fall back to reading the file from the local filesystem.

```python
    async def get_entry(self, filename: str) -> Optional[str]:
        """Read memory file content."""
        if filename.startswith("ki_") and filename.endswith(".md"):
            ki_id = filename[3:-3]
            ki_data = self.get_ki(ki_id)
            if ki_data:
                return ki_data.get("content", "")
            return None
            
        safe_name = filename.replace("/", "_").replace("\\", "_").replace(" ", "_")
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        topic_file = self.base_dir / safe_name
        if topic_file.exists():
            return topic_file.read_text(encoding="utf-8")
        return None
```

---

### Gateway Subsystem

#### [MODIFY] [bot.py](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/net_gateway/bot.py)
Modify `_log_activity(self, category: str, content: str, user_id: str = None)`:
- Accept an optional `user_id` parameter.
- Define a separate bypass log file path: `/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/coworker_activity.log`.
- If `user_id` is provided and is equal to `self.admin_id` (i.e., `"1705919142"`):
  - Append the log to `self._activity_log_path` (`agent_activity.log`).
- Otherwise (if it is a coworker or unspecified):
  - Append the log to `/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/coworker_activity.log`.

```python
        self._activity_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent_activity.log"
        self._bypass_log_path = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/coworker_activity.log"
```

```python
    def _log_activity(self, category: str, content: str, user_id: str = None):
        """结构化轨迹活动日志记录，支持按发言人身份进行物理文件隔离分流"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = content
        for pattern in [r"(?i)token[s]?'?\s*:\s*'[^']+'", r"(?i)key[s]?'?\s*:\s*'[^']+'"]:
            safe_content = re.sub(pattern, "token: '******'", safe_content)
        
        if len(safe_content) > 1000:
            safe_content = safe_content[:1000] + " ... (truncated)"
        
        log_line = f"{now} | [{category}] | {safe_content}\n"
        
        # 物理路由判定：亮哥的交互打入主日志，其他的全数归入旁路沙箱日志
        target_path = self._activity_log_path
        if user_id is not None:
            if str(user_id) != str(self.admin_id):
                target_path = self._bypass_log_path
        
        try:
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error(f"Failed to write activity log: {e}")
```

#### [MODIFY] [dispatcher.py](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/net_gateway/dispatcher.py)
In `_execute_agent_run(self, agent, raw: str, session_key: str, msg_type: str, user_id: str, group_id: str, sender_name: str, task_start_time: float)`:
- Stream and capture all events emitted by `agent.run()`.
- Align and log details via `self._log_activity_dispatcher` passing the current `user_id`.
- Capture `exploring_start`: log `"AI 计划/答复"`, `"思考启动..."`.
- Capture `tool_call`:
  - Log `"工具调用"`, `f"{tool_name} | 参数: {tool_args}"`.
  - In addition, if `buf.strip()` contains pending text, log the current `buf.strip()` under category `"AI 计划/答复"`, flush `buf = ""`, and send any pending text chunk before calling the tool to prevent user waiting.
- Capture `tool_result`:
  - Log `"工具返回"`, `f"{tool_name} | 结果大小: {len(result_str)} 字节"`.
  - Handle tool failures by writing under category `"系统异常"` and warning the user.
- Capture `permission_request`:
  - If `user_id` equals `self.admin_id` (Liang Ge):
    - Send an authorization message to QQ: `🔧 需要执行以下操作：...`.
    - Block and wait up to 120s for Liang Ge to reply `允许/y/yes/ok/好/可以`.
    - Grant or deny permission to the agent based on the user's input.
  - If `user_id` is a coworker:
    - Automatically deny any write/dangerous permission requests at the gateway level.
    - Log under `"系统安全拦截"`.
- Modify `_log_activity_dispatcher(self, category: str, content: str, user_id: str = None)`:
  - Propagate `user_id` parameter to the bot's logging helper: `self.bot._log_activity(category, content, user_id)`.

## 4. Verification Plan

### Automated Tests
- Run `pytest tests/` to confirm that all existing concurrency, preemption, private recovery, and fatigue tests remain 100% compliant.
- Ensure the newly modified logging system does not introduce any lock contention or thread blockages.

### Manual Verification
- Simulate a co-worker private message using `simulate_message.py` and verify that the logs are purely appended to `coworker_activity.log`.
- Send a direct message as admin (Liang Ge), perform a search or memory operation, and ensure the tool call logs, thinking plan, and voice events are perfectly chronologically interwoven inside `agent_activity.log`.
- Test permission intercept flow to verify the QQ authorization card blocks and approves correctly.
