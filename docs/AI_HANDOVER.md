# XL Agent 项目架构与开发者交接文档

> [!CAUTION]
> **核心准则：系统变更必须同步更新本文档**
> 本文档为 XL Agent 的架构设计与运行规范。后续开发者在升级迭代中，若对系统进行目录迁移、核心机制调整或导入路径重构，必须同步更新本文档，确保文档与代码一致，避免冷启动延迟。

---

## 1. 项目目录结构

项目已按微服务规范完成解耦，各模块归属如下：

```text
Xl-General-AI-Agent/ (项目根目录)
├── agent/                      <-- Python 源码包
│   ├── core/                   <-- 核心逻辑
│   │   ├── bootstrap.py        <-- 系统组装、依赖注入与工具注册
│   │   ├── config.py           <-- 从 config/settings.yaml 加载配置
│   │   ├── cleanup.py          <-- 守护进程自愈、内存与旧进程清理
│   │   ├── gateway.py          <-- QQ Gateway 的 Facade 代理层
│   │   ├── agent.py            <-- 核心 Agent 状态机
│   │   ├── react_loop.py       <-- ReAct 循环 (时间戳锁定、Prompt Caching、死锁熔断)
│   │   └── llm.py, compressor.py, task_queue.py
│   ├── resources/              <-- 静态资源与角色数据
│   │   └── default_persona.json
│   ├── net_gateway/            <-- QQBot & 抖音双网关
│   │   ├── logger.py           <-- 活动日志追踪
│   │   ├── douyin_browser.py   <-- 抖音浏览器 CDP 管理
│   │   ├── douyin_dom_poller.py <-- 抖音私信 DOM 扫描与提取
│   │   ├── douyin_dom_sender.py <-- 抖音消息发送
│   │   └── douyin_bot.py       <-- 抖音独立网关进程主控 (端口 9000)
│   ├── tools/                  <-- 内置工具包
│   │   ├── media/
│   │   │   └── send_image_tool.py <-- 公共发图组件 (本地图片 → COS → QQ 推送)
│   │   └── registry.py, base_tool.py 等
│   └── memory/, ui/, evolution/
├── config/
│   ├── settings.yaml           <-- 中央配置 (模型 key、NapCat ws/http、Stitch 等)
│   └── .stitch_env             <-- Stitch MCP 环境配置
├── agent_memory/               <-- 统一记忆大脑 (四叶结构，三级认知架构物理落点)
│   ├── skills/                 <-- 第一级：核心技能区
│   ├── experiences/            <-- 第二级：动态经验区
│   ├── context/                <-- 上下文记忆 (coworker JSON)
│   └── core/                   <-- 第三级：知识库 (memories.db, gitignored)
├── logs/                       <-- 运行时日志
│   ├── chat.log                <-- 对话全貌：用户输入、工具调用、回复、Token审计
│   ├── system.log              <-- 系统运维：网关状态、错误、记忆自愈
│   └── startup.log             <-- 启动脚本输出
├── scripts/                    <-- 开发调试脚本
│   ├── debug_run.py
│   ├── debug_stitch.py
│   └── deepseek_key_scanner.py
├── tests/                      <-- 单元测试 (pytest)
│   ├── test_deadlock_fuse.py
│   └── test_*.py
├── main.py                     <-- 唯一启动入口
├── Dockerfile & docker-compose.yml
├── Makefile
├── pytest.ini
└── .gitignore & .env.example
```

---

## 2. 核心开发约束

后续开发中需严格遵守以下约束：

### 约束 1：`agent/core/gateway.py` 引入 `net_gateway` 必须使用绝对导入

`gateway.py` 位于 `agent/core/` 子包下，而网络协议包在 `agent/net_gateway/`。若使用相对导入 `from .net_gateway.bot ...`，Python 会在 `agent/core/net_gateway` 下寻址导致 `ModuleNotFoundError`。

* **规范**：必须使用绝对导入：
  ```python
  from agent.net_gateway.bot import QQGateway, main
  ```

### 约束 2：人设模板加载路径必须使用 `parents[1]`

`agent/core/agent.py` 中的人设资产已迁移至 `agent/resources/` 目录。

* **规范**：初始化画像缓存时使用：
  ```python
  template_file = Path(__file__).resolve().parents[1] / “resources” / “default_persona.json”
  ```

### 约束 3：元配置文件保持在项目根目录

`pytest.ini`、`Makefile`、`Dockerfile`、`requirements.txt` 等元文件必须保留在根目录，不得移入子包。否则 IDE 和 pytest 将无法在根目录直接运行测试套件。

