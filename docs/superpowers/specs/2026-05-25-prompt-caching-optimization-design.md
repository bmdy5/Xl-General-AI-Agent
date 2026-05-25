# DeepSeek 90%+ 缓存命中率与多轮 ReAct 优化设计文档

本设计文档旨在解决系统在 DeepSeek 模型下 Prompt 缓存命中率低（目前仅为 10% ~ 15%）的痛点，通过精细化重构消息队列的物理排布，将缓存命中率跃升至 **90%+**，大幅削减 API 账单开销并提升响应速度。

---

## 1. 痛点分析与改进原理

### 1.1 现状与问题
当前发送给 LLM 的消息队列结构为：
1. `[0]`: 静态 System 人设（约 10k+ Tokens，不变）
2. `[1]`: 动态 System 消息（包含每分钟变化的 `Time` 以及每次请求都在变的 RAG `memory_block`）
3. `[2..N]`: 历史对话（随着轮数递增）

由于 DeepSeek 采用**自前向后连续前缀匹配**的自动缓存机制，中间 `[1]` 位置的动态变化会彻底截断匹配链条，导致占据总 Token 80% 以上的 `[2..N]` 历史对话**完全无法命中缓存**。

### 1.2 解决方案（方案 A1 + B1）
我们将动态上下文（Time + RAG）移动到**当前回合发起 User 消息的前面**：
1. `[Static System]`: 首部静态人设提示词（100% 缓存）。
2. `[Past Messages]`: 历史回合的所有已完成对话消息（随着对话进行单调递增，100% 缓存）。
3. `[Dynamic System]`: 动态环境上下文（Time + RAG），作为插入点。
4. `[Active Messages]`: 本回合发起的最新 User 消息，以及本回合内生成的 ToolCall 与 ToolResult（仅在此处不命中缓存）。

这样，在单轮会话中，除了最新用户提问和 RAG 内容，其余 90%+ 的前缀与历史都能稳定命中缓存。在多轮 ReAct 迭代中，由于插入点固定在回合发起 User 消息前，后续的 Tool 交互中，前半部分也保持完全静态，实现多轮 ReAct 过程中的极致缓存。

---

## 2. 详细设计与实现方案

### 2.1 插入点动态定位算法
在 `agent/core/react_loop.py` 中，我们在构建 `llm_messages` 时，通过算法动态找出当前回合的“发起 User 消息”：
* **定位逻辑**：从 `agent.messages` 的最尾部向前搜索，找到**最后一个 role 为 "user" 且其后没有跟随 assistant 消息**的那个 User 消息索引。
* **分片重组**：
  * 将 `agent.messages` 在该索引处切分为 `past_messages`（前历史）与 `active_messages`（当前回合包，含 User 消息及之后追加的 ToolCall / ToolResult 消息）。
  * 最终的 `llm_messages` 组装为：
    ```python
    llm_messages = []
    # 1. 写入首部静态 System 消息
    llm_messages.append({"role": "system", "content": system_prompt})
    
    # 2. 追加历史会话
    for m in past_messages:
        llm_messages.append(dict(m))
        
    # 3. 写入插入点：动态 System 消息（RAG + Time）
    llm_messages.append({"role": "system", "content": dynamic_system_prompt})
    
    # 4. 追加当前回合活动消息
    for m in active_messages:
        llm_messages.append(dict(m))
    ```

---

## 3. 影响范围与后向兼容性

* **后向兼容性**：
  * **Claude 显式缓存标记**：`setup_prompt_caching` 会照常运行。由于 Claude 缓存 index 0 和倒数第二条，我们在 `llm_messages` 重组后，Claude 仍然能精准命中 System 0 和历史消息的后半截。
  * **协议兼容**：避免了将 System 消息置于队列最尾部（部分 API 不允许尾部为 System 的限制），最新一帧消息始终为 User / Assistant / Tool，完全符合严苛的 API 规范。

---

## 4. 验证与测试方案

### 4.1 自动化测试
* 在 `tests/test_prompt_caching.py` 中新增 `test_react_loop_dynamic_insertion_point`，验证在多轮 ReAct 工具交互（包含 ToolCall 与 ToolResult）时：
  1. `dynamic_system_prompt` 被精准插入在首个发起 User 消息的前面。
  2. 验证多步 ReAct 迭代中，插入点位置完全固定，不随 Tool 消息的追加而向后漂移。

### 4.2 手动验证
* 启动网关，亮哥与小萤进行连续 3 轮以上的长对话，尾随观察控制台或 `logs/agent_activity.log` 中输出的 `📊 [UNIFIED TOKEN AUDIT]` 账单。
* **预期指标**：首轮提问后，从第二轮开始，`Hit Rate` 命中率应当直奔 **80% ~ 95%**，大模型 Token 计费成本暴降 80% 以上！
