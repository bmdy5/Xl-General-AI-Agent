# 自我进化管道 Implementation Plan
> REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task.

**Goal:** 连上 session FTS5 搜索 + on_session_end 反思，让小萤具备跨会话回忆和自我反思能力

**架构:** 两个独立改动，互不依赖
- 改动A: core.py 的 _build_memory_block 加入 session FTS5 搜索
- 改动B: core.py 的 run() 末尾调用 on_session_end

**风险控制:** 每次改完跑一次基本测试，确保不崩

---

### Task 1: session FTS5 加入 MEMORY BLOCK

**Files:**
- Modify: `agent/core.py:_build_memory_block` 方法

**改动分析:**
- 目标：让 _build_memory_block 在查完记忆和笔记之后，再查一次历史会话
- 方法：调用 self.session.search_all_sessions(user_input, self.llm, max_results=3)
- 返回结果格式：包含 session_id、role、snippet 带高亮上下文
- 注入格式：放在「相关知识」部分下方，标注「来源: 历史会话」
- 风险：self.session 可能为 None（非会话模式），需判空

- [ ] **Step 1: 读 _build_memory_block 完整代码确认入口**
  - 读 `agent/core.py` 第 655 行附近
  - 找到记忆搜索和笔记搜索的代码位置
  - 确认 self.session 的类型和可用方法

- [ ] **Step 2: 在笔记搜索之后加入 session 搜索**
  - 在 `note_results` 处理完之后
  - 加 `if self.session and hasattr(self.session, 'search_all_sessions'):`
  - 调用 `session_results = await self.session.search_all_sessions(enhanced_query, self.llm, max_results=3)`
  - 格式化为 `## 相关知识（来源: 历史会话）`
  - 用 snippet 内容，截取前 200 字

- [ ] **Step 3: 测试改动不崩**
  - 跑 `python -c "from agent.core import Agent; print('import ok')"` 检查导入
  - 确认无语法错误

---

### Task 2: 连上 on_session_end 反思

**Files:**
- Modify: `agent/core.py:run()` 方法末尾

**改动分析:**
- on_session_end(agent) 在 evolution.py 已写好
- 功能：对话结束时自动反思，提取 learnings 存到 memory
- 需要在 run() 的 finally 块或末尾处调用
- 用 asyncio.create_task 确保不阻塞主流程
- 风险：要确保 agent 有 memory 属性，on_session_end 内部已有 try/except

- [ ] **Step 1: 确认 on_session_end 的函数签名和内部逻辑**
  - 读 `agent/evolution.py` 第 89 行附近的 on_session_end 函数
  - 确认它是否需要特殊参数

- [ ] **Step 2: 在 run() 方法末尾加入调用**
  - 在 `core.py` 的 run() 的 finally/末尾处
  - 加 `if len(self.messages) >= 4: asyncio.create_task(on_session_end(self))`

- [ ] **Step 3: 测试改动不崩**
  - 检查语法
  - 确保 on_session_end 的 try/except 能兜住所有异常

---

### Task 3: 端到端验证

**Files:**
- 无代码改动，手动验证

- [ ] **Step 1: 验证 方案1 — 搜历史对话**
  - 改完后，问亮哥一个问题：「我之前跟你说过股票跌停的事吗？」
  - 预期：我能从 session FTS5 搜到相关内容

- [ ] **Step 2: 验证 方案2 — 自动反思**
  - 跑一轮正常对话
  - 检查 `~/.my-agent/memory/` 是否有 `reflect_*` 文件生成

- [ ] **Step 3: 验证两边不冲突**
  - 同时跑两轮对话，确认两边都正常工作
