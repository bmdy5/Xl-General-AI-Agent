# XL-General-AI-Agent 全栈架构解耦与微服务化重构设计方案

> 版本: v1.0 | 日期: 2026-05-23 | 作者: 架构审计委员会
>
> 基于 `refactor/architecture-decouple` 分支第二阶段审查——从 net_gateway 扩展至全项目 49 个 Python 模块。

---

## 一、项目全景现状

### 1.1 规模概览

| 维度 | 数据 |
|------|------|
| Python 文件总数 | 49（不含 scratch/test） |
| 代码总行数 | ~12,800 行 |
| 超大文件 (>500行) | 7 个 |
| 工具模块 | 25 个 |
| 子包数量 | 5（net_gateway, tools, memory, session, duoagent） |
| MCP 服务 | 5 个（stitch, xiaohongshu, notebooklm, mcp_client, agent_learning） |

### 1.2 当前架构图

```
┌──────────────────────────────────────────────────────────────┐
│                        main.py (562行)                        │
│  CLI入口 + 6种运行模式 + 22个工具注册                          │
└──────────────┬───────────────────────────────────────────────┘
               │
     ┌─────────▼─────────┐
     │  agent/core.py    │  1240行 — 核心Agent类
     │  Agent.run()      │  系统提示词 + 工具循环 + 权限审批
     │  _run_loop()      │  + 记忆注入 + 上下文压缩 + 滑动窗口
     │  _build_system()  │  + 历史修复 + 错误分类 + 意图锁定
     └───┬───────┬───────┘
         │       │
    ┌────▼──┐ ┌──▼────────────┐
    │  llm  │ │  memory/      │  1174行 — MemoryManager
    │ 258行 │ │  manager.py   │  记忆CRUD + FTS5 + Embedding + KI
    └───────┘ │  fts_index.py │  会话集成 + 上下文检索
              │  notes_fts.py │
              │  error_trk.py │
              └───────────────┘
         │
    ┌────▼──────────┐  ┌──────────────┐  ┌────────────────┐
    │  evolution.py │  │  compressor  │  │  session/      │
    │  874行        │  │  235行       │  │  handler.py    │
    │  8大模式函数   │  │  ContextComp │  │  360行         │
    └───────────────┘  └──────────────┘  └────────────────┘
         │
    ┌────▼──────────────────────────────────────┐
    │            agent/tools/ (25文件/4975行)      │
    │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │
    │  │MCP Tools │ │Web Tools │ │Agent Tools │  │
    │  │stitch    │ │fetch     │ │spawn       │  │
    │  │xhs       │ │search    │ │swarm       │  │
    │  │notebooklm│ │image     │ │schedule    │  │
    │  │mcp_client│ │read_img  │ │run_seq     │  │
    │  └──────────┘ └──────────┘ └────────────┘  │
    │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │
    │  │File Tools│ │QQ Tools  │ │Meta Tools  │  │
    │  │bash      │ │qq_status │ │manage_tool │  │
    │  │edit_file │ │send_qq   │ │memory      │  │
    │  │file_r/w  │ │          │ │            │  │
    │  └──────────┘ └──────────┘ └────────────┘  │
    └────────────────────────────────────────────┘
         │
    ┌────▼──────────────────────────────────────────┐
    │        agent/net_gateway/ (13文件/~2000行)      │
    │  ┌────────┐ ┌──────────┐ ┌────────────────┐  │
    │  │bot 274 │ │dispatcher│ │executor  221   │  │
    │  │sender  │ │  341     │ │presenter  91   │  │
    │  │logger  │ │carrier   │ │security   82   │  │
    │  │sched   │ │fatigue   │ │tts      281   │  │
    │  └────────┘ └──────────┘ └────────────────┘  │
    └──────────────────────────────────────────────┘
```

### 1.3 问题热力图

