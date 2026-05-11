# XL Agent — 通用自进化 AI Agent (v2.1)

> 老肖的个人 AI Agent。ReAct 循环 + 智能权限拦截 + Docker 生产级部署。

## ✨ 核心特性

### 🛡️ 智能权限拦截 (Smart-Permission)
**效率与安全的完美平衡。** 
*   **安全工具自动执行**：`read_file`、`save_memory`、`web_search` 等无害工具由 Agent 自动决策并执行，无需人工确认，交互极其丝滑。
*   **危险操作强制拦截**：通过正则引擎实时监控 `bash` 指令。任何包含 `rm`、`rmdir`、`truncate` 等删除行为，或调用带 `delete` 字样的工具，都会自动触发 **Plan Mode**，必须经过用户 QQ 或 CLI 手动确认方可继续。

### 🧬 自进化与鲁棒性
*   **Transcript Repair (对话修护)**：核心循环内置“历史洗涤器”。在每一轮 LLM 调用前，自动扫描并补全因网络异常、断电导致的“孤儿工具请求”，彻底根除 `insufficient tool messages` 报错。
*   **自进化规则 (L6 记忆)**：当用户针对同一主题进行 ≥2 次反馈时，LLM 会提炼出规则并自动注入 `EVOLVED_RULES.md`，实现“越用越懂你”。

### 🌐 QQ Gateway 24/7 持久接入
*   **OneBot v11 协议**：通过 NapCat 集成，支持私聊/群聊、@提及过滤。
*   **持久化登录**：采用标准化的 `~/.xlagent/napcat_data` 存储，扫码一次，永久授权。
*   **分布式/云端友好**：原生支持 Docker 部署，在腾讯云等服务器上实现 24 小时在线。

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

## 📁 项目结构 (v2.1)

```text
肖亮搭建的agent/
├── main.py                     # 统一入口（支持 --gateway, --auto-learn 等）
├── Dockerfile                  # Agent 镜像定义
├── docker-compose.yml          # NapCat + Agent 容器编排
├── 启动QQAgent.command          # macOS 一键启动脚本
│
├── agent/                      # 核心代码区
│   ├── core.py                 # Agent 主循环 & 历史自动修护
│   ├── gateway.py              # QQ 网关 & 智能权限拦截器
│   ├── evolution.py            # 自进化逻辑
│   ├── tools/                  # 模块化工具箱
│   └── memory/                 # 长期记忆管理 (MEMORY.md)
│
├── agent/systemd/              # 服务化配置
│   └── com.xlagent.qq.plist    # macOS LaunchAgent 守护进程
│
└── docs/                       # 设计与规划文档
    ├── 核心技能系统.md          # 技能演进路线
    └── 问题修复与自动启动规划.md   # 本次升级的技术细节
```

---

## ⚙️ 技术栈
*   **LLM 引擎**: LiteLLM (支持 OpenAI/DeepSeek/Claude)
*   **机器人框架**: NapCat (OneBot v11)
*   **容器化**: Docker & Docker Compose
*   **语言**: Python 3.10+ (Asyncio)
*   **记忆**: 纯文本 MEMORY.md + JSONL 会话日志

---
**Powered by XL Agent Team.** 实现真正的个人数字化智能助手。
