# Xl Architecture



---
<!-- 2026-05-19T09:41:22Z -->
<!-- hash:01200355bf48e48d58cd2cc4cd7dfc77 -->
### [project] tinypace-ai-desktop 是一个基于 Electron + React 的桌面端 AI 自动化测试控制台，核心架构为 AgentCore 管理组件
会话反思发现: tinypace-ai-desktop 是一个基于 Electron + React 的桌面端 AI 自动化测试控制台，核心架构为 AgentCore 管理组件生命周期和聊天循环


---
<!-- 2026-05-21T13:42:07Z -->
<!-- hash:84fa2fc21bbdf2722e4a454c66cc7fa2 -->
### [user] 用户已实现SessionHandler：基于JSONL持久化+SQLite FTS5全文索引的跨会话记忆系统，支持CJK分词和降级融合
会话反思发现: 用户已实现SessionHandler：基于JSONL持久化+SQLite FTS5全文索引的跨会话记忆系统，支持CJK分词和降级融合


---
<!-- 2026-05-22T09:27:06Z -->
<!-- hash:19561a6c6d43ab6b21d73a44b4833317 -->
### [learn] 借鉴Claude Code的section缓存策略，对小萤core.py的system prompt构建、工具定义、EVOLVED_RULES读取进行三级缓存优化
# 小萤缓存优化方案（借鉴 Claude Code）

## 结论先行

小萤当前的缓存机制只有最基础的 "人格画像一次读入缓存"，其余部分每轮都在重建。参考 Claude Code 的三层缓存架构，可以做到：**静态段100%命中DeepSeek前缀缓存，工具定义零重复构建，磁盘IO消除**。

## 现状 vs 优化点

### 问题1: System Prompt 每轮全量重建
现状: `_build_system_prompt()` 每轮都重新拼接 STATIC_PROMPT + persona_section + 动态规则 + Current Context。其中 EVOLVED_RULES.md 每次都从磁盘读文件。

根因: STATIC_PROMPT 内嵌了 {persona_section} 和 {user_address} 模板变量，导致它无法被直接缓存为纯静态字符串。

### 问题2: 没有利用 DeepSeek 前缀缓存
现状: system prompt 和 tools 虽大且稳定，但每次调用时它们被当作新字符串传入 API，DeepSeek 无法命中已缓存的 prompt 前缀。

CC 的方案: 在 system prompt 尾部插入 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` 标记，将静态段(系统指令+人格设定+工具定义)与动态段(memory block+当前时间)物理隔离。API 自动缓存 boundary 之前的内容，每轮只传增量。

### 问题3: 工具定义每轮重建
现状: `self.registry.get_definitions()` 在 `_run_loop` 中被调用，每次遍历所有注册工具重建 JSON Schema。实际上工具在初始化后不再变化。

### 问题4: 压缩时重复读记忆
现状: `compress()` 内部调 `memory.list_memories()` 做 pre-compression flush，但这已经是 memory block 注入过的数据，属于冗余 IO。

## 优化方案（按 ROI 排序）

### 方案A: 静态段预渲染缓存（ROI最高，改动最小）

在 Agent.__init__() 中，完成 persona 加载后立即预渲染 STATIC_PROMPT，缓存为 `self._cached_static_section`：

```
在 __init__ 尾部加：
self._cached_static_section = self._render_static_prompt()
self._last_rules_mtime = 0
self._cached_rules = ""
```

_build_system_prompt() 改为只拼动态部分：
```
static = self._cached_static_section  # 启动时一次渲染，永久缓存
rules = self._read_rules_with_cache()  # 仅 mtime 变化时才重读
return static + "\n" + rules + "\n" + now + cwd
```

改动: core.py 约 20 行，零新依赖，无副作用。

收益: 消除每轮 STR Build + 磁盘 IO，system prompt 前 80% 字节稳定不变 → 自然命中 DeepSeek 前缀缓存。

### 方案B: SYSTEM_PROMPT_DYNAMIC_BOUNDARY 标记注入

将 Memory Block + Current Context 从 system prompt 中剥离，放在 user message 的前缀。

具体: `_run_loop` 中，system prompt 只包含纯静态段，动态的 memory_block 追加到最后一条 user 消息头部：

```
# 改 system_prompt = cached_prompt (全量)
# 为 system_prompt = self._cached_static_section (纯静态)

# Memory block 只挂载在最后一条 user msg 前面，不污染 system
```

改动: core.py _run_loop 中约 15 行。

收益: system prompt 彻底稳定，DeepSeek 缓存命中率接近 100%，每轮省去 800-1500 token 的动态段传输。

### 方案C: 工具定义缓存

在 ToolRegistry 中加 `_definitions_cache` 和 dirty 标记：

```
get_definitions():
    if not self._dirty and self._definitions_cache:
        return self._definitions_cache
    self._definitions_cache = [tool.get_tool_definition() for tool in self._tools.values()]
    self._dirty = False
    return self._definitions_cache

register/deregister 时设置 self._dirty = True
```

改动: registry.py 约 10 行。

收益: 每轮省去 20+ 个工具函数的 schema 构建，对工具多的场景尤其显著。

## 综合架构（三合一）

```
__init__:
  预渲染 STATIC_PROMPT → self._cached_static
  工具定义缓存就绪

_run_loop (每轮):
  system = self._cached_static  # 零构建，百分百命中前缀缓存
  rules = self._read_cached_rules()  # 仅 mtime 变化才读盘
  tools = self.registry.get_definitions()  # 命中缓存，零构建

  memory_block → 只挂载在最后一条 user msg 前
  Current Context → 同上

  传给 LLM:
  messages = [
    {"role": "system", "content": self._cached_static + rules},  # 稳定！
    {"role": "user", "content": "[MEMORY BLOCK]\n{block}\n\n{user_input}"},
    ...历史对话...
  ]
```

## 最终收益估算

| 维度 | 优化前 | 优化后 | 差距 |
|------|--------|--------|------|
| system prompt 构建 | 每轮 3-5ms | 0ms（预渲染） | 消除 |
| EVOLVED_RULES 读盘 | 每轮 2 次 | mtime 变化时 1 次 | 99% 消除 |
| 工具定义构建 | 每轮 O(n*20) | O(1) 缓存命中 | 消除 |
| DeepSeek 前缀缓存 | 不命中 | 100% 命中 | 省 800-1500 token/轮 |
| 磁盘 IO | 每轮 2-3 次 | 0 次 | 消除 |

核心认知: 缓存不是"要不要"的问题，而是"在哪一层、用什么粒度、缓存什么"的问题。Claude Code 的 section 边界方案强在物理隔离——它允许你把 90% 的稳定内容锁死在前缀缓存里，只传 10% 的动态增量。小萤现在的代码只做到了"数据缓存"（persona），没做到"结构缓存"（section boundary + tool defs）。



---
<!-- 2026-05-22T09:27:52Z -->
<!-- hash:70ccc36df0eb2dceb5fd909ea2213cb0 -->
### [project] 当前system prompt每次都重新拼接，导致DeepSeek服务端前缀缓存失效，需要拆分为静态+动态部分以复用缓存
会话反思发现: 当前system prompt每次都重新拼接，导致DeepSeek服务端前缀缓存失效，需要拆分为静态+动态部分以复用缓存