---

## 3. 已实现的核心机制

### ReAct 死锁熔断 (Deadlock Fuse)

* **位置**：`agent/core/react_loop.py` → `run_loop`
* **机制**：同一工具在同一 ReAct 窗口内以相同参数被连续调用 ≥4 次时，判定 LLM 陷入死循环，触发熔断并反馈警告信息引导 LLM 自我调整。
* **测试**：`tests/test_deadlock_fuse.py`

### 环境变量自愈与容灾鉴权

* **位置**：`agent/core/llm.py`
* **机制**：
  1. `_sync_environ_keys`：在 LiteLLM 调用 `acompletion` 前，将 `api_key` 和 `api_base` 注入 `os.environ`，解决灾备切换时的鉴权丢失。
  2. 灾备密钥继承：主鉴权为空时自动继承全局 `deepseek_api_key` 及 Base 端点。
  3. `total_tokens` 容错：`Agent.__init__` 中初始化为 0，避免单测直调 ReAct loop 时 `AttributeError`。

### DeepSeek 官方 API 强制路由 (2026-05-24)

* **位置**：`agent/core/llm.py`
* **机制**：
  1. 模型名包含 `deepseek` 时，强制路由至官方 `DEEPSEEK_API_KEY`，基址绑定 `https://api.deepseek.com/v1`。
  2. 已移除 Mimo 中转兜底逻辑，DeepSeek 不再经过 Mimo。
  3. 非 DeepSeek 模型（如 Vision 系列）继续使用 Mimo 的 `api_key` 和 `api_base`。

### GPT-SoVITS 语音服务管理 (2026-05-24)

* **位置**：`agent/voice/tts.py` & `agent/net_gateway/scheduler.py`
* **机制**：
  1. 无语音对话时服务不启动，内存占用 0。
  2. 检测到 `[语音:情绪]` 标记时，后台执行 `api_v2.py -d cpu -ll half` 拉起服务（冷启动约 2s），200ms 轮询等待最长 4s。
  3. 通过 `logs/.tts_state` 保活：每次发声刷新 2h 活跃期，闲置满 2h 自动 `pkill` 释放内存并清理 `output/` 临时文件。

---

## 4. LLM 缓存优化设计 (2026-05-24)

### 4.1 Prompt Caching 前缀保持

DeepSeek/OpenAI 的自动缓存基于严格前缀单调递增匹配。若在 ReAct 循环中途重写 `agent.messages` 中的历史消息，会导致前缀哈希失效。

* **规范**（已在 `agent/core/react_loop.py` 实现）：
  1. `agent.messages` 中的 User/Assistant/Tool 消息在 ReAct 循环中保持静态，不进行重写。
  2. 动态上下文（时间戳、cwd、memory_block 等）在 `llm_stream`/`llm_chat` 调用时以临时 System 消息追加到 `final_messages` 末尾，调用完成后丢弃，不混入 `agent.messages`。确保历史前缀单调递增，缓存命中率稳定在 95%-99%。

### 4.2 记忆检索的级联混合持久化

* **流程**：Query 标准化 → 内存 LRU 缓存 → 语义余弦相似度匹配 (>0.98) → SQLite 检索缓存表
* **持久化**：SQLite 存储检索结果，缓存跨网关重启保持有效。
* **清退**：`save_memory()` 或 notes 变动时触发 `DELETE FROM retrieval_cache` 清除脏缓存。

### 4.3 网页外链摘要持久化缓存

* **实现**：`notes.db` 中 `link_summaries` 表，URL 的 SHA-256 哈希作为主键，TTL 为 24h。

### 4.4 TTS 本地音频缓存

* **目录**：`agent/resources/voice_cache/`
* **匹配键**：`sha256(clean_text + emotion_style + speed_factor)`
* **效果**：高频重复短句从磁盘直接返回 `.wav`，延迟从 ~3000ms 降至 0ms。

---

## 5. 梦境与疲劳预取自愈机制 (Dreaming & Active Prefetching)

### 5.1 子缓存块文本索引路由

将长期知识库划分为四大静态子缓存块，通过文本正则与 SQLite FTS5 倒排索引（不消耗大模型算力）路由到最相关的子块：

1. `system_architecture_block`（系统架构与配置）
2. `tts_voice_block`（语音合成规则与情绪参数）
3. `persona_history_block`（角色自画像与历史对话）
4. `general_knowledge_block`（通用知识与学习资料兜底）

