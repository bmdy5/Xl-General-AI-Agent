# QQ 消息网关解耦架构深度优化与竞态消除设计方案

## 一、 概述与优化背景

在对 refactor/architecture-decouple 分支网关的全面审查中，我们盘点并确定了当前高度解耦架构下的 7 项跨层直接访问、隐式耦合以及并发抢占竞态隐患。虽然系统已能够通过全量测试并实现真实消息推送，但这些设计隐患如果不加以彻底治理，将在未来的迭代中演化为难以调试的竞态 Bug 与技术债。

本方案旨在为这 7 项缺陷提供高内聚、零死角、企业级规范的系统自愈与重构设计。

---

## 二、 7 项缺陷的具体治理设计

### 1. 消除僵尸属性 bot._pending_perms
- 现状剖析: bot.py 初始化了空的 self._pending_perms，而真正工作的审批队列在 executor._pending_perms。dispatcher 强制进行了覆盖。这导致 bot._pending_perms 沦为可能读错的僵尸属性。
- 优化设计: 移除 bot.py 的原始空字典初始化。采用外观模式（Facade）在 bot.py 中为 _pending_perms 提供 property 属性代理，直接穿透路由到 self.dispatcher._pending_perms（其已绑定至同一个 executor._pending_perms 字典对象）。
- 收益说明: 完美保持外部测试套件向下兼容，且全局仅保留一个物理审批字典，干净彻底。

### 2. 消除 FatigueManager 绕路环形调用
- 现状剖析: FatigueManager 在触发打盹宣告时，跨层调用 bot 上的 hasattr(_generate_private_fatigue_announcement) 方法，而 bot 本身并不处理该业务，又通过 proxy 调回 dispatcher，最后实际上转了一圈回到 FatigueManager 自身定义的私有方法。
- 优化设计: 彻底去掉 fatigue_manager.py 中 hasattr(self.bot, ...) 绕路分支。当需要产生打盹宣告时，FatigueManager 直接在本地调用自身的 _generate_private_fatigue_announcement 与 _generate_fatigue_announcement 方法，实现 100 percent 逻辑闭环。
- 收益说明: 缩短调用链，消除不必要的跨层依赖。

### 3. 补齐打盹宣告链路中丢失的 sender_name 数据
- 现状剖析: 绕路调用 bot 时签名不包含 sender_name，导致打盹宣告无法感知当前用户昵称，在最后退化为空字符串。
- 优化设计: 在 FatigueManager 直接进行本地调用的接口中，显式向 _generate_private_fatigue_announcement 传递 sender_name，并由该方法结合人设拼装出高情商的可爱打盹用脑过度吐槽。
- 收益说明: 彻底补全用户元数据，杜绝对话体验缺陷。

### 4. 任务队列与抢占状态的公开 API 抽象封装
- 现状剖析: dispatcher 与 executor 散落了大量直接对私有属性 hasattr(self.bot, "_message_queues") / getattr(self.bot, "_current_tasks") 的底层窥探与操作，强耦合了 bot 内部存储。
- 优化设计: 在 bot.py (QQGateway) 上进行高内聚的行为封装，对外暴露出 6 个类型安全的标准公开 API：
  - def get_active_task(self, session_key: str) -> object
  - def set_active_task(self, session_key: str, task: object) -> None
  - def remove_active_task(self, session_key: str) -> None
  - def enqueue_message(self, session_key: str, event: dict, raw: str) -> None
  - def pop_queued_message(self, session_key: str) -> tuple
  - def has_queued_messages(self, session_key: str) -> bool
- 收益说明: 物理收拢任务逻辑至编排层 bot，dispatcher 与 executor 仅面向公开 API 编程，彻底解开强耦合。

### 5. 播客状态隐式桥接类型安全化
- 现状剖析: scheduler.py 通过 getattr 反射操作 dispatcher 的 _waiting_podcast_topic 与 _podcast_choices 字典，缺乏类型安全与防空判定。
- 优化设计: 在 bot.py 上定义标准的代理接口：
  - def get_waiting_podcast_topic(self) -> dict
  - def get_podcast_choices(self) -> dict
  这两者内部优雅代理至 self.dispatcher，提供安全的 dict 返回。
- 收益说明: Scheduler 直接面向 bot 暴露的公开方法调用，实现了坚固的类型保障。

### 6. 并发抢占取消后的协程竞态风险消除
- 现状剖析: 抢占发生时，dispatcher 对 active_task.cancel() 并 pop 掉 _current_tasks 中的键。由于 cancel() 会导致正在运行的大模型协程抛出 CancelledError，其 finally 块仍会被触发。finally 块会从队列中 pop 出积压的下一个任务并派发；而与此同时 dispatcher 已经开始为抢占消息派发了新任务。这导致同一个会话在毫秒级竞态中拉起了两个并行推理协程，极其危险！
- 优化设计: 在 executor.py 协程的 finally 块中，派发积压任务前增加当前任务句柄的自身性校验判定：
  - 获取当前正在退出的 asyncio.current_task()。
  - 只有当 bot 的 _current_tasks[session_key] 中登记的活跃任务依然是当前 current_coro 时，才允许从队列中 pop 并派发下一个任务。
  - 如果 _current_tasks 登记的句柄已经被 dispatcher 改写（即抢占任务），则 finally 块静默退出，决不去 pop 队列。
- 收益说明: 完美解决了毫秒级的高并发协程竞态，利用极其精简的协程校验保护了 Agent 内存免受并发写入污染。

### 7. Presenter 依赖层级扁平化
- 现状剖析: Presenter 目前依赖 dispatcher，并通过它代理 _log_activity_dispatcher 与 _count_tokens 方法，形成了 Presenter -> Dispatcher -> Executor 的多层中转。
- 优化设计: StreamPresenter 在初始化时，改为直接依赖并传入实例化它的调用者 executor 实例（__init__(self, executor)）。Presenter 直接通过调用 self.executor 上的对应方法来记录日志与计数 Token。
- 收益说明: 斩断无谓的跨层依赖，令依赖关系彻底扁平与直接。

---

## 三、 实施计划

根据系统稳定性与零退化原则，我们制定如下三步走方案：

1. 步骤一：在 bot.py 上实现标准 API 并重构属性代理 Facade 链路（针对问题 1, 4, 5）。
2. 步骤二：进行 FatigueManager 本地闭环，传递 sender_name，以及 StreamPresenter 扁平化依赖改造（针对问题 2, 3, 7）。
3. 步骤三：实装 executor.py 的协程 finally 锁判定以彻底消除抢占竞态风险，并开展全量回归测试与真实发包测试（针对问题 6）。