```
文件规模 (行数)          耦合程度             解耦优先级
─────────────────────────────────────────────────────
core.py        1240 ████  与所有模块静态导入  🔴 P0
memory/mgr.py  1174 ████  一个类1075行        🔴 P0
evolution.py    874 ███▌  强耦合memory私有API  🟡 P1
main.py         562 ███   工具注册+6种模式      🟡 P1
dispatcher.py   341 ██▌   11步单块方法         🟢 P2 (已部分解耦)
auto_podcast    510 ██▌   独立但路径硬编码      🟢 P2
auto_learn      493 ██▌   辩论机制过长         🟢 P3
tools/*        4975 ██▌   散落无分组           🟡 P1
```

---

## 二、目标架构设计

### 2.1 核心原则

1. **单文件单职责**：每个 .py 文件不超过 400 行，每个类不超过 200 行
2. **显式接口**：模块间通过 Protocol/ABC 通信，杜绝 `hasattr` 探路
3. **可插拔**：新功能 = 新文件 + 注册，不修改已有文件
4. **独立可测**：每个模块可 mock 依赖独立运行 pytest
5. **配置集中**：消除硬编码路径，统一到 config 层

### 2.2 目标架构全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                         config.py (NEW)                         │
│  统一配置中心: 路径/密钥/模型/白名单/TTL/开关                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                      bootstrap.py (NEW)                         │
│  依赖注入容器: 按需组装 LLM/Memory/Session/Tools/Pipeline           │
└───┬─────────┬──────────┬──────────┬──────────┬─────────────────┘
    │         │          │          │          │