### 5.2 梦境整理与深夜睡眠 (Dreaming & Deep Sleep)

* **触发**：每日凌晨静默期或系统长期 Idle
* **动作**：
  1. **记忆固化**：对过去 24h 的 Session 历史进行蒸馏，提炼最多 3 条核心结论或纠偏反馈，作为 Knowledge Items (KI) 写入 SQLite。
  2. **上下文重置**：清空当前长 Session 历史，仅保留核心指代名词（如 `{“当前讨论核心”: “API鉴权”}`）作为画像线头在醒来时注入，防止历史指代丢失。

### 5.3 活跃期实时缓存预测预取 (Active Session Prefetching)

已在 `react_loop.py` & `llm.py` 中实现：

* **Token 审计**：处理流式/非流式下 `cached_tokens` 等指标的安全提取，规避 `stream_options` 末尾空帧造成的 `IndexError`。
* **缓存命中率监测**：实时输出 `[TOKEN AUDIT]` 指标。
* **意图预测**：高频交互期，后台协程通过最近 3 次工具调用轨迹预测下一轮可能需要的主题子块，在 SQLite 中以只读模式预加载。
* **疲劳阻尼**：最近 3 次工具调用包含 `bash`、`write_file` 等高负载操作时，判定为紧急任务期，将疲劳触发阈值从 64K 延迟防抖至 100K。

---

## 6. 记忆库沙箱化、多实例隔离与零硬编码路径 (2026-05-24)

### 6.1 零绝对路径硬编码

* **原则**：系统内禁止硬编码 `/Users/xiaofeng`。所有路径通过 `config/settings.yaml` 管理：
  - `memory.base_dir`：记忆数据库目录（如 `~/.my-agent/memory`）
  - `memory.backup_dir`：备份目录（如 `./.memory`）
  - `knowledge_base.notes_paths`：笔记路径列表，支持 `~` 解析
  - `knowledge_base.kb_dir`：长期知识库路径
* **实现**：`MemoryManager.resolve_adaptive_path` 处理以 `./` 开头的相对路径（寻址到项目根目录）和以 `~` 开头的路径（展开为用户主目录）。

### 6.2 异步防抖热双写备份与逆向还原

* **热双写**：写入后触发 1.5s 异步防抖备份协程，优先使用 SQLite `connection.backup()`，异常时 fallback 至 `shutil.copy2`。
* **逆向还原**：新环境部署时若 `backup_dir` 存在且 `base_dir` 为空，启动时自动复制还原记忆数据。

### 6.3 多实例哈希隔离

* **机制**：`MemoryManager` 在主路径和备份目录追加 `admin_id` 哈希子目录（如 `.memory/1705919142/`），实现单机多实例物理隔离，避免 SQLite 并发写锁冲突。

### 6.4 空目录保护

* **机制**：`search_notes` 与 `update_knowledge_index` 中，若配置的目录不存在，静默跳过并打印 DEBUG 日志，不自动创建空目录。

---

## 7. 消息压缩合并机制 (2026-05-24)

### 7.1 队列级消息合并 (Queue Compaction)

* **位置**：`net_gateway/executor.py`
* **机制**：在任务 `finally` 块中通过 `while self.bot.has_queued_messages(session_key)` 循环，将思考期间积压的消息一次性全部出队并在会话窗口内合并。

### 7.2 发言人区分与提示词注入

根据出队消息的发言人身份构造合并提示词：

* **单人连发**：拼接所有消息，注入提示要求一次性综合回复。
* **群聊多人连发**：每行带姓名标注，注入分层提示词（对特定用户亲昵，对他人克制）。

### 7.3 Carrier Sense 穿透与群聊唤醒

* **时间戳刷新**：合并后的 event 在 `TaskDispatcherMiddleware` 中获得最新 `time.monotonic()` 时间戳，穿透 Carrier Sense 防刷屏判定。
* **群聊唤醒前缀**：群聊合并消息在 Prompt 头部添加 `[CQ:at,qq={self_id}]` 前缀，防止被中间件过滤。

---

## 8. 记忆进化引擎 (2026-05-24)

### 8.1 数据库 DDL 升级与快照备份

* **数据模型**：`knowledge_items` 表追加 `version`（INT，默认 1）与 `revision_history`（TEXT JSON）列。
* **快照备份**：`_get_db` 在 DDL 迁移前用 `shutil.copy2` 制作 `memories.db.bak`，以独立事务执行 `ALTER TABLE`。
* **回滚**：迁移异常时释放文件锁，用 `.bak` 覆写还原主库后重新连接。

