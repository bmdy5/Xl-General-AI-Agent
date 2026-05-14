# XL Agent

肖亮的个人 AI 代理。基于 ReAct 循环，支持工具调用、记忆管理、知识检索、多 Agent 协作。

## 快速开始

```bash
xl "你好"              # 单次对话
xl                    # 交互模式（/exit 退出）
xl --dashboard       # 监控面板
xl --auto-learn      # 自动学习
```

## 记忆系统

XL 的记忆分为两个层次：

### 1. 情景记忆（FTS5 全文搜索）
- 每次工具调用的上下文自动存入 JSONL
- 通过 SQLite FTS5 建立全文索引，BM25 排序
- 支持中文分词（CJK 字符自动分字处理）
- 三层降级：FTS5 → LIKE → JSONL grep
- 记忆注入时按关键词匹配度排序，只取 Top-5

### 2. 知识库（学习笔记索引）
- 自动扫描 /Users/xiaofeng/Desktop/学习笔记/ 目录
- 长文本自动分块（max_chars=500）后索引
- 在 memory_block 中显示为 "相关知识" 区域
- 首次调用时延迟初始化（不阻塞启动）

### 记忆类型

| 类型 | 用途 | 来源 |
|------|------|------|
| [user] | 用户偏好、个人信息 | 主动保存 |
| [learn] | 学习笔记、知识文档 | 自动索引 |
| [feedback] | 用户纠正、经验教训 | 会话分析 |
| [project] | 项目决策、架构记录 | 主动保存 |

### 保存与检索

```python
await mm.save("filename", "[user] 描述", "内容")
results = mm.search_memories("关键词")
mm.list_memories()       # 列出所有
mm.remove("filename")   # 删除
```

## 工具系统

所有工具通过 ToolRegistry 统一注册，支持运行时动态添加/移除。

| 工具 | 能力 |
|------|------|
| read_file | 读取文件 |
| write_file | 写入文件 |
| edit_file | 搜索替换编辑 |
| bash | 执行 shell 命令 |
| web_search | 网页搜索 |
| web_fetch | 抓取网页内容 |
| read_image | 图片分析（视觉模型） |
| image2_generate | 生成像素纹理 |
| spawn_agent | 派生子 agent 并发执行 |
| swarm | 蜂群协作（拆任务→并发 worker→聚合） |
| run_sequence | 链式执行多个工具 |
| manage_tool | 运行时注册/注销工具 |
| mcp_client | 连接外部 MCP 服务器 |
| stitch_generate | 调用 Google Stitch 生成 UI |
| save_memory | 保存/搜索/移除长期记忆 |

## 进化系统

XL 在每次会话结束后自动分析表现，生成改进规则。

```
工具审计 → 模式检测 → 规则生成 → 规则注入 → 效果验证
```

- **audit_tool_call**: 每次工具调用记录耗时、成功/失败
- **detect_task_pattern**: 发现重复操作模式
- **on_session_end**: 会话结束后提取 learnings
- **evolve_rules**: 从 feedback 记忆中生成规则
- **EvolutionEngine**: 规则存储 + 置信度追踪 + 自动注入 system_prompt

## 任务队列

支持文件持久化的定时任务，交互模式下通过 /tasks 管理：

```
/tasks                       # 列出待办
/tasks add 描述 / daily     # 添加每日任务
/tasks done task_id         # 标记完成
/tasks clear                # 清理已完成
```

## 架构

```
main.py                    # 入口（交互/单次/自动学习模式）
agent/
  core.py                  # Agent 核心（ReAct 循环）
  llm.py                   # LLM 客户端
  tui.py                   # 终端界面渲染
  tui_events.py            # 事件流处理
  compressor.py            # 上下文压缩
  evolution.py             # 审计 + 分析 + 规则生成
  evolution_apply.py       # 规则存储 + 注入 + 验证
  task_queue.py            # 定时任务队列
  memory/
    manager.py             # 记忆管理器
    fts_index.py           # FTS5 全文索引
    notes_fts.py           # 学习笔记索引
  tools/
    registry.py            # 工具注册中心
    base_tool.py           # 工具基类
    file_tools.py          # 文件读写
    ... (15 tools)
  session/
    handler.py             # 会话管理
```

## 命令参考

| 命令 | 用途 |
|------|------|
| /exit | 退出交互模式 |
| /clear | 清空对话历史 |
| /stats | 上下文用量统计 |
| /memory | 列出所有记忆 |
| /tools | 列出所有工具 |
| /mode normal\|deep | 切换模式（normal 5min/deep 2h）|
| /tasks | 管理定时任务 |