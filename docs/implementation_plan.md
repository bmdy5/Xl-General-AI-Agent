# XL Agent 深度进化实施计划 (DeepSeek V4 专用)

> **任务背景**：你目前的记忆系统在历史检索深度、Token 消耗优化及并行协作方面仍有提升空间。本计划旨在通过实装 SQLite FTS5 索引、优化记忆注入逻辑及增强子代理派发机制，将你升级为具备“长期情景回忆”能力的生产级智能体。

---

## 阶段 1：情景回忆实装 (Episodic Memory)
**目标**：为 JSONL 消息历史增加全文检索能力。

- [ ] **1.1 增加 SQLite 索引层**：
    - 修改 `agent/session/handler.py`，引入 `sqlite3`。
    - 创建 `sessions.db`，使用 `FTS5` 虚拟表存储 `(session_id, role, content, timestamp)`。
    - 在 `append_message` 方法中，实现 JSONL 写入与 SQLite 索引同步更新。
- [ ] **1.2 增强搜索工具**：
    - 修改 `search_all_sessions` 工具。
    - 将原来的 `grep` 文件遍历逻辑替换为 `SELECT ... FROM fts_table WHERE content MATCH :query`。
    - 优化返回格式，支持返回匹配片段的上下文。

## 阶段 2：Token 极限压缩与记忆审计 (Token & Conflict)
**目标**：降低运行成本并解决记忆自相矛盾。

- [ ] **2.1 动态 Top-K 记忆注入**：
    - 修改 `MemoryManager.load_context()`。
    - 增加一个步骤：使用 Flash 模型先提取当前用户输入的 `keywords`，然后只召回最相关的 Top-5 记忆条目。
- [ ] **2.2 记忆合并 (Conflict Resolution)**：
    - 修改 `save_memory` 工具。
    - 在写入新记忆前，检测是否存在相同主题的旧记忆。若有，调用 LLM 进行合并（Merge）而非追加（Append）。

## 阶段 3：并行协作增强 (Parallel Sub-agents)
**目标**：支持主 Agent 同时派发并监控多个任务。

- [ ] **3.1 异步派发机制**：
    - 修改 `spawn_agent` 工具，使其支持 `background=True` 模式（返回一个 task_id）。
    - 修改 `core.py` 的推理循环，支持在等待子代理返回的同时，继续处理其他思考逻辑。
- [ ] **3.2 技能物化 (Skills as Configs)**：
    - 在 `on_session_end` 钩子中增加审计逻辑。
    - 将成功的高频复杂操作序列保存为 `agent/skills/*.md` 配置文件。

---

## 执行指令 (给 DeepSeek)

当你（Agent）读取此文件后，请遵循以下规则执行：
1. **最小改动原则**：不要重写现有的 `core.py` 逻辑，仅在必要位置插入 Hook。
2. **测试先行**：每完成一个子项，请创建一个 `.py` 脚本验证 SQLite 索引是否生效或 Token 是否下降。
3. **安全第一**：在修改 `handler.py` 前，请务必先对 `~/.my-agent/sessions/` 进行备份。

---

**指令触发**：请先执行 `1.1 增加 SQLite 索引层`。