### 8.2 相似度阻尼带分流

* **双轨判定**：新碎片与已有 KI 余弦相似度 ≥0.90 时直接进入 LLM 合并；在 [0.75, 0.90) 区间内触发 `DAMPING_JUDGE_PROMPT` 终审裁判。节省无关或极相似条目的 LLM 调用。

### 8.3 事实纠偏覆写与版本修订历史

* **覆写**：`DREAM_MERGE_PROMPT` 在冲突时以最新事实覆写。
* **历史追加**：合并后在 `revision_history` 字段录入结构化 JSON，并在 `content` 尾部追加 Markdown 修订行（如 `* v2 (2026-05-24): ...`）。

### 8.4 深夜粗聚类熔炼与碎片清退

* **粗聚类**：拉取 24h 内活跃的 Master KI 或新碎片，基于 keywords 交集在 Python 内存中进行贪心聚类，划分为 [2, 5] 个条目的熔炼桶。
* **熔炼与清退**：每桶调用 `DREAM_FUSE_PROMPT` 熔接为唯一 Master KI，在同一事务中清退桶内旧碎片 KI、embeddings 和 FTS 倒排，防止记忆库膨胀。

### 8.5 RAG 检索版本召回加权

* **加权公式**：`Version Multiplier = 1.0 + 0.05 × ln(version)`
* **效果**：经历多轮纠偏和熔炼的高版本事实获得更高检索召回权重。

---

## 9. 老旧数据库迁移引擎 (2026-05-24)

实现从旧无隔离库（`~/.my-agent/memory/memories.db`）到多实例隔离新库的平滑迁移。

### 9.1 DDL 列结构对齐 (Schema Alignment)

* **问题**：老库缺失 `version` 和 `revision_history` 字段，直接 ATTACH 导入会因列不匹配抛异常。
* **方案**：迁移前用独立 sqlite 连接探查老库列结构，缺失字段时当场执行 DDL 补齐。

### 9.2 跨库 ATTACH 原子合并

1. `ATTACH DATABASE '<old_db_path>' AS old_db`
2. 在同一事务中执行：`INSERT OR IGNORE INTO` 覆盖 `knowledge_items` 和 `ki_embeddings`；FTS 索引做排重增量导入
3. `DETACH DATABASE old_db`

### 9.3 Markdown 碎片分段去重合并

1. 普通碎片（如 `reflect_*.md`）：`shutil.copy2` 安全复制
2. 核心文件（如 `user_profile.md`）：按 `###` 标题分段解析，MD5 去重后追加，避免覆盖导致偏好记忆丢失
3. 索引行合并去重：解析老 `MEMORY.md`，过滤已存在项后 `_upsert_index` 至新索引

### 9.4 迁移归档

迁移成功后，老库文件重命名为 `*.migrated` 归档，下次启动自动跳过迁移。

---

## 10. Prompt Caching 优化与 KI 结构化融合 (2026-05-24)

### 10.1 尾端临时消息消除 (Tail Jitter Elimination)

* **问题**：旧方案将 `TempContext` 作为临时 System 消息追加到消息列表末尾并在调用后丢弃，导致从 ReAct 第 2 步起前缀分叉，后续步骤缓存大面积失效。
* **方案**：将 `TempContext` 动态合并到 `llm_messages[0]`（System 消息）末尾，保持 `agent.messages` 尾端纯净单调累加。后续工具步骤的前缀与第 1 步完全一致，缓存命中率达到 100%。

### 10.2 单轮 ReAct 静态时间戳

* **方案**：在 `run_loop` 初始化时提取一次静态时间戳 `now = datetime.now().strftime(“%Y-%m-%d %H:%M”)`，整轮 ReAct 循环中保持不变，消除取模/取整/时区运算等冗余防抖逻辑。

### 10.3 Master KI 结构化注入

* **方案**：RAG 召回时，对 `ki_` 开头的长期记忆自动查询 `knowledge_items` 表获取完整属性，格式化为标准属性卡片（ID、Version、Title、Keywords、Summary、Content、Latest Revision）。
* **修订历史过滤**：仅在 System Prompt 中展示最近一条修订历史，完整历史保留在数据库中以节省 Token。

---

## 11. 短期记忆持久化与梦境自进化系统 (2026-05-24)

解决网关重启/断线导致短期对话丢失的问题。

### 11.1 active_sessions 持久化表

`bootstrap` 初始化数据库时自动建表：