┌───▼──┐ ┌───▼───┐ ┌───▼───┐ ┌───▼───┐ ┌───▼──────────────────┐
│ LLM  │ │Memory │ │Session│ │Evolve │ │  Tools Layer          │
│ 258  │ │ Layer │ │ 360   │ │ Layer │ │                       │
│      │ │       │ │       │ │       │ │ ┌───────────────────┐ │
│      │ │ store │ │       │ │ coach │ │ │ MCP Subsystem     │ │
│      │ │ index │ │       │ │ tester│ │ │  mcp/ (NEW PKG)   │ │
│      │ │ ki    │ │       │ │ traces│ │ │  ┌─────────────┐  │ │
│      │ │ fts5  │ │       │ │ apply │ │ │ │ client.py   │  │ │
│      │ │ notes │ │       │ │ rules │ │ │ │ stitch.py   │  │ │
│      │ └───────┘ │       │ └───────┘ │ │ │ xhs.py      │  │ │
│      │   ↑ SPLIT │       │           │ │ │ notebooklm  │  │ │
│      │   1174→   │       │           │ │ │ learning    │  │ │
│      │   6 files │       │           │ │ └─────────────┘  │ │
│      │           │       │           │ └───────────────────┘ │
│      │           │       │           │                       │
│      │           │       │           │ ┌───────────────────┐ │
│      │           │       │           │ │ Web Subsystem     │ │
│      │           │       │           │ │  web/ (NEW PKG)   │ │
│      │           │       │           │ │  fetch/search     │ │
│      │           │       │           │ └───────────────────┘ │
│      │           │       │           │                       │
│      │           │       │           │ ┌───────────────────┐ │
│      │           │       │           │ │ Agent Subsystem   │ │
│      │           │       │           │ │  agent_tools/     │ │
│      │           │       │           │ │  spawn/swarm/seq  │ │
│      │           │       │           │ │  schedule/memory  │ │
│      │           │       │           │ └───────────────────┘ │
│      │           │       │           │                       │
│      │           │       │           │ ┌───────────────────┐ │
│      │           │       │           │ │ QQ Subsystem      │ │
│      │           │       │           │ │  qq/ (NEW PKG)    │ │
│      │           │       │           │ │  status/send_msg  │ │
│      │           │       │           │ └───────────────────┘ │
│      │           │       │           │                       │
│      │           │       │           │ ┌───────────────────┐ │
│      │           │       │           │ │ Media Subsystem   │ │
│      │           │       │           │ │  media/ (NEW PKG) │ │
│      │           │       │           │ │  image/read_img   │ │
│      │           │       │           │ └───────────────────┘ │
│      │           │       │           └───────────────────────┘
│      │           │       │
│      │           │       │
┌──────▼───────────▼───────▼─────────────────────────────────────┐
│                    agent/core.py (REFACTORED)                   │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Agent 核心 (~400行)                                      │  │
│  │  - 状态管理 (mode, fatigue, permission)                   │  │
│  │  - 依赖注入接收 (llm, memory, session, compressor, tools) │  │
│  │  - run() 公共入口                                         │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  ReActLoop (~300行, NEW FILE)                            │  │
│  │  - _run_loop() 核心 while-True                           │  │
│  │  - 事件 yield (text_delta, tool_call, permission...)     │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  PromptBuilder (~200行, NEW FILE)                        │  │
│  │  - _build_system_prompt()                                │  │
│  │  - _build_memory_block()                                 │  │
│  │  - STATIC_PROMPT 常量                                     │  │
│  └─────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  HistoryRepair (~150行, NEW FILE)                        │  │
│  │  - _repair_history()                                     │  │
│  │  - _apply_sliding_window_and_scratchpad()                 │  │
│  └─────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│              agent/net_gateway/ (FURTHER REFACTORED)          │
│                                                              │
│  bot.py    → 编排层 (~200行)                                  │
│  pipeline/ (NEW SUB-PKG)                                     │
│    ├── self_filter.py     # 自身消息过滤                      │
│    ├── activity_log.py    # 输入审计日志                      │
│    ├── group_listener.py  # 群聊静默旁听/唤醒                  │
│    ├── security_check.py  # 白名单+冷却拦截                   │
│    ├── admin_handler.py   # 管理员唤醒+开关指令               │
│    ├── pause_filter.py    # 私聊暂停检查                      │
│    ├── permission_gate.py # 权限审批答复                      │
│    ├── podcast_handler.py # 播客选题拦截                      │
│    ├── voice_test.py      # 语音测试指令                      │
│    ├── queue_manager.py   # 消息排队/抢占                     │
│    └── agent_launcher.py  # Agent推理启动                     │
│  voice/ (NEW SUB-PKG) ← tts.py 拆分                           │
│    ├── __init__.py                                            │
│    ├── emotion_config.py  # EMOTION_LOCKED_CONFIG              │
│    ├── synthesizer.py     # send_voice() 核心合成              │
│    ├── parser.py          # parse_voice_test_command()         │
│    └── audio_utils.py     # _pad_wav() 等工具函数              │
│  sender.py / logger.py / scheduler.py (不变)                  │
│  carrier.py / security.py / fatigue_manager.py (不变)         │
│  executor.py / presenter.py (不变)                            │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 Pipeline 中间件模式（net_gateway/dispatcher 解耦）

```
QQ消息事件
  │
  ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ SelfMsgFilter│───▶│ ActivityLog  │───▶│GroupListener │
│ (自身过滤)    │    │ (审计日志)    │    │ (静默旁听)    │
└──────────────┘    └──────────────┘    └──────┬───────┘
                          STOP (自身消息)       │ CONTINUE (触发唤醒)
                          STOP (未触发静默)      │
                                               ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│SecurityCheck │◀───│ AdminHandler │◀───│ PauseFilter  │
│ (白名单拦截)  │    │ (管理员特权)  │    │ (私聊暂停)    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │ STOP              │ STOP              │ STOP
       │ (非白名单)         │ (开关指令)         │ (暂停中)
       ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│PermissionGate│───▶│PodcastHandler│───▶│ VoiceTest    │
│ (权限审批)    │    │ (播客选题)    │    │ (语音测试)    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │ STOP              │ STOP              │ STOP
       │ (审批答复)         │ (选题选择)         │ (语音指令)
       ▼
┌──────────────┐    ┌──────────────┐
│QueueManager  │───▶│AgentLauncher │──────▶ 大模型推理
│ (排队/抢占)   │    │ (最终执行)    │
└──────────────┘    └──────────────┘
       │ STOP
       │ (排队等待)
```

