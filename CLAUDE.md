# XL Agent — 通用 AI Agent

> 老肖的个人 Agent。Python + LiteLLM + DeepSeek。自进化、会学习、有像素面板。

## 项目结构

```
搭建agent/
├── main.py                  # CLI (--gateway/--dashboard/--auto-learn/--cleanup/--plan)
├── start_dashboard.sh       # Dashboard 一键启动
├── auto_learn.sh            # 自主学习启动（launchd 用）
├── com.myagent.autolearn.plist  # macOS 定时任务（每天 10:00）
├── requirements.txt         # litellm + python-dotenv + aiofiles + ddgs
├── .env                     # API key + 模型配置
├── CLAUDE.md                # 本文件
│
├── agent/                   # 核心代码
│   ├── core.py              # Agent 主循环 (while-true + AsyncGenerator, 374行)
│   │                        #    - 统一流式/非流式 _run_loop(stream=bool)
│   │                        #    - [MEMORY BLOCK] 隔离注入 + 自进化规则
│   │                        #    - Plan mode: plan_ready → 用户确认 → 执行
│   ├── llm.py               # LiteLLM 封装 (chat + chat_stream, 160行)
│   ├── compressor.py        # 上下文压缩 (Head/Tail + LLM摘要 + 熔断器, 219行)
│   ├── auto_learn.py        # 自主学习系统 (子代理并行 + 辩论审查, 493行)
│   │                        #    - Phase 1: spawn子代理并行搜索学习
│   │                        #    - Phase 2: 交叉质疑→辩护→双评审→双通过入库
│   ├── evolution.py         # 进化模块 (7个模式, 392行)
│   │                        #    - after_tool_call审计 / on_session_end反思
│   │                        #    - task→skill检测 / 技能追踪
│   │                        #    - 自进化规则: 反馈≥2次→生成规则→注入prompt
│   ├── cleanup.py           # 知识库清理器 (pro模型批量审查, 174行)
│   ├── gateway.py           # QQ Gateway (NapCat WebSocket + HTTP API, 140行)
│   │                        #    - aiohttp ws → OneBot v11 → Agent.run()
│   │                        #    - 按用户/群隔离 session，@提及过滤
│   ├── dashboard.py         # HTTP+SSE server (100行)
│   ├── dashboard.html       # Canvas 像素办公室 (300行, 零依赖)
│   │
│   ├── tools/               # 工具系统
│   │   ├── base_tool.py     # BaseTool ABC (抄 tinypace)
│   │   ├── registry.py      # ToolRegistry 单例 (简化自 hermes)
│   │   ├── file_tools.py    # read_file + write_file
│   │   ├── bash_tool.py     # shell 执行
│   │   ├── web_search_tool.py  # DuckDuckGo 搜索
│   │   ├── web_fetch_tool.py   # 网页全文抓取
│   │   └── memory_tool.py      # save_memory (5类+禁止清单)
│   │
│   ├── memory/
│   │   └── manager.py       # MEMORY.md + 时间戳进化 + 用户画像 (148行)
│   │
│   └── session/
│       └── handler.py       # JSONL 持久化 + Transcript repair + 跨会话搜索 (145行)
│
├── docs/                    # 技术设计文档（给 AI 看的）
│   ├── 升级路线总览.md
│   ├── 升级设计-阶段2-工具系统.md
│   ├── 升级设计-阶段3-上下文压缩.md
│   └── 升级设计-阶段4-自动记忆.md
│
└── ../Agent/自建Agent实操/   # 实操文档（给人看的，16篇）
    ├── 00-总览.md
    ├── 01~15-各阶段实操.md
    └── 16-像素办公室Dashboard.md
```

## 编码规范

### 原则
1. **能复用优先复用** — 5 个开源项目 (tinypace/hermes/cc-haha/openclaw/OpenMAIC) 的代码能抄就抄
2. **简单优先** — Anthropic 哲学：能单次 LLM 就不用 Agent
3. **写完必须审查去冗余** — 每次 commit 前检查死代码、重复方法、未用 import
4. **新功能先看源码** — 做新东西前，先看 5 个参考项目的对应实现
5. **省 token** — 能用 Explore agent 不用 general-purpose，能并行不串行

### 风格
- Python：类型注解、async/await、dataclass
- 方法 ≤ 30 行
- 文件 ≤ 500 行（超过就拆分）
- 注释只写"为什么"，不写"是什么"
- 中文 docstring 给用户看的方法，英文给内部方法

### 来源标注
每个文件头部标注抄了哪个项目：
```python
"""Memory system — CC MEMORY.md pattern + timestamp evolution."""
```

## AI 协作流程

看别的项目怎么做 → 分析优缺 → 组合设计 → 编码 → 审查去冗余 → 提交 → 更新文档

### 提交规范
```
feat: 做了什么
fix: 修了什么
refactor: 简化了什么
docs: 文档
perf: 性能优化
```
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>

### 执行环境
- venv: `.venv/bin/activate`
- Python: 3.14
- 模型: deepseek-v4-pro (对话) / deepseek-v4-flash (学习)
- 禁止: brew install、全局 pip、删除 ~/.* 目录

## 关键设计决策

| 决策 | 选型 | 原因 |
|------|------|------|
| 核心循环 | while-true + AsyncGenerator | 比递归直观，比同步支持流式 |
| LLM 接口 | LiteLLM | 多 provider 一键切换 |
| 工具抽象 | tinypace BaseTool ABC | 接口最干净 |
| 记忆存储 | CC MEMORY.md | 零依赖，模型原生可读 |
| 记忆注入 | hermes [MEMORY BLOCK] + CC 构造时固定 | 隔离 + 可靠 |
| 压缩 | tinypace Head/Tail + CC 熔断器 | 简单有效 |
| 学习审查 | flash提取 + pro审查 双层 | 成本 vs 质量平衡 |
| Dashboard | Canvas + SSE 纯前端 | 零外部依赖 |
| 自进化 | 反馈≥2次→LLM生成规则→注入prompt | 数据驱动不改代码 |
| Plan Mode | plan_ready事件 + asyncio.Event确认 | 用户审查后执行 |
| 终端交互 | 多行粘贴检测 + SIGINT打断 + ANSI高亮 | Unix原生支持 |

## 参考源码位置

```
源码集合/agent源码/
├── tinypace-ai-agent/   → BaseTool、Session、AutoCompact、MCP
├── hermes-agent/         → ToolRegistry、MemoryProvider、FTS5、学习循环
├── cc-haha/              → Agent Loop、Bash安全、Prompt组装
├── openclaw/             → 插件SDK、多Agent、BM25+向量
└── OpenMAIC/             → LangGraph编排、SSRF防护
```

源码索引: `../Agent/源码索引.md`

## 文档导航

| 看什么 | 去哪里 |
|--------|--------|
| 项目总览 | `../Agent/自建Agent实操/00-总览.md` |
| 各阶段实操 | `../Agent/自建Agent实操/01~16-*.md` |
| 源码索引 | `../Agent/源码索引.md` |
| 设计决策 | `docs/升级设计-阶段*.md` |
| 记忆架构全景 | `../Agent/自建Agent实操/12-记忆架构全景.md` |
| 三层记忆v2 | `../Agent/自建Agent实操/13-记忆架构v2-三层记忆与注入模式.md` |
| KI蒸馏系统 | `../Agent/自建Agent实操/09-KI蒸馏系统设计.md` |
| 进化机制对比 | `../Agent/自建Agent实操/08-三家进化机制最终对比.md` |
| Dashboard 维护 | `../Agent/自建Agent实操/16-像素办公室Dashboard.md` |