```sql
CREATE TABLE IF NOT EXISTS active_sessions (
    session_key TEXT PRIMARY KEY,
    messages TEXT NOT NULL,       -- JSON 序列化消息数组
    updated_at TEXT NOT NULL      -- UTC ISO-8601 时间戳
);
```

### 11.2 异步防抖刷盘与冷启动恢复

* **防抖刷盘 (`save_active_session_async`)**：维护 `_debounce_tasks` 字典，1.0s 防抖延迟后将内存 `messages` 快照原子写入 SQLite。
* **冷启动恢复 (`load_active_session`)**：`Agent.run()` 启动时优先从 `active_sessions` 表加载未清账消息；表为空时 fallback 到 `session.json`。
* **Hook 位置**：`react_loop.py` 的 `run_loop` 初始化、每轮 turn 末尾、以及 `completed`/`aborted` 退出分支。

### 11.3 并发快照增量清账切片算法

* **问题**：做梦提炼耗时 10-30s，期间用户可能发来新消息。直接清空 `messages` 会丢失这些新消息。
* **算法**：
  1. 做梦前截取消息快照：`snapshot = list(agent.messages)`，`snapshot_len = len(snapshot)`
  2. 仅基于快照进行进化提炼，阻断并发流入
  3. 做梦完成后执行增量清账切片：
     ```python
     if len(agent.messages) >= snapshot_len:
         agent.messages = agent.messages[snapshot_len:]
     else:
         agent.messages = []
     ```
  4. 更新 SQLite `active_sessions` 表

### 11.4 梦境回顾卡片与 8s Fallback

* **回顾卡片**：醒来时通过 `DREAM_EVOLUTION_SUMMARY_PROMPT` 生成包含自省反思、新策略、技能更新等板块的回顾卡片。
* **8s Fallback**：对回顾总结的 LLM 调用设置最长 8.0s 超时，超时则通过本地模板组装精简版回顾卡片。

### 11.5 测试验证

DDL 表生成、防抖写盘、冷启动恢复、并发快照清账、本地 fallback 容灾等测试均在 `tests/test_fatigue_dream_persistence.py` 中验证通过。

---

## 12. 抖音独立网关微服务 (2026-05-26)

抖音网关作为独立进程运行 (`main.py --douyin`)，与 QQ 大脑 (`main.py --gateway`) 通过 HTTP 通信，完全解耦。

### 12.1 子模块构成

| 文件                     | 职责                                          |
| ------------------------ | --------------------------------------------- |
| `douyin_browser.py`    | CDP 管理、浏览器拉起、视觉引擎                |
| `douyin_dom_poller.py` | 登录校验、私信面板检测、消息轮询与气泡提取    |
| `douyin_dom_sender.py` | JS 文字写入、点击发送、发送结果验证           |
| `douyin_bot.py`        | 进程主控、9000 端口 HTTP API、轮询调度与视觉 API |

### 12.2 通信协议

```
Douyin 进程 (:9000)          QQ 大脑进程 (:8000)
     │                              │
     ├── POST :8000/event ─────────→│  上行: 消息上报
     ├── POST :8000/report_qrcode ─→│  上行: 扫码图片
     │                              │
     │←─ POST :9000/send_private_msg│  下行: 回复指令
     │←─ POST :9000/vision/* ───────│  下行: 视觉接管 RPC
```

### 12.3 设计原则

- **零硬编码 CSS class**：DOM 选择器基于语义文本和几何约束，不依赖 React hash class
- **零硬编码用户名**：无特殊账号豁免逻辑
- **一次 poll 最多两次 JS evaluate**：Phase 1 扫描联系人+点击，Phase 2 提取气泡
- **JS 文字写入代替 keyboard.type**：通过 `InputEvent` 触发 React 绑定
- **不再嵌入 QQ 进程**：`ENABLE_DOUYIN_IN_QQ` 和 `only_douyin` 死分支已移除

### 12.4 启动方式

```bash
make douyin-restart   # 启动/重启抖音网关
make gateway-restart  # 启动/重启 QQ 大脑
```

---

## 13. 桌面视觉与 Web 视觉双轨架构 (VisualAgent 双模 - 2026-05-26)

系统采用 Web + OS 分离的双轨视觉引擎，通过 Template Method 提取公共基类。

### 架构拆分

