# 敏感权限会话级全局绿灯信任机制设计规范 (Spec)

## 1. 概述与痛点

在智能安全沙箱中，当小萤在多轮对话或同轮 ReAct 推理中连续执行多个敏感指令或修改源码区时，系统会多次、高频地弹出 `🔧 [主人专属审批授权]` 物理卡片挂起等待。这导致用户必须连续多次手动回复「y」或「允许」，造成严重的刷屏冗余与糟糕的交互体验。

本规范设计了 **“敏感权限会话级全局绿灯信任（方案 A）”** 机制：亮哥在聊天中一旦对首个敏感操作授权同意，网关将开启一个 **30分钟** 的全局免审批绿灯时间窗口。在此窗口内的后续所有敏感操作都将自动免审放行并顺延保活，同时以轻量非阻塞的消息提示通知亮哥，既免除打扰又保证绝对知情。

---

## 2. 核心架构设计

### 2.1 全局动态时间窗口状态机

由于网关的事件分发器 [`dispatcher.py`](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/net_gateway/dispatcher.py) 在运行时保持长生命周期，其内部的 `AgentExecutor` 长生命周期对象是跨协程记录状态的最优温床。我们在 `AgentExecutor` 中维护全局单调时间戳成员变量 `session_approved_until`：

```text
                        +---------------------------------------+
                        |      亮哥回复「y」通过首发权限申请      |
                        +---------------------------------------+
                                            |
                                            v
                        +---------------------------------------+
                        |     在长生命周期 Executor 中记录：    |
                        |   approved_until = monotonic() + 30m   |
                        +---------------------------------------+
                                            |
                                            +-----------------------------+
                                            |                             |
                                            v                             v
                                  [在 30 分钟绿灯窗口内]            [超过 30 分钟窗口]
                                            |                             |
                                            v                             v
                        +---------------------------------------+   +-----------------------------+
                        |         后续触发敏感工具操作时:        |   |   重新触发物理 QQ 卡片审批   |
                        | 1. 自动免审放行                       |   +-----------------------------+
                        | 2. 动态顺延 approved_until += 30m     |
                        | 3. 聊天框轻量通知 "💡 [信任放行] ..."  |
                        +---------------------------------------+
```

---

## 3. 技术实现方案

在中央配置文件 [`config/settings.yaml`](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/config/settings.yaml) 中：
```yaml
SESSION_PERMISSION_TIMEOUT: 1800               # 默认为 30 分钟（1800 秒），亮哥授权一次后在此时间段内全部自动放行并动态顺延
```

在 [`agent/net_gateway/executor.py`](file:///Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/net_gateway/executor.py) 中：

1. **状态初始化**：在 `AgentExecutor.__init__` 中声明：
   ```python
   self.session_approved_until = 0.0
   ```
2. **信任判定与顺延**：当 `evt["type"] == "permission_request"` 且 `user_id == self.admin_id` 时：
   - 判定单调时间 `time.monotonic() < self.session_approved_until`，若是，直接自动免审放行。
   - 放行时，网关向聊天框发送轻量级静默友好放行提示：
     `💡 [信任放行] 检测到主人在 30 分钟会话信任期内，已自动免审放行敏感操作 [工具名]。`
   - 放行后，自动将 `self.session_approved_until` 重新刷新顺延至当前时间 + 超时时间（默认 30 分钟）。
3. **首发拦截与激活**：若不在此窗口内，正常发送物理 QQ 审批卡片挂起等待。当亮哥通过后，将 `self.session_approved_until` 置为 `time.monotonic() + timeout_val`，开启绿灯信任窗口。
