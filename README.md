# 🌟 XL Agent (小萤 AI 代理) 🌟

肖亮的个人专属极客级 AI 智能体代理（XL Agent）。基于经典的 ReAct 决策双循环构建，支持运行时动态工具调用、多层降级长期记忆检索、多 Agent 蜂群协作、自动进化迭代，并配备了先进的 [CSMA/CD 消息避让防死锁] 与 [Token 精准脑力疲劳度打盹] 的物理网关防刷机制。

---

## 🚀 快速开始

可以通过终端 CLI 工具进行单次对话、交互式会话、启动监控面板或开启自动学习模式：

```bash
xl "你好"              # 单次命令行对话
xl                    # 交互模式（支持 /exit 退出，支持历史上下文压缩）
xl --dashboard       # 极简终端监控面板，实时观测 Agent 运行负荷
xl --auto-learn      # 自动学习与规则进化模式
bash bin/start.sh    # 统一启动自愈中枢，一键拉起网关及所有依赖
```

---

## 📡 消息网关控制与防死锁系统 (CSMA/CD ＆ Fatigue)

为了在生产环境（例如高频群聊、Bot 互怼、私聊碎碎念）中彻底规避消息连发引起的无限死锁、大模型 Token 资源恶意过载、抢话被打断等问题，XL Agent 引入了工业级的消息路由流控引擎：

### 1. 载波监听与退避协议 (Carrier Sense)
- [监听顺延]：当收到发言时，不立刻启动大模型提问协程，而是进入 QQ_CSMA_BACKOFF_SECONDS（默认 2.0s）的退避等待期。
- [合并去重]：若对方在等待期内连续发送消息，等待定时器会自动向后延展。只有当信道连续静默 2.0 秒（即对方说完了整段碎碎念）后，网关才正式合并文本并启动推理。前序消息 of 无用协程将被默默废弃。

### 2. 冲突检测与暂存避让 (Collision Detection)
- [两阶段中断丢弃]：在大模型流式推理生成中途，或在向渠道发送缓冲消息的最后时刻，如果检测到对方在此期间又“插嘴”发送了新消息，小萤会立刻强行中断大模型运行，并彻底清空发送缓冲区。这杜绝了各说各的、抢着发半成品碎片的尴尬局面。
- [并发避让]：当大模型正在调用某些高耗时工具（如生图、执行长代码）时，新涌入的消息将在暂存队列中避让，防止语序错乱。

### 3. Token 精准疲劳计费与打盹梦境进化 (Nap Mode)
- [Token 精准扣分]：非管理员（亮哥以外的普通群友或第三方 Bot）的消息会根据其回复 Token 数量乘以 QQ_FATIGUE_RATE 系数扣减脑力疲劳度（时间流逝会自动以每分钟 2.0% 的速度自我消退）。
- [用脑过度打盹]：疲劳值首次触及 100.0% 时，小萤会在群内发送高情商的“物理冷静用脑过度吐槽”，随后进入持续 15 分钟（QQ_FATIGUE_SLEEP_MINUTES）的物理打盹期。
- [打盹梦境进化]：打盹期间自动屏蔽对该用户的消息响应，同时异步唤起做梦机制，自动反思近期交互并将其压缩进进化库中。冷却结束后自动宣告苏醒。

### 4. 亮哥主控特权与穿透
- [一键启停命令]：管理员亮哥在私聊中发送 "暂停私聊" 和 "恢复私聊"，可一键物理冻结或解冻非亮哥用户的私聊网关。
- [特权唤醒穿透]：即使在非管理员因疲劳被打盹拦截、或私聊被暂停期间，亮哥的任何消息都能 100% 强行穿透拦截，立即强制唤醒小萤的大脑并予以高质量回复。

> [!IMPORTANT]
> 关于该网关更深度的机制原理、状态机设计及集成测试验证，请查阅 docs/architecture/GATEWAY_CSMA_FATIGUE.md。

---

## 🧠 记忆与自我审视系统

小萤的记忆系统经过精心设计，由轻量级纯本地向量库与运行时元认知审视机制组成：

### 1. 两层记忆模型
- [情景记忆 (FTS5 全文搜索)]：自动将工具调用和交互上下文持久化至 SQLite，并利用 SQLite FTS5 建立全文倒排索引（已针对 CJK 中文分词进行分字分词优化），检索时若无 FTS5 则平滑降写为 LIKE 模糊匹配。
- [长效知识库 (笔记自动索引)]：首次调用时延迟且异步初始化，自动分块扫描读取指定学习笔记，作为“背景相关知识”注入大模型。

### 2. 0 Key、100% 离线免费本地向量化 (Local m3e-base)
- 在 config/settings.yaml 中将 EMBEDDING_MODE 设为 local，小萤会自动在本地 CPU 上惰性加载 m3e-base 编码模型。
- 首次加载自动利用国内 HuggingFace 镜像极速拉取，无任何调用资费，完全在本地完成 768 维稠密实数向量的嵌入和 SQLite 特征存储，媲美云端网络。

### 3. 元认知物理环境自主审视 (Metacognition Self-Reflection)
- [拒绝生硬硬编码]：为了让小萤在面对“你现在有向量库吗？”、“你的 API Key 是什么？”、“你用什么模型运行？”等问题时，不依赖死记硬背的文本记忆，我们在 STATIC_PROMPT 中注入了“自我物理审视指令”。
- [知行合一]：遇到此类涉及自身配置或物理代码状态的问题，小萤将第一反应自主调用 read_file 工具，去查看自己所处物理环境中的 config/settings.yaml 文件或相关核心代码（如 agent/memory/manager.py），从而用最严谨客观的事实进行回答。