每个中间件接口：
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class PipelineMiddleware(Protocol):
    async def handle(self, ctx: EventContext) -> bool:
        """返回 True = 拦截停止, False = 继续下一环节"""
        ...
```

### 2.4 Tools 子系统重组

```
agent/tools/ (现状: 25个文件平铺)
         │
         ▼
agent/tools/ (目标: 分组子包)
  │
  ├── __init__.py          # 公开导出
  ├── base.py              # BaseTool + ToolResult
  ├── registry.py          # ToolRegistry (不变)
  ├── decorator.py         # @tool 装饰器
  │
  ├── mcp/  (NEW)          # MCP协议工具
  │   ├── __init__.py
  │   ├── protocol.py      # 统一MCP JSON-RPC 2.0客户端 (消除stitch/mcp_client重复)
  │   ├── stitch.py        # StitchTool (从 stitch_tool.py)
  │   ├── xiaohongshu.py   # XiaohongshuTool (从 xiaohongshu_tool.py)
  │   ├── notebooklm.py    # NotebookLMTool (从 notebooklm_tool.py)
  │   └── agent_learning.py # FastMCP server (从 mcp_agent_learning_server.py)
  │
  ├── web/  (NEW)          # Web交互工具
  │   ├── __init__.py
  │   ├── fetch.py         # WebFetchTool
  │   └── search.py        # WebSearchTool
  │
  ├── agent/  (NEW)        # 多Agent工具
  │   ├── __init__.py
  │   ├── spawn.py         # SpawnAgentTool
  │   ├── swarm.py         # SwarmTool
  │   ├── sequence.py      # RunSequenceTool
  │   └── schedule.py      # ScheduleTaskTool
  │
  ├── qq/  (NEW)           # QQ集成工具
  │   ├── __init__.py
  │   ├── status.py        # GetQQStatusTool
  │   └── send_message.py  # SendQQMessageTool
  │
  ├── media/  (NEW)        # 媒体工具
  │   ├── __init__.py
  │   ├── image_gen.py     # Image2GenerateTool
  │   ├── image_read.py    # ReadImageTool
  │   └── voice.py         # 语音合成工具 (NEW - Agent可直接调用TTS)
  │
  ├── filesystem/  (NEW)   # 文件系统工具
  │   ├── __init__.py
  │   ├── bash.py          # BashTool
  │   ├── edit.py          # EditFileTool
  │   ├── read.py          # ReadFileTool
  │   └── write.py         # WriteFileTool
  │
  └── meta/  (NEW)         # 元工具
      ├── __init__.py
      ├── memory.py        # MemoryTool
      └── manage.py        # ManageToolTool
```

**收益**：
- 加一个新的 MCP 工具 → 在 `tools/mcp/` 下新建文件，不动其他目录
- 改图片生成逻辑 → 只改 `tools/media/image_gen.py`
- 按子系统独立测试：`pytest tests/tools/mcp/`, `pytest tests/tools/web/`

### 2.5 Memory Layer 拆分

```
agent/memory/ (现状: manager.py 1174行单类)
         │
         ▼
agent/memory/ (目标: 6个独立文件)
  │
  ├── __init__.py
  ├── store.py      # MemoryStore (~300行) — 记忆文件CRUD、KI管理、CORE_FILES
  ├── index.py      # MemoryIndex (~200行) — FTS5全文索引、embedding检索
  ├── ki.py         # KIManager (~200行) — KI save/merge/search、LLM融合
  ├── context.py    # ContextRetriever (~200行) — 上下文检索、记忆块构建
  ├── session.py    # SessionBridge (~150行) — 会话集成桥接
  ├── notes_fts.py  # (不变, 382行)
  ├── fts_index.py  # (不变, 48行)
  └── error_tracker.py (不变, 142行)
