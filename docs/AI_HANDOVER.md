# XL Agent 项目架构与开发者交接文档

> 系统变更必须同步更新本文档。

---

## 1. 项目目录结构

```text
Xl-General-AI-Agent/
├── agent/                      <-- Python 源码
│   ├── core/                   <-- 核心：Agent状态机、ReAct循环、LLM、Prompt构建
│   │   ├── agent.py            <-- Agent 类
│   │   ├── bootstrap.py        <-- 系统组装、工具注册、日志初始化
│   │   ├── config.py           <-- 配置加载 (settings.yaml)
│   │   ├── compressor.py       <-- Token 计数器 (压缩已禁用)
│   │   ├── llm.py              <-- LLM 客户端 (LiteLLM)
│   │   ├── paths.py            <-- 全局路径常量
│   │   ├── prompt_builder.py   <-- System Prompt 构建
│   │   ├── react_loop.py       <-- ReAct 循环 (缓存、疲劳、死锁熔断)
│   │   ├── history_repair.py   <-- JSON 修复 + 工具调用回收
│   │   ├── visual_agent.py     <-- Playwright CDP 视觉引擎
│   │   └── cleanup.py, gateway.py
│   ├── memory/                 <-- 记忆系统 (SQLite + FTS5 + 向量)
│   │   ├── manager.py          <-- MemoryManager 主控
│   │   ├── index.py            <-- DB 初始化/DDL/自愈
│   │   ├── ki.py               <-- KI CRUD
│   │   ├── context.py          <-- search_memories (FTS5+向量+RRF)
│   │   ├── embedding.py        <-- 向量提取 (m3e-base/OpenAI)
│   │   ├── fts_index.py        <-- FTS5 索引
│   │   ├── store.py            <-- GC 碎片蒸馏
│   │   ├── cache.py            <-- 语义缓存 LRU
│   │   └── session.py, migration.py, notes_fts.py
│   ├── evolution/              <-- 进化引擎
│   │   ├── dream.py            <-- 做梦/记忆蒸馏/KI融合
│   │   ├── base.py             <-- Session 结束反思
│   │   ├── memory.py           <-- 偏好/过滤
│   │   ├── rules.py            <-- 自学习规则
│   │   ├── sop.py              <-- 任务模式检测
│   │   ├── traces.py           <-- 工具调用追踪
│   │   └── dream_prompts.py    <-- 做梦 Prompt 常量
│   ├── net_gateway/            <-- QQ 网关
│   │   ├── bot.py              <-- QQ Bot (NapCat WebSocket)
│   │   ├── executor.py         <-- Agent 推理调度
│   │   ├── dispatcher.py       <-- 消息分发路由
│   │   ├── fatigue_manager.py  <-- 网关层疲劳/休眠管理
│   │   ├── scheduler.py        <-- TTS 守护 + 定时任务
│   │   ├── sender.py           <-- QQ 消息发送
│   │   ├── presenter.py        <-- 流式回复渲染
│   │   ├── security.py         <-- 安全拦截
│   │   └── middleware/         <-- 消息中间件管道 (8个)
│   ├── tools/                  <-- LLM 可调用工具 (25个)
│   │   ├── filesystem/         <-- read/write/edit/bash
│   │   ├── agent/              <-- swarm/spawn/schedule/sequence
│   │   ├── meta/               <-- manage/memory/organize
│   │   ├── web/                <-- search/fetch
│   │   ├── media/              <-- image_gen/read/send
│   │   ├── qq/                 <-- status/send_message
│   │   ├── mcp/                <-- stitch/notebooklm/xiaohongshu/client
│   │   ├── visual_tools.py     <-- BrowserAgentTool
│   │   └── registry.py         <-- 工具注册表
│   ├── skills/                 <-- 技能管理代码
│   ├── session/                <-- 会话 JSONL 持久化
│   ├── runner/                 <-- 启动模式 (gateway/interactive/single)
│   ├── learn/                  <-- 自动学习/播客
│   ├── ui/                     <-- TUI 终端界面
│   └── voice/                  <-- TTS 语音合成
├── config/
│   └── settings.yaml           <-- 中央配置
├── agent_memory/               <-- 记忆数据 (四叶结构)
│   ├── core/                   <-- 核心数据库 (gitignored)
│   ├── experiences/            <-- 已废弃 (数据迁入 DB)
│   ├── skills/                 <-- 技能文件 (仅 自学习技能/)
│   └── context/                <-- coworker 记忆
├── logs/
│   ├── chat.log                <-- 对话+工具+Token (50MB×10)
│   ├── system.log              <-- 网关/错误/自愈 (5MB×10)
│   └── startup.log             <-- 启动脚本输出
├── tests/                      <-- pytest 测试
├── main.py                     <-- 唯一启动入口
├── Makefile
└── Dockerfile & docker-compose.yml
```

---

## 2. 核心架构

### 2.1 人格系统

不再硬编码。人格来源分为两层：

| 文件 | 内容 | 维护者 |
|------|------|--------|
| `core.md` | 系统事实 (OS/QQ号/路径/API) | 用户 |
| `routing_rules.md` | 性格/行为/偏好/自学习 | 小萤自行演化 |

两个文件位于 `~/.my-agent/memory/1705919142/`，每次对话注入 System Prompt。`default_persona.json` 已精简为仅 name/gender/user_address。

### 2.2 记忆系统

统一存储在 `memories.db` → `knowledge_items` 表，FTS5 + 768维向量混合检索。