| 类名 / 文件 | 职责 | 实现方式 |
| --- | --- | --- |
| `BaseVisualAgent` (`core/visual_agent.py`) | 认知骨架：决策循环、截图哈希容错、记忆存取 | 基类 |
| `VisualAgent` (`core/visual_agent.py`) | 网页引擎 | 继承基类，Playwright CDP 控制 Chrome |
| `DesktopVisualAgent` (`core/desktop_agent.py`) | 桌面引擎 | 继承基类，`screencapture -C` + `pyautogui` 控制 macOS |

### 工作流程

1. **网页任务** → `browser_agent` 工具 → `VisualAgent` → CDP 连接网页
2. **桌面/微信应用** → `desktop_agent` 工具 → `DesktopVisualAgent` → 全屏截图 + 鼠标操作

两套工具共享相同的思考循环代码（继承自 `BaseVisualAgent`）。

### 系统依赖

使用 `desktop_agent` 需要 macOS 授予终端/Python 「屏幕录制」与「辅助功能（控制电脑）」权限。

---

## 14. Token 消耗与缓存命中率监控

所有 LLM 调用（LiteLLM / OpenAI / DeepSeek）均在 `agent/core/react_loop.py` 中输出 `[TOKEN AUDIT]` 标识的监控指标，路由至 `chat.log`。

Token 获取自愈（流式 Usage 跨帧捕获、保守上限法智能估算）机制详见 `agent/core/llm.py` 和 `agent/net_gateway/executor.py`，测试覆盖在 `tests/test_token_metrics.py`。

### 14.2 双通道日志架构 (2026-05-27)

系统采用**双文件 + logger name 前缀路由**，不再依赖消息内容字符串匹配：

| 文件 | 路由规则 | 内容 |
|------|----------|------|
| `chat.log` | `agent.activity.*`, `agent.react_loop`, `net_gateway.logger` | 用户输入、小萤回复、工具调用、Token审计、缓存命中 |
| `system.log` | 其余全部 | 网关连接/断线、错误异常、记忆自愈、做梦GC |

### 14.3 日志速查指南

| 排查诉求 | 目标文件 | 查看命令 |
| :--- | :--- | :--- |
| 对话+工具+Token 全貌 | `logs/chat.log` | `tail -f logs/chat.log` |
| 网关/错误/自愈 | `logs/system.log` | `tail -f logs/system.log` |
| 特定会话链路 | 以上任意 | `grep “user_xxx” logs/chat.log` |
| Token 消耗/缓存命中 | `logs/chat.log` | `grep “TOKEN AUDIT” logs/chat.log` |

---

## 15. 核心模块解耦拆分规范 (2026-05-25)

为降低核心文件的行数与认知复杂度，于 2026-05-25 执行了核心模块解耦。

### 15.1 拆分详单

遵循最小修改与适度解耦原则，仅将各主文件中职责偏离度最高的部分平移：

1. **`agent/evolution/dream.py`** (632 → ~450 行)：
   * 将 Prompt 静态字符串（约 174 行）剥离至 `agent/evolution/dream_prompts.py`
   * 兼容：`dream.py` 头部通过 `from .dream_prompts import (...)` 原样导入

2. **`agent/memory/manager.py`** (513 → ~320 行)：
   * 将 SQLite 跨库 ATTACH 迁移引擎（约 200 行）剥离至 `agent/memory/migration.py`
   * 兼容：`MemoryManager` 类内部保留 Proxy 代理接口：
     ```python
     def _run_hot_migration_if_needed(self, old_base_dir_override=None):
         return _run_hot_migration_if_needed(self, old_base_dir_override)
     ```

3. **`agent/memory/index.py`** (501 → ~230 行)：
   * 将 `MemoryCache` LRU 缓存剥离至 `agent/memory/cache.py`
   * 将 SentenceTransformer 向量提取剥离至 `agent/memory/embedding.py`
   * 兼容：`index.py` 头部通过代理导入：
     ```python
     from .cache import MemoryCache
     from .embedding import _get_embedding, save_ki_embedding
     ```

### 15.2 后续拆分红线

后续解耦必须遵守：

* **约束 1**：维持原 API 签名。被平移的类或方法必须在原文件中通过代理导入暴露原命名空间，确保外部调用方零修改。
* **约束 2**：回归测试必须全部通过：
  ```bash
  PYTHONPATH=. venv/bin/pytest tests/
  ```

---

## 16. 异常自愈与工具调用修复机制 (2026-05-25)

### 16.1 JSON 截断自动修复 (JSON Repair)

