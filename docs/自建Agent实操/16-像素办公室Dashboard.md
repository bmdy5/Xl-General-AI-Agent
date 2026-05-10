---
title: 阶段 9：像素办公室 Dashboard
date: 2026-05-10
tags:
  - agent
  - dashboard
  - pixel-art
  - visualization
type: 实操
---

# 像素办公室 Dashboard

> 老肖 | 2026-05-10

Canvas 像素风办公室，俯视 RPG 视角。可以看到 agent 在房间里走路、搬桌椅、搜索、辩论。

---

## 1. 架构

```
浏览器 (Canvas) ←─SSE── Python HTTP Server ←── Agent 事件流
     │                    │                      │
  dashboard.html      dashboard.py         core.py / auto_learn.py
  (600行 Canvas)       (100行 HTTP+SSE)     (yield events)
```

## 2. 文件

| 文件 | 作用 | 行数 |
|------|------|------|
| `agent/dashboard.py` | HTTP server + SSE 推送 | ~100 |
| `agent/dashboard.html` | Canvas 渲染引擎，单文件零依赖 | ~300 |
| `start_dashboard.sh` | 一键启动脚本 | ~30 |
| `main.py` | `--dashboard` / `--dashboard-learn` 入口 | +50 |

## 3. 房间布局

Canvas 960×600 像素画布，5 个房间：

```
┌────────────┬────────────┬────────────┬────────────┐
│  电脑房      │  图书室      │  XL办公室    │  杂物室      │
│  4台电脑桌   │  3排书架+2桌 │  大桌+3屏    │  桌椅仓库     │
│  web搜索     │  本地文件     │  XL坐镇      │  agent搬      │
├────────────┴────────────┴────────────┴────────────┤
│                    走廊                            │
├──────────────────────────────────────────────────┤
│                    会议室                           │
│             圆桌 + 6椅 + 评审席                      │
└──────────────────────────────────────────────────┘
```

## 4. 像素精灵

16×16 像素角色，纯 Canvas fillRect 绘制：

- **XL Agent**: 金色 `#f4d058` + 红色披风
- **Learner**: 蓝色 `#4ba4e0`
- **激进派**: 红色 `#e04b4b`
- **保守派**: 绿色 `#4be05a`
- **魔鬼**: 紫色 `#a04be0`
- **评审**: 灰色 `#8b8b9b`

每个角色：身体(8×10) + 头部(6×8) + 眼睛(2×2×2)

## 5. Agent 状态机

```
idle → walk_to_target → action(work/debate/score) → idle
```

- 位置：`{x, y}` 当前坐标，`{tx, ty}` 目标坐标
- 移动：lerp 插值 `x += (tx - x) * 0.08`
- 动画：走路 2 帧上下微动，工作时头顶 `...` 闪烁

## 6. SSE 事件协议

后端 `dashboard.send({"agent": id, "event": type, ...})` → 前端 `handleEvent()`

### 事件类型

| event | 前端行为 |
|-------|---------|
| `search_start` / `web_search` | 移动到电脑房，坐下，屏幕闪烁 |
| `read_local` | 移动到图书室，书桌 |
| `fetch_article` | 工作态 + "阅读文章..." 气泡 |
| `extract_done` | "✅ 提取完成" 气泡 |
| `enter_debate` | 移动到会议室 |
| `critique` | "质疑!" 气泡 |
| `rebut` | "反驳!" 气泡 |
| `score` | "评分:X" 气泡 |
| `save_memory` | "💾 存记忆" + 移动到 XL 办公室 |
| `learn_done` | 回到 idle |
| `periodic_nudge` | XL 头顶 "💡 检查记忆" |

### JSON 格式

```json
{"agent": "learner-1", "event": "search_start", "name": "🌐搜:Python"}
{"agent": "debater-a", "event": "enter_debate", "name": "激进"}
{"agent": "reviewer", "event": "score", "action": "8.5"}
```

## 7. 调色板

```javascript
floor:'#2a2a3a', wall:'#3d3d55', door:'#5a4a3a',
furniture:'#d4c8a0', deskTop:'#c4b890', chair:'#b09860',
screenOn:'#4ba4e0', screenOff:'#1a1a2a',
xlGold:'#f4d058', learnBlue:'#4ba4e0',
debateRed:'#e04b4b', debateGreen:'#4be05a',
devilPurple:'#a04be0', reviewGray:'#8b8b9b',
```

## 8. 使用方法

```bash
# 交互模式 + Dashboard
./start_dashboard.sh
# 浏览器 http://localhost:8765

# 自主学习 + Dashboard
python main.py --dashboard-learn

# 清理端口
lsof -ti:8765 | xargs kill -9
```

## 9. 给维护 AI 的说明

### 加新房间
在 `ROOMS` 对象加条目，在 `render()` 加 `drawRoom()` 调用。

### 加新角色
在 `handleEvent()` 加 `spawnAgent()` 调用，指定颜色和初始房间。

### 加新事件
在 `handleEvent()` 的 switch 加 case，调用 `moveToRoom()` 或设置 `a.bubble`。

### 改布局
修改 `ROOMS` 的 x/y/w/h，`DESKS`/`CHAIRS`/`SHELVES` 的坐标，`drawDoor()` 的位置。

### Canvas 性能
10 个 agent + 60fps 没问题。20+ 降到 30fps。agent 离开后从 `agents` 对象删除以释放。

### 零外部依赖
dashboard.html 无 npm/webpack/CDN。纯 HTML+Canvas+SSE。任何现代浏览器直接打开。

### 路径
- `agent/dashboard.py` 中的 `HTML_PATH` 指向 `agent/dashboard.html`
- 如果 dashboard.py 被移动，更新 `HTML_PATH` 或在 `start()` 中加 fallback 路径
