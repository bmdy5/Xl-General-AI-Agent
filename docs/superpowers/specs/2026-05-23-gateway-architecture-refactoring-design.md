# QQ 网关架构解耦与重构设计方案

本文档旨在对当前 QQ 网关架构中高度粘连的业务逻辑、定时任务、打盹疲劳度控制以及流式发包渲染进行系统性的物理剥离解耦，打造一个职责纯粹、高内聚低耦合、易于维护扩展且 100% 向下兼容的现代化 Agent 通信架构。

## 1. 当前架构混乱点剖析

在当前的代码库中，主要存在以下三处职责混杂与硬粘连：

1.1 物理网关 bot.py 职责过载
bot.py 本应只承担底层 WebSocket 长连接维持、OneBot 网络包流控收发的基础通信职责。但目前它硬塞进了：
- 动漫语音（GPT-SoVITS）物理假死探测与命令行自愈拉起逻辑；
- 每日晚上九点的电台选题智能轮询；
- 每日早晨六点的早报播客拉取与文件主动推送；
- 兼容测试用的数十个状态属性双向代理。
这导致 bot.py 充斥着系统级、业务级和通信级的多重混杂。

1.2 消息分发路由器 dispatcher.py 过于臃肿
dispatcher.py 有 740 多行，它既要负责安全拦截与特权拦截判定，又要手写极其复杂的文本 delta 流式分句、[SPLIT]与[WAIT]标签逻辑，还直接包含了打盹休眠（梦境反思进化）的逻辑和提示词大模型调用，代码高度紧耦合。

1.3 状态管理与 I/O 逻辑高度粘连
疲劳数值（_fatigue_levels）、休眠标记（_sleep_modes）以及后台梦境任务句柄（_active_sleep_tasks）均直接存储在 Dispatcher 中，且在多个异步协程中被多点读写，容易引发数据不一致或并发竞态漏洞。

---

## 2. 架构重构设计与解耦方案

为了彻底解决上述粘连，我们将网关模块划分出四个高内聚的单一职责小单元：

2.1 物理通信网关：bot.py（精简）
- 职责：只负责 WebSocket 连接维持，以及 OneBot 原始 HTTP 包发送、平滑流控令牌桶控制。
- 改变：所有定时任务、进程守护自愈、高级展示格式渲染全部剥离。

2.2 后台任务与自愈管理器：scheduler.py（新建）
- 职责：作为独立的定时任务和高可用守护中心。
- 搬移内容：
  - GPT-SoVITS 语音服务健康探测与自愈拉起逻辑；
  - 每日 21:00 定时夜间电台选题推送；
  - 每日 06:00 定时晨间播客下载与文件传输。
- 交互：通过 GatewayContext 总线或者 Bot 实例发起发包请求。

2.3 疲劳打盹与梦境管理器：fatigue_manager.py（新建）
- 职责：接管所有的脑力疲劳累加、休眠判定、高情商打盹宣告生成以及梦境反思净化。
- 搬移内容：
  - 疲劳度（_fatigue_levels）、休眠态（_sleep_modes）和梦境任务表（_active_sleep_tasks）的内部存取；
  - 私聊/群聊用脑过度打盹宣告模型交互逻辑；
  - 梦境反思后台协程（_sleep_and_dream_process）。
- 交互：提供 adjust_fatigue(group_id, inc) 简洁接口供分发器调用。

2.4 流式展示与语音控制层：presenter.py（新建）
- 职责：专门负责 Agent 吐出的 delta 流的格式切割、延迟匹配与语音发送策略。
- 搬移内容：
  - [SPLIT] 与 [WAIT:N] 物理标签切割与拟真时延逻辑；
  - [语音:情绪] 前缀提取、语音音轨切换以及 send_voice 调用。
- 交互：对外暴露 async def present_stream(agent, raw, session_key, msg_type, ...) 接口。

---

## 3. 100% 向下兼容（测试套件 0 改动原则）

重构的关键原则是绝对不破坏现有的测试用例。在 tests/test_admin_private_recovery.py 和 tests/test_scheduler_preempt.py 中，测试通过 mock 和直接读写 gw.dispatcher 或 gw._sleep_modes、gw._fatigue_levels 等属性来检验状态。

为了保证测试完美兼容，我们将采用 <b>外观模式（Facade）</b>：
- 在精简后的 MessageDispatcher 中，仍保留对 _sleep_modes, _fatigue_levels, _active_sleep_tasks 等属性的 getter 和 setter 代理，但在内部自动将其路由到底层的 FatigueManager 实例；
- 保持 dispatcher.dispatch_event 的入参和返回值 100% 保持原样；
- 保持 build_agent 组装逻辑的向后兼容，确保测试挂载的 MockAgent 能够正常替换大模型。

---

## 4. 实施清单与步骤

我们将采用小步快跑、先易后难、分阶段本地提交的策略，每一步完成后均进行单元测试审查：

步骤一：编写并提交系统设计文档（本阶段已完成）
在 docs/superpowers/specs 物理归档本设计规范，并完成本地 Git 提交。

步骤二：创建 scheduler.py 并从 bot.py 移出后台守护与定时器
- 1. 新建 agent/net_gateway/scheduler.py 文件；
- 2. 将 _daemon_loop, _trigger_night_podcast_selection, _trigger_morning_podcast_download 等逻辑安全搬移；
- 3. 在 bot.py 中初始化 scheduler 实例并启动其守护循环；
- 4. 跑 pytest tests 验证有无功能回归。

步骤三：创建 fatigue_manager.py 剥离疲劳度与梦境控制
- 1. 新建 agent/net_gateway/fatigue_manager.py 文件；
- 2. 封装 FatigueManager 类，实现疲劳增减、打盹判断、Announce 生成及 dream_process；
- 3. 修改 dispatcher.py，在 dispatcher 中实例化 FatigueManager；
- 4. 用 property 代理 _sleep_modes 等测试依赖属性；
- 5. 跑 pytest tests 确保管理员强占自愈等用例 100% 通过。

步骤四：创建 presenter.py 剥离流式渲染与语音控制
- 1. 新建 agent/net_gateway/presenter.py 文件；
- 2. 将 [SPLIT] 切分、[WAIT] 延迟、[语音:情绪] 剥除与 send_voice 交互逻辑高内聚到 StreamPresenter 中；
- 3. 简化 dispatcher._execute_agent_run，使其只负责启动 agent.run 并将 delta 委托给 StreamPresenter；
- 4. 运行全量测试进行最终核实。