* **位置**：`agent/core/history_repair.py` → `repair_truncated_json(input_str)`
* **原理**：单字符嵌套栈状态机，1 轮扫描内判定引号及括号嵌套深度，剔除尾部多余逗号，空键补 `null`，逆序补全括号，恢复截断的 arguments 为合法 JSON。
* **安全分流**：
  * 安全只读工具（`read_file`、`web_search`）：允许自愈
  * 高危写入工具（`write_file`、`edit_file`、`bash`）：JSON 截断时直接熔断拦截，禁止自动修复，raise 语法异常让 LLM 通过 ReAct 重试

### 16.2 思考流/正文工具调用回收 (Tool-call Scavenger)

* **位置**：`agent/core/history_repair.py` → `scavenge_tool_calls(text, allowed_names)`
* **原理**：扫描 LLM 推理内容（`reasoning_content`）或正文中泄露的 JSON 调用，支持 `name/arguments`、OpenAI standard、`tool_name/tool_args` 等变体以及 `<DSML|invoke>` 标签。
* **安全约束**：
  * 仅提取被 Markdown 代码块或显式标签包裹的 JSON，隔离普通闲聊中的举例噪声
  * 多代码块时仅提取最后一个（Last-Write-Wins），避免旧演示被误执行

### 16.3 回归测试约束

对 LLM 底层接口改动后必须运行：

```bash
PYTHONPATH=. venv/bin/pytest tests/test_token_metrics.py
```

---

## 17. 三级认知挂载与动态技能召回架构 (2026-05-27)

为解决技能扩展带来的 Prompt 上下文膨胀问题，系统构建了三级认知挂载架构，将 Skills 与 Experiences 解耦，避免全量加载造成的 Token 增长与注意力稀释。

### 17.1 三级架构设计

#### 第一级：核心技能区 (Core Skills - 启发式按需召回)
- **路径**：`skills/*.md` 及 `skills/*/SKILL.md`
- **定位**：基础操作能力（系统底层操作、工具链使用、自学习规则）
- **动态算分机制**：
  - 使用 `_calculate_skill_score()` 精确算分取代 Boolean 模糊子串匹配。每个 trigger 分词命中得分 = 关键字字符长度（长词特异度更高）。trigger 整体在 Query 中以子串完全匹配时获得 `10.0` 加成。
  - 匹配得分 `[2.0, 5.0)` 为中度相关，无高度相关时最多补齐 Top-2
  - 匹配得分 ≥5.0 为高度相关，突破 Top-2 限制允许全部挂载（受 2500 字符截断保护兜底）
- **并发互斥锁 (`rules_lock`)**：`threading.Lock` 保护 `自学习技能/规则与偏好.md` 的写盘（深夜进化）与读盘（日常召回）操作
- **YAML 剥离与缓存保护**：`_strip_yaml_frontmatter()` 非贪婪匹配仅剥离第一个 YAML 块（`count=1`），保留纯净 Markdown 注入 Prompt。即使磁盘上的 usage/更新时间不断变化，注入 Prompt 的内容保持静态，确保缓存命中率 >99%
- **截断保护**：单次召回 Skills 内容总长度受 2500 字符截断保护，超限时记录 Warning 日志并安全截断

#### 第二级：动态经验区 (Dynamic Experiences)
- **路径**：`experience/*.md`（已从 `agent/experience/*.md` 扁平化迁移）
- **定位**：特定业务场景的实战经验与操作手册
- **加载机制**：
  - BM25 词频匹配，Query 长度 ≥2 字符时动态召回 Top-2 经验
  - 以 `[DYNAMIC EXPERIENCE BLOCK]` 下发，场景切换后自动卸载
- **遗忘拦截看门狗**：`react_loop.py` 中埋设。若触发动态经验块且 LLM 执行了工具操作但未调用 `record_skill_usage` 打卡，看门狗下发催促 System Prompt

#### 第三级：长期知识库 (Knowledge Items / SQLite)
- **路径**：`memories.db` → `knowledge_items`
- **定位**：技术结论、纠偏反馈、架构演进史
- **加载**：基于语义向量 (Embeddings) 与 FTS5，通过 `search_memories` 等召回

---

### 17.2 Experiences → Skills 演化晋升与 GC 蒸馏

1. **演化晋升机制**：
   - 经验在 `agent_memory/experiences/` 中打卡累积引用达到晋升阈值时，自动将 `.md` 文件剪切至 `agent_memory/skills/` 目录，并将数据库中 `is_skills` 置为 1。