| 表 | 内容 |
|----|------|
| `knowledge_items` | KI 条目 (含 ki_type/version/revision_history) |
| `ki_embeddings` | 向量 |
| `kis_fts` | FTS5 全文索引 |
| `skill_usage` | 技能使用统计 |

### 2.3 三级进化链

```
对话 → 做梦提炼 → KI (ki_type='ki')
                    │ visit>=5 + success>=80%
                    ↓
                  Experience (ki_type='experience')
                    │ visit>=10 + success>=90%
                    ↓
                  Skill (ki_type='skill')
```

晋升 = `UPDATE ki_type`。手动优先于自动。不再使用 .md 文件。

### 2.4 缓存架构

DeepSeek 自动前缀缓存。核心原则：
- `agent.messages` 在 ReAct 循环中保持静态
- 动态上下文合并到 `system[0].content` 末尾
- 时间戳使用 `%Y-%m-%d` (日期粒度，当日全部命中)
- 绝不压缩 (compressor 已删除)

### 2.5 疲劳机制

Token > 64K (调试时 100K) → 注入疲劳提示到 System Prompt → 模型自己提出休眠 → Session 结束触发 dreaming → 记忆蒸馏到 SQLite。全程不破坏缓存。

### 2.6 日志系统

双文件 + logger name 前缀路由：

| 文件 | 路由规则 | 内容 |
|------|----------|------|
| `chat.log` | `agent.activity.gateway`, `agent.react_loop`, `net_gateway.logger` | 对话+工具+Token |
| `system.log` | 其余全部 | 网关/错误/自愈 |

JSONL 永久存档: `~/.my-agent/sessions/`

---

## 3. 关键约束

### 3.1 导入路径
- `gateway.py` → `net_gateway` 必须用绝对导入: `from agent.net_gateway.bot import QQGateway`
- 人设路径: `Path(__file__).resolve().parents[1] / "resources" / "default_persona.json"`
- 路径常量统一从 `agent.core.paths` 导入

### 3.2 缓存约束
- 缓存命中率不得低于 80%
- `agent.messages` 在 ReAct 中途不允许修改
- 时间戳在 `run_loop` 入口锁定，循环内不得重复取

### 3.3 不硬编码原则
- 性格/行为/偏好 → `routing_rules.md` (小萤自行演化)
- 系统事实 → `core.md` (用户维护)
- STATIC_PROMPT 只保留技术规范，不超过 20 行

---

## 4. 核心机制

| 机制 | 位置 | 说明 |
|------|------|------|
| 死锁熔断 | react_loop.py | 同工具同参数连续 ≥3 次触发 |
| JSON 修复 | history_repair.py | 截断参数自动补全 (写入工具禁止修复) |
| 工具回收 | history_repair.py | 从思考流中提取泄露的 tool call |
| GC 蒸馏 | store.py | 仅匹配 reflect_/audit_ 前缀碎片 |
| 做梦引擎 | dream.py | KI 提炼/合并/熔炼 |
| 疲劳管理 | react_loop.py + fatigue_manager.py | LLM层 + 网关层 |
| 笔记路由 | routing_rules.md | save_to_notes 读取 |

---

## 5. 配置

### settings.yaml 关键项
- `memory.base_dir`: `~/.my-agent/memory`
- `memory.backup_dir`: `./agent_memory/core`
- `memory.multi_instance_isolation`: `true`
- `knowledge_base.notes_paths`: `~/Documents/学习笔记`
- `thresholds`: 技能/经验/TTS 阈值 (10项)

### 端口
- Brain HTTP: 8000
- NapCat WS: 3001
- NapCat HTTP: 3020
- TTS: 9880

### 启动
```bash
make gateway-restart
tail -f logs/chat.log    # 看对话
tail -f logs/system.log  # 看系统
```

---

## 6. 变更记录

### 2026-05-27/28 架构精简与会话优化

**缓存修复**
- 时间戳 %H:%M → %Y-%m-%d (当日缓存命中 85%+)
- 动态上下文合并到 system[0]，消除前缀抖动
- Compressor 彻底删除 (230→23行)

**Token 优化**
- max_tokens: 16384→4096, max_turns: 40→20
- bash 非分析型命令只返 exit code
- skills/experiences 只注入目录索引
- STATIC_PROMPT: 52→19行，省 ~1500 token/次
- Prompt 总量: 7300→1880 字符

**死代码删除 (~4500行)**
- 删除 15+ 文件: coach.py, tester.py, apply.py, douyin_browser.py, duoagent/, media/, 5 tool stubs, tool_decorator.py, fatigue.py
- migration.py: 204→8行 no-op

**架构精简**
- 视觉工具 5→1, 中间件 11→8
- 记忆统一到 knowledge_items 表 (ki_type 字段), 删除 75 个 .md 文件
- 备份只同步 .db 文件
- _cjk_space 3处重复 → 统一到 agent.memory
- _sync_environ_keys 移除 pop() 避免并发竞态

**人格系统重构**
- 移除 default_persona.json 中的硬编码性格
- 新增 core.md (系统事实) + routing_rules.md (自演化规则)
- 每次注入，小萤自行维护 routing_rules.md

**进化链**
- KI→Experience→Skill 三级晋升，ki_type 字段驱动
- 新知识统一从 ki_type='ki' 起步
- 晋升逻辑: visit_count + success_rate 自动晋升 + 手动升级
