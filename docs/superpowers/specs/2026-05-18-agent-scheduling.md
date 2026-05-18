# Agent Self-Scheduling + Auto-Execution

2026-05-18 | Status: draft

## Problem

Bot 的定时任务硬编码在代码里（"清理旧会话"等），Agent 自己不能创建/管理定时任务。Agent 应该在需要时自己决定"10 分钟后重试"、"每天检查日志"等。

## Design: `schedule_task` Tool

### Tool Definition

```
schedule_task(action, description, run_at, task, auto_execute)
```

| Param | Type | Description |
|-------|------|-------------|
| action | enum | `add` / `list` / `cancel` / `done` |
| description | string | 简短描述，用于列表展示 |
| run_at | string | 自然语言时间："in 10 min" / "tomorrow 9am" / "daily" / "every Mon 8am" |
| task | string | 要执行的任务描述（给 agent 自己看的 prompt） |
| auto_execute | bool | 是否到期自动执行（不需要用户确认） |

### Time Parsing

自然语言时间 → 内部 cron/epoch 转换：
- `"in 10 minutes"` → `now + 600s`
- `"tomorrow 9am"` → 明天 09:00 的 epoch
- `"daily"` → cron `0 9 * * *`
- `"every Monday 8am"` → cron `0 8 * * 1`

### Auto-Execute Flow

```
Agent 创建任务时:
  → auto_execute=false → 到期时 QQ 推送确认请求
  → auto_execute=true → 到期时自动执行

Agent 可以问你: "这个任务以后每天都跑，不需要再问你，可以吗？"
你同意 → Agent 调用 schedule_task(action="add", auto_execute=true, ...)
```

### Result Delivery

```
任务执行完成:
  → NapCat 在线（QQ Bot 登录中）
    → 推送结果到 QQ: "✅ 定时任务「XX」完成：[结果摘要]"
  → NapCat 离线
    → 静默写入学习笔记

  → 无论是否在线
    → 结果存档到学习笔记对应目录（agent 根据 routing_rules 自己判断位置）
    → 目录不存在则创建
```

### Safety Guards

- **去重**: 同 description + 同 run_at 的任务不重复创建
- **上限**: 每人最多 20 个待执行任务
- **审计**: 所有任务执行记录存在 task_queue 日志中

## Files To Change

### 1. `agent/tools/schedule_task_tool.py` (new)
- Tool definition + input validation + time parsing
- Calls TaskQueue.add/list/cancel/done

### 2. `agent/task_queue.py` (modify)
- Add `parse_natural_time()`: "in 10 min" → epoch
- Add `add(..., auto_execute: bool)` field
- Add dedup check
- Add max pending limit (20)

### 3. `agent/gateway.py` (modify)
- Daemon loop: support auto-execute (skip permission prompt)
- Task completion: push to QQ + save to notes
- Remove hardcoded tasks, let agent self-initialize

### 4. Hardcoded task cleanup
- Remove any code that creates fixed tasks
- Agent creates its own tasks on first run

## Non-Goals
- No recurring task editing (cancel + re-add is sufficient)
- No distributed task queue (single process is fine)