```

### 2.6 Voice/TTS 模块独立

```
agent/net_gateway/tts.py (现状: 281行混合6种职责)
         │
         ▼
agent/voice/ (NEW 顶级子包)
  │
  ├── __init__.py           # 公开导出
  ├── config.py             # EMOTION_LOCKED_CONFIG → 从环境变量/配置文件加载
  ├── synthesizer.py        # send_voice() — GPT-SoVITS API调用、Base64编码
  ├── parser.py             # parse_voice_test_command()
  ├── audio_utils.py        # _pad_wav()、WAV处理
  ├── emotion_presets.py    # 6种情绪预设 (撒娇/元气/傲娇/委屈/正常/知性)
  └── cache.py              # 语音缓存管理 (避免重复合成)
```

**收益**：
- Voice 模块可独立于 QQ Gateway 使用（CLI 模式、Dashboard 模式也能发声）
- 未来切换到 Edge-TTS 或 OpenAI TTS 只需改 `synthesizer.py`
- 情绪预设可热更新，不依赖代码部署

### 2.7 Skills 系统产品化

```
skills/ (现状: 2个孤立MD文件)
         │
         ▼
agent/skills/ (NEW 子包)
  │
  ├── __init__.py
  ├── loader.py          # SkillLoader — 扫描 skills/ 目录，解析 SKILL.md
  ├── registry.py        # SkillRegistry — 注册/启用/禁用/优先级
  ├── executor.py        # SkillExecutor — 按步骤执行skill
  └── builtins/          # 内置技能
      ├── identity_lock.py   # 身份验证锁定 (从 skills/identity_verification_lock)
      └── mind_cultivation.py # 心智能力培养 (从 skills/mind_cultivation_plan)
```

**Skill 定义格式标准化**：
```yaml
# skills/my_skill/SKILL.md
---
name: my_skill
version: "1.0"
priority: 10
trigger: on_session_start
enabled: true
---

# 技能名称
## 步骤1: ...
## 步骤2: ...
```

### 2.8 配置集中化

```
现状: .env + 硬编码路径 + shell脚本重复 + docker-compose重复
         │
         ▼
config/
  ├── default.yaml      # 默认配置 (所有key+默认值+文档注释)
  ├── qq_gateway.yaml   # QQ网关专属配置
  ├── tools.yaml        # 工具启用/禁用/优先级
  ├── voice.yaml        # TTS配置 (情绪预设/API地址/缓存策略)
  └── skills.yaml       # 技能注册表
```

`.env` 降级为仅存储**密钥** (API_KEY类)，其他所有配置迁移到 YAML。

### 2.9 启动脚本合并

```
现状: start-agent.sh (218行) + 启动QQAgent.command (113行) — 大量重复
         │
         ▼
bin/
  ├── start.sh           # 统一启动脚本 (~150行)
  ├── start_gateway.sh   # QQ Gateway 模式
  ├── start_dashboard.sh # Dashboard 模式
  ├── start_auto_learn.sh # 自主学习模式
  └── lib/
      ├── napcat.sh      # NapCat 容器管理函数
      ├── tts.sh         # GPT-SoVITS 管理函数
      └── env_check.sh   # 环境检测函数
