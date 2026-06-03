# XL Agent — 工业级通用异步 AI 智能体代理

基于经典 ReAct 决策双循环构建的高性能通用 AI 智能体代理（XL Agent）。系统支持运行时动态工具调用、多层降级长期记忆检索、多 Agent 蜂群协作、自适应规则进化，并配备了基于 CSMA/CD 载波监听/冲突检测的消息流控避让网关与基于 Token 过载度量的休眠冷却机制。

---

## 快速开始

可以通过终端 CLI 工具进行单次对话、交互式会话、启动监控面板或开启自动学习模式：

```bash
xl "你好"              # 单次命令行对话
xl                    # 交互模式（支持 /exit 退出，支持历史上下文压缩）
xl --dashboard       # 极简终端监控面板，实时观测 Agent 运行负荷
xl --auto-learn      # 自动学习与规则进化模式
bash bin/start.sh    # 统一启动自愈中枢，一键拉起网关及所有依赖
```

---

## 消息网关控制与防死锁系统 (CSMA/CD ＆ Fatigue)

为了在生产环境（例如高频群聊、Bot 间并发交互、高并发私聊）中规避消息连发引起的无限死锁、大模型 Token 资源恶意过载、发言被抢占中断等工程问题，XL Agent 引入了工业级的消息路由流控引擎：

### 1. 载波监听与退避协议 (Carrier Sense)
* **监听顺延**：当收到发言请求时，系统不立刻启动大模型推理协程，而是进入 `QQ_CSMA_BACKOFF_SECONDS`（默认 2.0s）的退避等待期。
* **合并去重**：若对方在等待期内连续发送消息，等待定时器会自动向后延展。只有当信道连续静默 2.0 秒（即发言流结束）后，网关才正式合并文本并启动推理。前序并发请求所关联的未完成协程将被自动销毁，节约计算资源。

### 2. 冲突检测与暂存避让 (Collision Detection)
* **两阶段中断丢弃**：在大模型流式推理生成中途，或在向渠道发送缓冲消息的最后时刻，如果检测到对方在此期间又发送了新消息（即信道抢占），智能体将立刻中断大模型运行，并清空发送缓冲区，避免冲突响应。
* **并发避让**：当大模型正在调用高耗时工具（如图像生成、长代码执行）时，新涌入的消息将在暂存队列中避让，防止上下文时序错乱。

### 3. Token 精准过载计费与休眠冷却反思机制 (Nap Mode)
* **Token 精准疲劳度计费**：非管理员用户的请求会根据其回复 Token 数量乘以 `QQ_FATIGUE_RATE` 系数累加系统疲劳值（该值会以每分钟 2.0% 的速度衰减）。
* **系统休眠冷却**：疲劳值触及 100.0% 的安全水位线时，智能体将输出系统过载冷却提示，随后进入持续 15 分钟（`QQ_FATIGUE_SLEEP_MINUTES`）的物理休眠期。
* **休眠期离线反思**：休眠期间自动屏蔽对该用户的消息响应，同时异步调度反思引擎，归纳近期交互知识并将其持久化归并至进化规则库中。冷却期结束后自动恢复在线服务状态。

### 4. 管理员特权通道与强穿透控制
* **一键启停指令**：超级管理员可在控制端发送 "暂停私聊" 和 "恢复私聊" 指令，一键物理冻结或解冻非管理员用户的私聊网关。
* **特权唤醒穿透**：即使系统处于休眠冷却或业务挂起状态，超级管理员的消息均能实现 100% 强穿透，强制唤醒推理引擎并执行响应。

> [!IMPORTANT]
> 关于该网关更深度的状态机设计及集成测试验证，请查阅 [docs/architecture/GATEWAY_CSMA_FATIGUE.md](docs/architecture/GATEWAY_CSMA_FATIGUE.md)。

---

## 记忆与自我审视系统

本智能体的记忆系统由轻量级纯本地向量库与运行时元认知审视机制组成：

### 1. 两层记忆模型
* **情景记忆 (FTS5 全文搜索)**：自动将工具调用和交互上下文持久化至 SQLite，并利用 SQLite FTS5 建立全文倒排索引（针对 CJK 中文分词进行优化），检索时若无 FTS5 则平滑降写为 LIKE 模糊匹配。
* **长效知识库 (文档自动索引)**：系统采用惰性加载模式，首次调用时异步分块扫描读取指定学习文档，作为相关背景知识注入大模型上下文。

### 2. 纯离线免费本地向量化 (Local m3e-base)
* 在 `config/settings.yaml` 中将 `EMBEDDING_MODE` 设为 `local` 时，智能体会自动在本地 CPU 上惰性加载 `m3e-base` 编码模型。
* 首次加载自动利用 HuggingFace 国内镜像源拉取，完全在本地完成 768 维稠密实数向量的嵌入和 SQLite 特征存储，确保数据隐私安全。

### 3. 元认知物理环境自主审视 (Metacognition Self-Reflection)
* **拒绝生硬规则响应**：为了使智能体在面对“系统当前是否启用向量库？”、“当前使用的模型与 API 密钥是什么？”等涉及自身物理配置的问题时，不依赖预设硬编码文本。
* **知行合一**：遇到涉及自身运行配置的问题，智能体将第一顺序自主调用 `read_file` 工具读取并解析 `config/settings.yaml` 或相关核心代码（如 `agent/memory/manager.py`），以底层真实配置状态作为事实基础生成严谨的客观回答。