---

## ⚙️ 中央参数配置参考 (config/settings.yaml)

项目中央配置采用 YAML 规范全解耦设计，调优时无需修改任何 Python 逻辑：

- MYAGENT_MODEL: 大模型主驱动模型（LiteLLM 规范，默认为 deepseek/deepseek-v4-flash）
- MYAGENT_MAX_TURNS: 限制单次 ReAct 会话的最深决策步数，防无限死循环
- QQ_ADMIN_ID: 管理员 (主人亮哥) 的 QQ 账号，具有高特权控制和免疲劳特权
- QQ_CSMA_BACKOFF_SECONDS: 载波监听避让秒数。高频群聊可调至 3.0 增强消息合并
- QQ_FATIGUE_SLEEP_MINUTES: 脑力疲劳过载后的打盹物理冷静睡眠时间 (分钟)
- QQ_FATIGUE_RATE: 私聊/群聊回复疲劳计费扣分系数，值设为 0 代表关闭疲劳度
- EMBEDDING_MODE: 向量化嵌入模式。可选 local (纯本地免费) 或 cloud (远程 API)

---

## 🛠️ 工具系统

XL Agent 拥有极其强大的工具箱，所有工具均由 ToolRegistry 统一注册，支持运行时动态增删：

- read_file: 读取物理文件内容（可用于自我环境审视）
- write_file: 新建或覆盖物理文件（写入产出等）
- edit_file: 搜索替换精准编辑（代码原地修改）
- bash: 执行系统 shell 终端命令（提供沙箱级的操作支持）
- web_search: 网页搜索 (联网检索，获取最新事实)
- web_fetch: 网页正文深度爬取与解析（剔除无效 HTML 标签）
- spawn_agent: 派生子 Agent 并发运行（自动分工与任务下发）
- swarm: 蜂群协作（任务拆解与 worker 结果聚合）
- mcp_client: 挂载外部 Standard MCP 服务（支持各种 Stitch 等外部生态工具）
- save_memory: 长期记忆库的增加、检索与删除（记忆持久化）

---

## 🧬 进化与自动优化引擎

小萤能够在每次会话结束后进行“复盘审计”，实现越用越聪明的正向循环：

```
交互完成 ──> 触发 tool_audit ──> 检测任务 Pattern ──> 提炼并吞噬归并 KI ──> 规则自进化 ──> 动态注入 System Prompt
```

- [EvolutionEngine]：负责追溯每一次工具成功率、耗时表现，发现无效操作后，从反馈记忆中生成去重规则，持久化于进化库，并由引擎在下一次初始化时动态注入大模型先识中。

---

## 📂 项目模块解耦架构 (物理极简 ＆ 高内聚)

本项目经过高水准工业级微服务化彻底重构，全系统物理大文件已被完全消灭（每个代码文件均控制在 400 行以内，杜绝臃肿耦合）：

```
.
├── main.py                     - 极简 CLI 路由启动器 (<40行)
├── requirements.txt            - 依赖包声明
├── config/
│   └── settings.yaml          - 全量统一解耦配置文件 (集中收拢)
├── bin/
│   ├── start.sh               - 统一启动自愈中枢 (无星号版)
│   ├── start-agent.sh         - 轻量代理 Wrapper
│   └── 启动QQAgent.command     - 桌面双击代理 Wrapper
├── docs/
│   ├── superpowers/specs/     - 系统重构与微服务设计文档集
│   └── architecture/          - 架构控制与说明文档
└── agent/
    ├── core.py                 - Agent 外观 Facade 决策中心 (<360行)
    ├── config.py               - YAML 配置管理器
    ├── bootstrap.py            - 系统环境初始化与工具自动组装
    ├── llm.py                  - 统一 LiteLLM 驱动器 (加固 mimo 认证)
    ├── react_loop.py           - ReAct 决策推理大循环
    ├── prompt_builder.py       - 动态提示词与上下文组装
    ├── tts.py                  - 语音合成向下兼容接口
    ├── voice/
    │   └── tts.py             - 动漫语音合成组件 (解耦自网关)
    ├── net_gateway/
    │   ├── bot.py             - WebSocket 连接与 OneBot 适配
    │   ├── executor.py        - 异步网关指令并发执行器
    │   ├── dispatcher.py      - 管道执行器
    │   └── middleware/
    │       ├── base.py        - 中间件基类
    │       └── pipeline.py    - 11 大高内聚中间件流水线 (解耦 600 行单体)
    ├── evolution/
    │   ├── base.py            - 自进化与工具审计调度中枢 Base
    │   ├── dream.py           - 异步梦境吞噬合并
    │   ├── rules.py           - 规则自进化生成
    │   └── sop.py             - 任务 SOP 模式提取
    ├── skills/
    │   └── manager.py         - 技能动态注册与使用状态监控
    └── memory/
        ├── manager.py         - 长期记忆延迟加载 Facade 代理
        ├── store.py           - 记忆文件 I/O 与 GC 回收
        ├── index.py           - SQLite 表结构与向量嵌入
        ├── ki.py              - 长期大脑 KI 提炼与合并
        ├── context.py         - 混合 RAG 双通道全文检索
        └── session.py         - 用户特征画像合成
```

---

## 🧪 静态校验与单元测试

为了确保对系统文档和代码的修改不引起任何核心功能的崩溃，请在发布前跑通回归单元测试：

```bash
# 运行回归单元测试套件，核验 28 项核心指标
PYTHONPATH=. venv/bin/pytest tests
```