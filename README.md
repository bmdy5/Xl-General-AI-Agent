# XL Agent — 通用 AI Agent

> 老肖的个人 AI Agent。ReAct 循环 + 自进化 + 自主学习。

## ✨ 特性

### 🔄 ReAct 推理循环

Think → Act → Observe 循环，最多 200 步自主推理。统一流式/非流式 `_run_loop(stream=bool)`，支持实时打字机效果和工具调用状态展示。SIGINT 优雅打断不退出，连续 3 次压缩失败自动熔断。

### 🧠 记忆系统

| 层级  | 名称    | 说明                                                          |
| --- | ----- | ----------------------------------------------------------- |
| L1  | 会话持久化 | JSONL append-only + os.fsync 崩溃安全，跨会话恢复                     |
| L2  | 上下文压缩 | Head/Tail 分割 + LLM 结构化摘要，超过 90 万 token 触发                   |
| L3  | 长期记忆  | MEMORY.md 索引 + 5 类记忆（user/feedback/project/learn/reference） |
| L4  | 用户画像  | LLM 从所有 user+feedback 记忆中合成深层画像                             |
| L5  | 记忆注入  | [MEMORY BLOCK] 每轮隔离注入，上限 4000 字符                            |
| L6  | 自进化规则 | 同主题反馈 ≥2 次 → LLM 生成规则 → 注入 system prompt                    |

**动态记忆注入**：偏好类问题自动优先召回 user+feedback 类型记忆，LLM 按查询相关性选择（不只是时间戳排序）。

### 🧬 自进化系统（7 模式）

| 模式 | 说明 |
|------|------|
| after_tool_call 审计 | 工具执行后自动检测学习价值 → 自动存记忆 |
| on_session_end 反思 | 会话结束生成摘要 + 提取知识 + 检测技能 |
| 任务→技能转化 | 检测重复多步操作模式 → 自动创建技能文件 |
| Flash 记忆选择 | LLM 按查询相关性选记忆，替代纯时间戳排序 |
| 偏好专用召回 | 问偏好时只搜 user+feedback 类型 |
| 技能改进追踪 | 记录技能 usage_count + success_count + 自动版本号 |
| 自进化规则 | feedback ≥2 → LLM 生成规则 → 写入 EVOLVED_RULES.md |

### 📋 Plan Mode

LLM 检测到工具调用时先暂停，展示计划 + 工具列表，用户确认后执行。支持 CLI 交互确认和 QQ 远程确认两种方式。

### 🤖 Sub-agent 委派

| 角色 | 说明 |
|------|------|
| coder | 资深软件工程师，写高质量代码 |
| reviewer | 代码审查专家，找 bug/安全漏洞 |
| debugger | 调试专家，定位根因给修复方案 |
| architect | 系统架构师，全局视角分析 |
| general | 通用助手，完成分配的任务 |

- 独立上下文，纯净启动不继承主 Agent 记忆
- 递归深度上限 3 层，超时 120s
- 主 Agent 通过 `spawn_agent` 工具委派

### 🎓 自主学习

```
记忆库提取兴趣 → spawn 子代理并行搜索学习 → 交叉质疑 → 辩护 → 双评审 → 双通过入库
```

- **兴趣驱动**：反馈驱动 60% + 技术栈兴趣 40%
- **辩论审查**：激进派 vs 保守派 + 魔鬼代言人 → 辩护 → 双评审独立打分
- **双层模型**：flash 模型提取 + pro 模型审查
- **知识分类**：后端 / 前端 / AI / 运维 / 技能
- **定时运行**：macOS launchd 每天 10:00 自动学习

### 🧹 知识库清理

pro 模型批量审查已有知识，删重、去杂、合并。极严标准：必须有代码/命令/配置/数字才保留，纯概念/教程大纲/商业推广直接删除。

### 🔒 安全设计

- **断点保护**：上下文压缩不在 tool_use/tool_result 链中间切断
- **熔断器**：3 次压缩失败 → 停止压缩，30 分钟后自动重置
- **Transcript repair**：自动修复孤儿 tool_calls/tool_result 配对
- **SIGINT 优雅打断**：不退出进程，只中断当前 Agent 执行

## 🛠️ 内置工具