---

## 中央参数配置参考 (config/settings.yaml)

项目中央配置采用 YAML 规范全解耦设计，调优时无需修改任何 Python 逻辑：

* `MYAGENT_MODEL`: 大模型驱动模型定义（遵循 LiteLLM 规范，默认为 `deepseek/deepseek-v4-flash`）
* `MYAGENT_MAX_TURNS`: 限制单次 ReAct 会话的最深决策步数，防范无限递归
* `QQ_ADMIN_ID`: 管理员（超级用户）的唯一身份标识，具备最高优先权与免疲劳豁免
* `QQ_CSMA_BACKOFF_SECONDS`: 载波监听避让秒数，高频交互场景下可适当调高以增强消息合并率
* `QQ_FATIGUE_SLEEP_MINUTES`: 疲劳过载后的休眠冷却时间（分钟）
* `QQ_FATIGUE_RATE`: 回复疲劳计费扣分系数，值设为 0 代表关闭疲劳控制
* `EMBEDDING_MODE`: 向量化嵌入模式，可选 `local`（纯本地免费）或 `cloud`（远程 API 服务）

---

## 工具系统

XL Agent 拥有功能完备的工具箱，由 `ToolRegistry` 统一注册，支持运行时动态管理：

* `read_file`: 读取物理文件内容（用于环境自我审视与代码审计）
* `write_file`: 新建或覆盖物理文件内容
* `edit_file`: 搜索替换与精准局部编辑
* `bash`: 执行系统 shell 终端指令（提供沙箱级执行环境）
* `web_search`: 联网进行实时网页搜索
* `web_fetch`: 网页正文深度爬取与解析（去除冗余 HTML 标签）
* `spawn_agent`: 派生子 Agent 并发运行（实现任务分工下发）
* `swarm`: 蜂群协作（任务拆解与多 Worker 结果聚合）
* `mcp_client`: 挂载外部 Standard MCP 服务以扩展外部生态工具
* `save_memory`: 长期记忆库的增删改查与检索维护

---

## 进化与自动优化引擎

智能体能够在每次会话结束后进行自愈复盘与优化审计：

```
交互完成 ──> 触发 tool_audit ──> 检测任务 Pattern ──> 提炼并归并 KI ──> 规则自进化 ──> 动态注入 System Prompt
```

* `EvolutionEngine`：负责追溯每一次工具成功率与耗时表现，发现低效或异常操作后，从反馈记忆中生成去重规则，持久化于自进化库中，并在系统下一次初始化时动态注入大模型提示词。

---

## 项目模块解耦架构 (物理极简 ＆ 高内聚)

全系统经过模块化微服务重构，避免臃肿耦合，每个核心代码文件均控制在 400 行以内：

```
.
├── main.py                     - 极简 CLI 路由启动器 (<40行)
├── requirements.txt            - 依赖包声明
├── config/
│   └── settings.yaml          - 全量统一解耦配置文件 (集中收拢)
├── bin/
│   ├── start.sh               - 统一启动自愈中枢
│   ├── start-agent.sh         - 轻量代理 Wrapper
│   └── 启动QQAgent.command     - 桌面双击代理 Wrapper
├── docs/
│   ├── superpowers/specs/     - 系统重构与微服务设计文档集
│   └── architecture/          - 架构控制与说明文档
└── agent/
    ├── core.py                 - Agent 外观 Facade 决策中心 (<360行)
    ├── config.py               - YAML 配置管理器
    ├── bootstrap.py            - 系统环境初始化与工具自动组装
    ├── llm.py                  - 统一 LiteLLM 驱动器
    ├── react_loop.py           - ReAct 决策推理大循环
    ├── prompt_builder.py       - 动态提示词与上下文组装
    ├── tts.py                  - 语音合成向下兼容接口
    ├── voice/
    │   └── tts.py             - 语音合成组件 (解耦自网关)
    ├── net_gateway/
    │   ├── bot.py             - WebSocket 连接与 OneBot 适配
    │   ├── executor.py        - 异步网关指令并发执行器
    │   ├── dispatcher.py      - 管道执行器
    │   └── middleware/
    │       ├── base.py        - 中间件基类
    │       └── pipeline.py    - 11 大高内聚中间件流水线
    ├── evolution/
    │   ├── base.py            - 自进化与工具审计调度中枢
    │   ├── dream.py           - 异步离线反思合并
    │   ├── rules.py           - 规则自进化生成
    │   └── sop.py             - 任务 SOP 模式提取
    ├── skills/
    │   └── manager.py         - 技能动态注册与使用状态监控
    └── memory/
        ├── manager.py         - 长期记忆延迟加载 Facade 代理
        ├── store.py           - 记忆文件 I/O 与 GC 回收
        ├── index.py           - SQLite 表结构与向量嵌入
        ├── ki.py              - 长期知识库 KI 提炼与合并
        ├── context.py         - 混合 RAG 双通道全文检索
        └── session.py         - 用户特征画像合成
```

---

## 静态校验与单元测试

为了确保修改不引起任何核心功能回归，请在部署前运行单元测试套件：

```bash
# 运行回归单元测试套件，核验核心指标
PYTHONPATH=. venv/bin/pytest tests
```