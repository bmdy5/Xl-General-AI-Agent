# 对比分析：XL Agent vs 同事 Coding Agent

> 基于 README + 源码的逐项对比，识别可行的升级方向。

---

## 一、总览

| 维度 | 同事 Agent | XL Agent | 差距 |
|------|-----------|----------|------|
| 核心循环 | ReAct，20 步，工具失败自省+熔断 | ReAct，200 步，仅有压缩熔断 | 缺工具失败自省 |
| 记忆存储 | ChromaDB 向量库 + m3e-base | MEMORY.md 纯文本 | **最大差距** |
| 记忆检索 | 语义搜索 + 关键词兜底 | LLM 从文件名列表选 | 缺向量搜索 |
| Token 计量 | tiktoken 精确 | 混合估算（中文~2，英文~4） | 精度低 |
| 权限系统 | 三级（允许/询问/禁止），会话级 | 无 | 缺 |
| 工具数量 | 10 | 9 | 接近 |
| 工具类型 | 含 grep、list_dir、code_exec、http | 含 image2、read_image、spawn_agent | 各有千秋 |
| 写后验证 | py_compile / JSON.parse / node --check | 无 | 缺 |
| 子代理 | 4 角色，SubMemory 经验传递 | 5 角色，无 SubMemory | 缺 SubMemory |
| QQ Gateway | FastAPI HTTP，会话管理+限流+超时淘汰 | aiohttp WebSocket，手动隔离 | Gateway 架构弱 |
| 自主学习 | 无 | 辩论审查+并行学习+清理 | **XL 独有** |
| 自进化 | 仅技能提取 | 7 模式（审计/反思/规则/技能追踪等） | **XL 独有** |
| Plan Mode | LLM 复杂度评估 + 计划注入 | plan_ready 事件 + asyncio.Event 确认 | **XL 更完善** |

---

## 二、分模块对比

### 2.1 核心循环

| | 同事 | XL |
|---|---|---|
| 模式 | `chat()` 方法内 while | `_run_loop()` AsyncGenerator |
| 流式 | 文本流式，工具非流式 | 全流式（思考+文本+工具） |
| 工具失败处理 | ≥2 次失败注入自省提示，≥3 次熔断 | 无自省机制 |
| 规划前置 | LLM 判复杂度 → 预生成计划注入 system | plan_mode 参数 → plan_ready 暂停等用户确认 |

**可升级：** 工具连续失败自省提示。

### 2.2 记忆系统（最大差距）

| 层级 | 同事 | XL |
|------|------|-----|
| L1 会话 | JSON 文件 | JSONL append-only + os.fsync |
| L2 压缩 | LLM 摘要，阈值 60% 上下文 | Head/Tail + LLM 摘要，阈值 75%（900K/1M） |
| L3 知识 | **ChromaDB KI**：去重/合并/新鲜度/语义搜索 | MEMORY.md 5 类 flat file |
| L4 实体 | **结构化实体提取** + 语义+关键词混合搜索 | LLM 合成用户画像（非结构化） |
| L5 核心 | 自动注入，超过 3% 上下文自动压缩 | STATIC_PROMPT + 动态段 + [MEMORY BLOCK] |
| L6 技能 | **ChromaDB Skill**：版本追踪/自动合并相似流程 | 文件系统 .md 技能 + usage_count 追踪 |

**可升级（按优先级）：**

1. **向量语义搜索**（高优先）：ChromaDB + Embedding 模型。当前 LLM 从文件名列表选记忆，几十条够用，上百条后效果会下降
2. **tiktoken 精确计量**（中优先）：当前估算有 20-30% 误差
3. **L4 结构化实体提取**（低优先）：从 KI 中提取项目/人/工具的事实

### 2.3 工具系统

**同事有、XL 没有的：**

| 工具 | 用途 | 优先级 |
|------|------|--------|
| `grep_search` | 目录内文本/正则搜索，跳过 node_modules/.git | **高** |
| `list_directory` | 列出目录结构，递归+glob 过滤 | **高** |
| `code_executor` | 沙箱 Python 执行，白名单 builtins | 中 |
| `http_request` | HTTP GET/POST/PUT/DELETE | 中 |

**XL 有、同事没有的：**

| 工具 | 用途 |
|------|------|
| `read_image` | Mimo vision API 图片分析 |
| `image2_generate` | AI 像素图生成 |
| `save_memory` | 持久化记忆 CRUD |

### 2.4 权限系统

同事有完整的三级权限，XL **完全没有权限管理**：

```
同事: DEFAULT_ALLOW → DEFAULT_ASK → ALWAYS_DENY
      安全工具自动过    危险工具询问    手动禁止

XL:   无权限检查，所有工具直接执行
```

**可升级：** 三级权限系统。当前 CLI 和 QQ Gateway 都没有工具确认机制（除了 Plan Mode）。

### 2.5 写后验证

同事在 `file_write` 成功后自动语法检查：

- Python → `py_compile`
- JSON → `json.loads`
- JS/TS → `node --check`

XL 完全没有。**可升级。**

### 2.6 Sub-agent SubMemory

同事的 Sub-agent 有 SubMemory：同类子代理（dev/debug/deploy/research）之间通过独立记忆文件传递经验，越用越好。

XL 的 SpawnAgentTool 每次纯净启动，无经验积累。**可升级。**

### 2.7 QQ Gateway

| | 同事 | XL |
|---|---|---|
| 传输 | HTTP POST（NapCat 推送） | WebSocket（主动连接 NapCat） |
| 框架 | FastAPI | aiohttp 裸写 |
| 会话管理 | SessionManager，50 并发，30min 超时淘汰 | 手动 dict 管理，无超时 |
| 限流 | 10 msg/min/用户 | 无 |
| 健康检查 | `/health` 端点 | 无 |
| Plan Mode | 无远程确认 | QQ 消息确认（独有） |

**可升级：** 会话超时淘汰、限流、健康检查端点。

---

## 三、升级优先级排序

```
高优先（本周可做）:
  1. grep_search + list_directory 工具     → 补齐基础工具缺口
  2. 权限系统（三级）                       → CLI 和 Gateway 的工具安全
  3. 写后验证                              → file_write 质量保障

中优先（下周可做）:
  4. tiktoken 精确 token 计量               → 压缩时机更准确
  5. 工具连续失败自省提示                    → 减少 Agent 死循环
  6. QQ Gateway 会话超时 + 限流             → 生产稳定性

低优先（等向量搜索上游就绪）:
  7. ChromaDB + m3e-base 语义搜索           → 记忆检索升级
  8. 实体记忆结构化提取                      → L4 升级
  9. SubMemory 子代理经验传递               → 子代理越用越好
```

---

## 四、XL 已经领先的部分（保持）

这些是 XL 独有或更优的，不需要改：

- **自主学习**：子代理并行 + 辩论审查，同事完全没有
- **自进化 7 模式**：审计/反思/规则/技能追踪，同事只有技能提取
- **知识库清理**：pro 模型批量审查，同事没有
- **Plan Mode**：asyncio.Event 暂停→确认→继续，比同事的"注入计划文本"更可控
- **QQ Plan Mode 远程确认**：同事 Gateway 无此功能
- **图片分析**：Mimo vision API，同事没有
- **[SPLIT]/[WAIT:N]**：QQ 消息分段+间隔，同事没有
- **200 步上限**：同事只有 20 步