```

---

## 三、优先级与分期规划

### Phase 1: P0 — 核心分解 (1-2 周)

| 编号 | 任务 | 影响文件 | 预期收益 |
|------|------|----------|----------|
| P0-1 | 拆分 `agent/core.py` → Agent + ReActLoop + PromptBuilder + HistoryRepair | 4 文件 | 1240→4×~300行 |
| P0-2 | 拆分 `agent/memory/manager.py` → 6 模块 | 6 文件 | 1174→6×~200行 |
| P0-3 | 创建 `agent/tools/mcp/` 统一 MCP 协议层 | ~5 文件 | 消除 stitch/mcp_client 重复 |
| P0-4 | 创建 `agent/tools/` 子包分组 (mcp/web/agent/qq/media/filesystem/meta) | 仅移动文件 | 25→8 目录 |

### Phase 2: P1 — 管道化与独立 (1 周)

| 编号 | 任务 | 影响文件 | 预期收益 |
|------|------|----------|----------|
| P1-1 | net_gateway Pipeline 中间件模式 | 11 新文件 + dispatcher 精简 | 加功能不碰 dispatcher |
| P1-2 | 创建 `agent/voice/` 独立 TTS 模块 | 6 文件 | TTS 脱离 QQ Gateway |
| P1-3 | 创建 `agent/skills/` 系统 | 4 文件 | 技能可动态加载 |
| P1-4 | 配置集中化 `config/*.yaml` | 4 文件 | 消除硬编码 |
| P1-5 | `main.py` 拆分 → CLI + bootstrap + 各模式 runner | 3 文件 | 562→~150行 |

### Phase 3: P2 — 清理与优化 (1 周)

| 编号 | 任务 | 影响文件 | 预期收益 |
|------|------|----------|----------|
| P2-1 | 启动脚本合并去重 | 3 文件 | 消除 ~200 行重复 |
| P2-2 | Dashboard 去重 (删除 office.html) | 1 文件 | 单一渲染实现 |
| P2-3 | 清理 scratch/ 和 dead code | ~40 文件 | 目录整洁 |
| P2-4 | `agent/evolution.py` 拆分为 coach/tester/traces/apply 子模块 | 4 文件 | 874→4×~220行 |
| P2-5 | `auto_learn.py` 辩论机制独立 | 2 文件 | 逻辑可复用 |

---

## 四、关键设计决策

### 4.1 为什么 Pipeline 而不是 Event Bus？

| 维度 | Pipeline (推荐) | Event Bus |
|------|-----------------|-----------|
| 执行顺序 | 确定、可配置 | 不确定、依赖订阅顺序 |
| 调试难度 | 线性追踪 | 事件跳跃难以定位 |
| 学习成本 | 低 (就是链式调用) | 中 (发布订阅心智模型) |
| 适合场景 | **有序拦截链** ← 当前场景 | 广播通知 |

当前网关消息处理是严格的**顺序过滤链**（security → admin → fatigue → ...），Pipeline 模式天然匹配。如果未来需要"新消息到达时通知多个模块"，可以在 Pipeline 之外叠加轻量 Event Bus。

### 4.2 为什么 Tools 按子包分组而不是按接口分组？

Tools 已经是统一接口 (`BaseTool`)，分组依据是**运行时依赖**：
- MCP 工具 → 依赖子进程/外部服务
- Web 工具 → 依赖 HTTP
- Agent 工具 → 依赖 `agent.core.Agent`
- QQ 工具 → 依赖 NapCat API
- Filesystem → 纯本地
- Media → 依赖外部 API (Image2, Mimo)
- Meta → 管理其他工具

这种分组确保：修改 QQ 工具时不需要了解 MCP 协议，修改 Web 工具时不需要启动 NapCat。

### 4.3 向后兼容策略

- 所有旧导入路径保留为 re-export（如 `from agent.tools.stitch_tool import StitchTool` → 自动指向 `from agent.tools.mcp.stitch import StitchTool`）
- `agent/gateway.py` Facade 继续生效
- 分阶段废弃：Phase 1 兼容 → Phase 2 标记 DeprecationWarning → Phase 3 移除旧路径

---

## 五、测试策略

### 每阶段门禁

```
Phase N 完成
    │
    ▼
┌─────────────────────┐
│ 1. 已有测试全量通过   │ ← 必须，零退化
├─────────────────────┤
│ 2. 新增模块独立测试   │ ← 新模块 >80% 覆盖率
├─────────────────────┤
│ 3. QQ Gateway 真实   │ ← 启动 start-agent.sh 测试真实收发
│    消息收发回归       │
├─────────────────────┤
│ 4. CLI 交互模式正常   │ ← python main.py 运行命令
└─────────────────────┘
```

### 新增测试文件规划

```
tests/
  ├── test_admin_private_recovery.py  (已有)
  ├── test_scheduler_preempt.py       (已有)
  ├── unit/
  │   ├── test_pipeline_middleware.py  (NEW - Pipeline 链路测试)
  │   ├── test_voice_synthesizer.py   (NEW - TTS 独立测试)
  │   ├── test_skill_loader.py        (NEW - 技能加载测试)
  │   ├── test_memory_store.py        (NEW - MemoryStore CRUD)
  │   ├── test_mcp_protocol.py        (NEW - 统一MCP客户端)
  │   └── test_config_loader.py       (NEW - 配置加载)
  └── integration/
      ├── test_tool_registry.py       (NEW - 工具注册/分发)
      └── test_bootstrap.py           (NEW - DI容器组装)
```

---

## 六、MCP 统一协议层设计

### 问题

当前 3 个工具各自实现 MCP JSON-RPC 2.0 协议：
- `stitch_tool.py`: `_encode_mcp()`, `_read_mcp_message()`, `_send_mcp()`
- `mcp_client_tool.py`: 自己的 stdio 子进程管理
- `xiaohongshu_tool.py`: Streamable HTTP 实现

### 方案

```python
# agent/tools/mcp/protocol.py

class MCPTransport(ABC):
    """MCP 传输层抽象"""
    @abstractmethod
    async def send(self, request: dict) -> dict: ...
    @abstractmethod
    async def close(self): ...

class StdioTransport(MCPTransport):
    """stdio JSON-RPC 2.0 (stitch, mcp_client)"""
    ...

class StreamableHTTPTransport(MCPTransport):
    """Streamable HTTP (xiaohongshu)"""
    ...

class MCPClient:
    """统一 MCP 客户端"""
    def __init__(self, transport: MCPTransport):
        self.transport = transport

    async def initialize(self) -> dict: ...
    async def list_tools(self) -> list[dict]: ...
    async def call_tool(self, name: str, arguments: dict) -> dict: ...
```

StitchTool、XiaohongshuTool、MCPClientTool 都通过 `MCPClient` + 对应 Transport 实现，消除协议重复代码 (~150 行)。

---

## 七、数据流图 (Data Flow)

```
┌─────────────────────────────────────────────────────────────┐
│                       用户交互层                              │
│  QQ消息 / CLI输入 / Dashboard WebSocket / HTTP API            │
└──────────────┬──────────────────────────────────────────────┘
               │
     ┌─────────▼──────────┐
     │   Message Pipeline  │  ← 11个中间件顺序过滤
     │   (net_gateway)     │
     └─────────┬──────────┘
               │ event passed through
     ┌─────────▼──────────┐
     │   Agent.run()       │  ← 核心ReAct循环
     │   (agent/core.py)   │
     └──┬──────┬──────┬───┘
        │      │      │
   ┌────▼─┐ ┌─▼──┐ ┌─▼─────┐
   │ LLM  │ │Tool│ │Prompt │  ← 每次推理: 构建prompt → 调LLM → 执行tool → 下一轮
   │ call │ │exec│ │build  │
   └────┬─┘ └─┬──┘ └───────┘
        │     │
        │     ├── MCP工具 → 外部MCP Server (stdio/HTTP)
        │     ├── Web工具 → DuckDuckGo / urllib
        │     ├── Agent工具 → 子Agent (spawn/swarm)
        │     ├── QQ工具 → NapCat HTTP API
        │     ├── Media工具 → Image2 / Mimo API
        │     └── FS工具 → 本地文件/Shell
        │
   ┌────▼──────────────────┐
   │  StreamPresenter       │  ← 流式输出分句 + 语音触发
   │  (net_gateway)         │
   └────────┬───────────────┘
            │
   ┌────────▼───────────────┐
   │  MessageSender          │  ← 令牌桶限流 + Markdown净化 + HTTP发送
   │  (net_gateway)         │
   └────────┬───────────────┘
            │
   ┌────────▼───────────────┐
   │  Voice Synthesizer      │  ← GPT-SoVITS TTS (条件触发)
   │  (agent/voice/)         │
   └─────────────────────────┘
```

---

## 八、验收标准

每个 Phase 完成后必须满足：

1. **`find . -name "*.py" -exec wc -l {} \; | sort -rn | head -5`** → 所有文件 < 400 行
2. **`pytest tests/ -v`** → 全量通过，零退化
3. **`python main.py --gateway`** → 启动成功，WebSocket 连接 NapCat
4. **真实 QQ 消息测试**：发送 "小萤你好" → 收到回复
5. **`python main.py`** (CLI模式) → 交互正常
6. **`grep -r "hasattr.*bot" agent/`** → 无输出（消除鸭子类型探路）
7. **`grep -r "getattr.*bot" agent/`** → 无输出
8. **新功能追加测试**：在 pipeline 中新增一个 middleware → 不需修改已有文件

---

## 九、附录

### A. 文件行数完整排名 (Top 20)

| # | 文件 | 行数 | 优先级 |
|---|------|------|--------|
| 1 | agent/core.py | 1240 | P0 |
| 2 | agent/memory/manager.py | 1174 | P0 |
| 3 | agent/evolution.py | 874 | P1 |
| 4 | agent/tools/xiaohongshu_tool.py | 574 | P1 |
| 5 | main.py | 562 | P1 |
| 6 | agent/tools/stitch_tool.py | 559 | P1 |
| 7 | agent/image_server.py | 548 | P2 |
| 8 | agent/auto_podcast.py | 510 | P2 |
| 9 | agent/auto_learn.py | 493 | P2 |
| 10 | agent/tools/notebooklm_tool.py | 441 | P1 |
| 11 | agent/tests/test_tool_robustness.py | 382 | — |
| 12 | agent/memory/notes_fts.py | 382 | — |
| 13 | agent/tools/mcp_agent_learning_server.py | 380 | P1 |
| 14 | agent/tools/memory_tool.py | 371 | P1 |
| 15 | agent/session/handler.py | 360 | — |
| 16 | agent/net_gateway/dispatcher.py | 341 | P1 (已完成部分) |
| 17 | agent/tools/file_tools.py | 329 | P1 |
| 18 | agent/evo_tester.py | 318 | P2 |
| 19 | agent/net_gateway/tts.py | 281 | P1 |
| 20 | agent/net_gateway/bot.py | 274 | P2 (已完成部分) |

### B. 待清理项

| 项目 | 说明 |
|------|------|
| `scratch/` (40+文件) | 历史实验代码，建议归档到 `archive/scratch-2026-05/` |
| `scratch/gateway_perfect_backup.py` (1257行) | 旧 gateway 备份，可删除 |
| `agent/tools/stitch_tool.py` L345-532 | 死代码 (unreachable asyncio subprocess) |
| `agent/dashboard_v2/office.html` | 与 engine.js+sprites.js+events.js 重复 |
| `agent/dashboard_v2/archive/` + `留档/` | 旧版历史，建议外部归档 |
| `agent/tools/notebooklm_tool.py` ↔ `mcp_agent_learning_server.py` 交叉导入 | 统一到 `tools/mcp/` 子包后消除 |
| `start-agent.sh` ↔ `启动QQAgent.command` 重复 | 合并为 `bin/start.sh` |
