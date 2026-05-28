# KI Clustering 核心机制修复与优化设计 (Design Spec)

## 1. 背景与目标 (Background & Goals)
当前小萤的记忆向量库（KI）采用 Macro-Micro 的双层聚类架构。该架构旨在通过宏观专题（Macro）命中搜索，然后展开微观碎片（Micro）以注入上下文，从而在节省 Token 的同时提供精准记忆。
然而，当前的聚类与展开机制存在两个严重隐患，导致长效记忆会发生丢失和孤岛化：
- **Bug A（记忆黑洞）**：展开 Macro 时硬编码限制了最多展开 5 个子 Micro，导致超过 5 个历史经验的专题中，较晚或较早的记忆永远无法被 LLM 读取。
- **Bug B（聚类孤岛）**：新产生的零散经验（Orphans）在聚类时，仅与其他 Orphans 进行匹配，不会主动融入已有的相关 Macro。这导致相似的经验碎片散落各地，Macro 逐渐变得碎片化。

本次设计的目的是修复这两个底层逻辑，实现真正的“活体增量记忆网络”。

## 2. 详细设计方案 (Proposed Design)

### 2.1 修复 Bug A：废除个数限制，动态字符截断
**目标文件**：`agent/memory/clustering.py` -> `expand_macro_result` 方法
**设计逻辑**：
1. **取消强硬限制**：删除 `for cid in child_ids[:5]:` 中的 `[:5]` 截断。
2. **重排子节点优先级**：在遍历子节点之前，先去数据库查询这批 `child_ids` 的 `last_hit_at`（最近访问时间）或 `updated_at`（最近更新时间），按时间倒序排列。确保最新、最常被检索的记忆最先被拼装。
3. **基于容量的动态截断**：维持现有的 `total_chars + len(ctext) > max_content_chars` 逻辑。只要没触碰上下文安全水位（如 5000 字符），就尽可能多地展开碎片。

### 2.2 修复 Bug B：增量吸收机制 (Incremental Absorption)
**目标文件**：`agent/memory/clustering.py` -> `build_macros` 方法
**设计逻辑**：
在当前的“无向图聚类”逻辑之前，插入一个**“找干爹（寻找已有 Macro）”**的增量吸收阶段。
1. **提取已有 Macro 的 Embeddings**：在拉取到所有孤儿 Micro（`parent_id IS NULL`）后，同步拉取数据库中所有现存 Macro 的 Embeddings。
2. **交叉计算相似度**：将每个孤儿 Micro 的 Embedding 与现有 Macro 进行余弦相似度计算，门槛设定为 `> 0.82`。同时也需要通过分类（Category）与关键词（Keywords）的基础校验。
3. **吸收与合并**：
    - 如果某个孤儿 Micro 成功命中一个高相似度的 Macro，则直接将其 `parent_id` 设为该 Macro 的 ID。
    - 将该 Micro 从 `kis_fts` 全文索引中移除（避免重复检索）。
    - 提取该 Micro 的 Keywords，并集到 Macro 的 Keywords 列表中。
    - （可选/异步）标记该 Macro 需要重新生成 Summary（由于摘要生成涉及 LLM 开销，若考虑成本可先仅追加关键词和挂载，后续由专门的梳理任务更新文本）。
4. **剩余处理**：那些没有找到合适 Macro 的孤儿 Micro，继续走原来的无向图连通分量算法，互相抱团建立新的 Macro。

## 3. 边界场景与异常处理 (Edge Cases & Error Handling)
- **过大的 Macro 问题**：如果一个 Macro 不断吸收新碎片，导致其 children 数量达到上百个。由于我们在 `expand_macro_result` 阶段加入了倒序排列和按总字符数的动态截断，因此无论挂载多少个碎片，最终提取进上下文的永远是**最新的且不超过字数阈值**的那部分，不会导致 Prompt 爆炸。
- **并发写入冲突**：聚类任务运行在 `dream.py` 后台进程中。通过 SQLite 的事务控制保障数据的一致性。

## 4. 影响范围 (Impact Area)
- `agent/memory/clustering.py`：聚类引擎核心逻辑变更。
- 数据结构：表结构无需变更，完全兼容历史数据。

---
> [!IMPORTANT]  
> 请用户评审上述设计逻辑是否符合预期。如无异议，我们将进入执行阶段。