2. **深夜 GC 蒸馏合并**：
   - 临时碎片存放于 `agent_memory/core/` 子目录
   - 深夜 dreaming 或 `MemoryManager` 异步初始化时，自动启动 `gc_and_merge_fragmented_memories` 进行多文件去重与蒸馏收敛
   - Git 排除：`/agent_memory/core/` 目录整体被 `.gitignore` 忽略；`agent_memory/skills/` 与 `agent_memory/experiences/` 保持 Git 跟踪

3. **备份镜像同步**：`trigger_backup()` 时自动扫描并删除备份目录中遗留的孤立文件，保持备份与主目录一致。

---

### 17.3 自学习与进化规则统一 (Rules Unification)

已清理各处残存的 `EVOLVED_RULES.md` 文件，统一收归：
1. 所有自学习和进化偏好落盘到 `agent_memory/skills/自学习技能/SKILL.md`
2. 进化评估沙箱（`agent/evolution/tester.py`）自动读取并剔除 YAML 后的规则
3. `agent/evolution/rules.py` 读写指针已重写指向本标准位置

---

### 17.4 经验/技能文档撰写规范

#### 目录与资产架构
- **经验文档 (`agent_memory/experiences/`)**：必须保持扁平，禁止创建子文件夹
- **技能文档 (`agent_memory/skills/`)**：
  - 单文件技能：直接在根目录创建 `[skill_name].md`
  - 子目录模式：单层文件夹，主文档必须命名为 `SKILL.md`，可通过 `templates/`、`scripts/`、`references/` 存放支撑资产
  - 禁止深度超过 1 层的嵌套目录

#### YAML Frontmatter 表头
所有 Skills 和 Experiences 文档头部必须包含：
- `name`: 唯一标识，全小写，下划线分割
- `trigger`: 匹配触发关键字，用 `/` 或 `,` 切分
- `description`: 功能描述

**标准模板：**

```markdown
---
name: my_wechat_skill
trigger: 微信/mimo/小程序/发图
description: 微信小程序自动化操作手册
---

# 核心目标
[一句话说明用途]

# 约束 (Critical Rules)
- [约束 1：必须先验证 UI 元素是否存在]
- [约束 2：禁止的操作]

# 操作流 (Action Flow)
1. **第一步 [意图]**：[使用的工具及参数]
2. **第二步 [意图]**：[具体动作]

# 排错与自愈 (Troubleshooting)
- **如果遇到 [现象 A]**：执行 [动作 B]
- **如果看到 [报错 X]**：原因 [Y]，补救 [Z]
```

#### 字符限制
核心技能 (`agent_memory/skills/`)：总长度不超过 2500 字符。

#### 打卡要求
当上下文出现 `[DYNAMIC EXPERIENCE BLOCK]` 时，任务完成后必须调用 `record_skill_usage` 工具，传入经验的 `skill_name` 及成功/失败状态。

---

## 18. 全局路径常量与阈值配置化 (2026-05-27)

### 18.1 统一路径常量 (`agent/core/paths.py`)

消除各处重复的 `Path(__file__).resolve().parents[2]` 路径解析，所有 `agent_memory/` 子目录路径统一从此模块导入：

```python
from agent.core.paths import PROJECT_ROOT, SKILLS_DIR, EXPERIENCES_DIR, CONTEXT_DIR, SELF_EVOLUTION_DIR
```

后续新增子目录或移动文件时，只需修改 `paths.py` 一处即可。

### 18.2 阈值配置化 (`config/settings.yaml` → `thresholds`)

所有运行时阈值集中管理在 `settings.yaml` 的 `thresholds:` 节点下，代码通过 `settings.get_threshold(key, default)` 读取，配置缺失时回退到默认值保证不崩溃。

| 键 | 默认值 | 用途 |
|---|---|---|
| `skill_relevance_low` | 2.5 | 技能召回最低相关度分数 |
| `skill_relevance_high` | 5.0 | 高相关度技能分类边界 |
| `skill_prompt_max_chars` | 2500 | 技能 Prompt 截断字符数 |
| `skill_promotion_usage` | 5 | 经验晋升技能所需最低使用次数 |
| `skill_promotion_success_rate` | 0.90 | 经验晋升技能所需最低成功率 |
| `experience_recall_top_k` | 2 | 动态经验召回数量 |
| `experience_prompt_max_chars` | 2000 | 经验 Prompt 截断字符数 |
| `tts_idle_timeout_seconds` | 7200 | TTS 闲置超时强杀秒数 |
| `tts_health_timeout` | 3.0 | TTS 健康探测超时秒数 |
| `tts_health_fail_threshold` | 3 | TTS 自愈前连续失败次数 |