| 工具 | 功能 |
|------|------|
| read_file | 读取文件内容（绝对路径，上限 100KB） |
| write_file | 写入文件（自动创建目录） |
| bash | 执行 shell 命令（60s 超时，输出截断 50KB） |
| web_search | DuckDuckGo 搜索（最多 10 条） |
| web_fetch | 网页全文抓取（去 HTML/CSS/JS，上限 30KB） |
| read_image | Mimo vision API 图片分析（支持 png/jpg/gif/webp） |
| image2_generate | AI 像素风图片生成 |
| spawn_agent | 派发子 Agent 执行独立任务 |
| save_memory | 持久化记忆管理（新增/替换/删除/读取） |

## 🚀 快速开始

### 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入 MYAGENT_API_KEY 等配置
```

### CLI 交互

```bash
python main.py                    # 交互模式
python main.py "读一下 README"     # 单次模式
python main.py --plan             # Plan mode（工具执行前确认）
python main.py --gateway          # QQ Gateway
python main.py --auto-learn       # 自主学习
python main.py --cleanup          # 知识库清理
```

### QQ Gateway

```bash
NAPCAT_WS_URL=ws://localhost:3001 NAPCAT_HTTP_URL=http://localhost:3000 python main.py --gateway
```

通过 NapCat + OneBot v11 协议接入 QQ。支持按用户/群隔离会话，@提及过滤，Plan mode 远程确认。

### 自主学习

```bash
python main.py --auto-learn       # 手动运行
./auto_learn.sh                   # launchd 定时任务
```

## ⌨️ CLI 指令

| 指令 | 说明 |
|------|------|
| `/exit` | 退出（自动反思 + 进化 + 规则检测） |
| `/clear` | 清空对话历史 |
| `/tools` | 查看可用工具及描述 |
| `/memory` | 查看记忆列表 |
| `/stats` | 查看上下文用量 / 消息数 / token 数 |
| Ctrl+C | 打断当前 Agent 执行（不退出） |

## 📁 项目结构

```
搭建agent/
├── main.py                     # CLI 入口（6 种模式）
├── requirements.txt            # litellm + python-dotenv + aiofiles + ddgs
├── .env                        # API key + 模型配置
├── README.md                   # 本文件
├── CLAUDE.md                   # AI 协作规范
│
├── agent/                      # 核心代码
│   ├── core.py                 # Agent 主循环（while-true + AsyncGenerator）
│   ├── llm.py                  # LiteLLM 封装（chat + chat_stream）
│   ├── compressor.py           # 上下文压缩（Head/Tail + LLM 摘要 + 熔断器）
│   ├── auto_learn.py           # 自主学习（子代理并行 + 辩论审查）
│   ├── evolution.py            # 自进化（7 模式）
│   ├── cleanup.py              # 知识库清理（pro 模型批量审查）
│   ├── gateway.py              # QQ Gateway（NapCat WebSocket + HTTP API）
│   ├── tools/                  # 工具系统
│   │   ├── base_tool.py        # BaseTool ABC + ToolResult
│   │   ├── registry.py         # ToolRegistry 单例
│   │   ├── bash_tool.py        # shell 执行
│   │   ├── file_tools.py       # read_file + write_file
│   │   ├── web_search_tool.py  # DuckDuckGo 搜索
│   │   ├── web_fetch_tool.py   # 网页全文抓取
│   │   ├── read_image_tool.py  # Mimo vision 图片分析
│   │   ├── image2_tool.py      # AI 像素图生成
│   │   ├── memory_tool.py      # save_memory（5 类记忆管理）
│   │   └── spawn_agent_tool.py # 子 Agent 委派
│   │
│   ├── memory/
│   │   └── manager.py          # MEMORY.md + 时间戳进化 + 用户画像
│   │
│   └── session/
│       └── handler.py          # JSONL 持久化 + Transcript repair + 跨会话搜索
│
└── docs/                       # 技术设计文档
    ├── 升级路线总览.md
    ├── 升级设计-阶段2-工具系统.md
    ├── 升级设计-阶段3-上下文压缩.md
    └── 升级设计-阶段4-自动记忆.md
```

## ⚙️ 技术栈

| 组件 | 选型 |
|------|------|
| LLM 接口 | LiteLLM（多 provider 一键切换） |
| 模型 | DeepSeek V4 Pro（对话）/ DeepSeek V4 Flash（学习） |
| 核心循环 | while-true + AsyncGenerator |
| 工具抽象 | BaseTool ABC（抄 tinypace） |
| 记忆存储 | MEMORY.md 零依赖纯文本 |
| 记忆注入 | [MEMORY BLOCK] 隔离注入（抄 hermes） |
| 上下文压缩 | Head/Tail + LLM 摘要 + 熔断器 |
| QQ 接入 | NapCat + OneBot v11 |
| 语言 | Python 3.14 + async/await |
