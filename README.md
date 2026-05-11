# XL Agent — 通用自进化 AI Agent (v2.2)

> 老肖的个人 AI Agent。ReAct 循环 + FTS5 情景回忆 + Token 优化 + Docker 生产级部署。

## ✨ 核心特性

### 🔍 情景回忆 (FTS5 Full-Text Search)
- **SQLite FTS5 全文索引**：JSONL 会话历史自动同步到 FTS5 虚拟表，毫秒级检索
- **CJK 中文分词**：中文字符自动分字预处理，英文/中文混合搜索无死角
- **snippet() 上下文片段**：搜索结果高亮命中词 + 前后文，不只是文件列表
- **降级保护**：FTS5 语法异常 → LIKE 降级 → JSONL grep 兜底，三层保障

### 🧠 Token 优化 (Dynamic Top-K)
- **Flash 模型关键词提取**：用户输入 → Flash 模型提取 3-5 个关键词
- **关键词匹配排序**：记忆按命中数排序，只注入 Top-5 最相关条目（原 8 条）
- **精简展开**：仅 1 条全文展开（原 2 条），上限从 4000 → 3000 字符
- **实测效果**：记忆注入 Token 降低约 40%

### 🔗 记忆合并去重 (Memory Merge)
- **同主题检测**：保存前扫描旧记忆，文件名精确匹配 + 关键词重叠双路检测
- **LLM 合并**：新旧内容 → LLM 去重合并 → 删除旧文件，避免记忆膨胀

### 🛡️ 智能权限拦截 (Smart-Permission)
- **安全工具自动执行**：`read_file`、`save_memory`、`web_search` 等自动通过
- **危险操作强制拦截**：`rm`、`rmdir`、`truncate` 等自动触发 Plan Mode 确认

### 🧬 自进化与鲁棒性
- **Transcript Repair**：自动扫描补全孤儿 tool_calls，根除 API 400 错误
- **自进化规则 (L6)**：同主题反馈 ≥2 次 → LLM 生成规则 → 注入 system prompt

---

## 🚀 部署指南

### 方案 A：Docker 一键部署 (推荐/服务器端)
适用于 Linux 服务器或希望环境隔离的用户。

1.  **准备环境**：确保已安装 `docker` 和 `docker-compose`。
2.  **一键启动**：
    ```bash
    docker-compose up -d --build
    ```
3.  **扫码登录**：
    ```bash
    docker logs -f napcat
    ```

### 方案 B：本地 CLI 开发模式 (macOS/Linux)
1.  **安装依赖**：
    ```bash
    pip install -r requirements.txt
    ```
2.  **快速运行**：
    ```bash
    ./启动QQAgent.command    # 自动化启动 QQ 机器人与 Gateway
    python main.py           # 进入本地交互模式
    ```

---

## 🛠️ 工具系统

| 工具 | 分类 | 权限策略 | 功能说明 |
| :--- | :--- | :--- | :--- |
| **bash** | 系统 | **智能拦截** | 执行 Shell 命令，自动拦截 `rm` 等危险指令 |
| **save_memory** | 记忆 | 自动执行 | 持久化长期记忆，支持 5 类标签 |
| **web_search** | 联网 | 自动执行 | DuckDuckGo 深度搜索 |
| **read_image** | 视觉 | 自动执行 | 结合 Vision 模型分析本地/网络图片 |
| **file_tools** | 文件 | 自动执行 | 绝对路径下的文件读写 (write_file 受目录保护) |
| **spawn_agent** | 协作 | 自动执行 | 派发具有特定人设（Coder/Reviewer 等）的子 Agent |

---

## 📁 项目结构 (v2.2)

```text
肖亮搭建的agent/
├── main.py                     # 统一入口（--gateway/--auto-learn/--cleanup/--plan）
├── Dockerfile                  # Agent 镜像定义
├── docker-compose.yml          # NapCat + Agent 容器编排
├── 启动QQAgent.command          # macOS 一键启动脚本
│
├── agent/                      # 核心代码区
│   ├── core.py                 # 主循环 + Transcript Repair + 关键词提取
│   ├── llm.py                  # LiteLLM 封装
│   ├── compressor.py           # 上下文压缩（Head/Tail + LLM 摘要 + 熔断器）
│   ├── auto_learn.py           # 自主学习（子代理并行 + 辩论审查）
│   ├── evolution.py            # 自进化 7 模式
│   ├── cleanup.py              # 知识库清理（pro 模型批量审查）
│   ├── gateway.py              # QQ Gateway（NapCat WebSocket + HTTP API）
│   ├── dashboard.py            # HTTP+SSE server
│   ├── tools/                  # 9 个内置工具
│   ├── memory/
│   │   └── manager.py          # MEMORY.md + 时间戳进化 + 用户画像
│   └── session/
│       └── handler.py          # JSONL 持久化 + SQLite FTS5 + Transcript repair
│
├── tests/                      # 单元测试
│   ├── test_fts5_index.py      # FTS5 索引验证
│   └── test_phase2_memory.py   # 关键词/合并验证
│
├── agent/systemd/              # macOS LaunchAgent
│   └── com.xlagent.qq.plist
│
└── docs/                       # 设计文档
    ├── implementation_plan.md          # 深度进化实施计划
    ├── 对比分析-与同事Agent对比.md        # 竞品分析
    └── 升级路线总览.md                   # 升级路线
```

---

## ⚙️ 技术栈
*   **LLM 引擎**: LiteLLM (DeepSeek V4 Pro 对话 / Flash 学习)
*   **机器人框架**: NapCat (OneBot v11)
*   **全文搜索**: SQLite FTS5 + CJK 分字预处理
*   **容器化**: Docker & Docker Compose
*   **语言**: Python 3.14 (Asyncio)
*   **记忆**: MEMORY.md + JSONL + SQLite FTS5 三层存储

---
**Powered by XL Agent Team.** 实现真正的个人数字化智能助手。
