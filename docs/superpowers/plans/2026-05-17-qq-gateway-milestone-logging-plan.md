# AI 意图自进化驱动与微广播日志系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 QQ Gateway 中 Agent 专属女性极客伙伴“小肖”的性格驯化、工具入参与命令报错的透明同步、多任务自适应抢占与 100% 大脑驱动排队调度，以及**人格画像自检手册 (Persona Profile) 的定时自我反思、整合与自进化长期性格闭环**。

**Architecture:** 在 `agent/core.py` 中重塑 `STATIC_PROMPT`，使之能够动态载入 `persona_profile.json` 手册。在 `agent/gateway.py` 中引入 `asyncio.Task` 抢占中断与 `FIFO` 队列。强占中断时通过大模型流式确认挂起；排队等待时通过 `await agent.llm.chat(...)` 异步调起小肖极速单轮秒回懂事安抚话，在每次长任务执行结束的 `finally` 中自动拉起 LLM 对人格自画像进行自我反思与整定。

**Tech Stack:** Python 3, Asyncio, LiteLLM, JSON, File I/O.

---

### Task 1: 小肖人格画像自画像数据结构与 `core.py` 系统装配

**Files:**
- Modify: `agent/core.py:32-48` (重塑 `STATIC_PROMPT`)
- Modify: `agent/core.py:80-90` (初始化加载画像)
- Modify: `agent/core.py:529-545` (动态编织人格画像系统提示词)

- [x] **Step 1: 重塑静态系统提示词 `STATIC_PROMPT` 预留人格插槽**
- [x] **Step 2: 在 `Agent.__init__` 中自动初始化小肖自检人格画像**
- [x] **Step 3: 重构 `_build_system_prompt` 动态拼装人格自画像**
- [x] **Step 4: 编译校验与提交**

---

### Task 2: 审计日志写入器 `_log_activity` 实现

**Files:**
- Modify: `agent/gateway.py:40-47`
- Modify: `agent/gateway.py:368-368`

- [x] **Step 1: 在 `QQGateway.__init__` 中添加日志文件定义**
- [x] **Step 2: 在 `gateway.py` 中编写 `_log_activity` 辅助方法**
- [x] **Step 3: 运行脚本编译校验**
- [x] **Step 4: 提交代码**

---

### Task 3: 100% 大脑驱动的多任务智能抢占排队调度器与人格自整定管理器

**Files:**
- Modify: `agent/gateway.py:40-47` (添加调度容器初始化)
- Modify: `agent/gateway.py:205-295` (重构消息拦截、AI直觉分类器、气泡秒回、长期记忆性格反思合并)

- [x] **Step 1: 初始化调度容器**
- [x] **Step 2: 重构 `_handle` 引入 AI 直觉分类器与排队气泡/AI 动态秒回**
- [x] **Step 3: 实现 `_execute_task` 并加入对话后小肖人格自反思整理机制**
- [x] **Step 4: 编写 `_tool_detail` 函数**
- [x] **Step 5: 语法编译校验**
- [x] **Step 6: 提交代码**

---

### Task 4: 完整离线单元仿真测试

**Files:**
- Create: `tests/test_scheduler_preempt.py`

- [x] **Step 1: 编写多任务调度及小肖自研人设反思更新测试文件**
- [x] **Step 2: 运行测试验证其通过**
- [x] **Step 3: 提交代码**
