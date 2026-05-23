# 🤖 XL Agent 全局文件收纳、物理架构与 AI 开发者交接说明书

> [!CAUTION]
> **🚨 核心准则：若有变更，必须强制同步更新本交接文档！**
> 本交接说明书为 XL Agent 的全局地标性设计与运行规范，后续接班的 AI 编码助手或人类开发者如果在后续的升级迭代中，**对系统进行了任何物理目录迁移、核心高可用机制微调或导入路径重构，必须强制同步更新本交接文档！** 确保项目知识库的实时准确性，坚决消灭一切未来的冷启动延迟！

---

## 1. 📂 全新企业级项目物理架构地图 (Project Blueprint)

项目物理结构已经根据企业级微服务开发规范，进行了一次性完美的全局规范收纳与彻底解耦。所有散落资产已全部归属各包：

```text
Xl-General-AI-Agent/ (项目根目录)
├── agent/                      <-- 干净高内聚的 Python 源码包
│   ├── core/                   <-- 系统底层的核心逻辑中枢 (已完成 100% 物理重构)
│   │   ├── bootstrap.py        <-- 物理迁移: 负责系统组装、依赖注入与工具自动注册 (已改绝对导入)
│   │   ├── config.py           <-- 物理迁移: 自适应从 config/settings.yaml 中加载配置
│   │   ├── cleanup.py          <-- 物理迁移: 负责守护进程级别的自愈、内存与旧进程清理
│   │   ├── gateway.py          <-- 物理迁移: QQ Gateway 的物理桥接外观层 (Facade 代理，已改绝对导入)
│   │   ├── agent.py            <-- 核心智能体状态机类 (已修复 default_persona.json 路径 Bug)
│   │   ├── react_loop.py       <-- ReAct 思考循环核心 (已锁定时间戳、Prompt Caching 与死锁熔断)
│   │   └── llm.py, compressor.py, task_queue.py
│   ├── resources/              <-- 静态资源与角色数据资产存储包
│   │   └── default_persona.json <-- 物理迁移: 默认的人设设定数据 (修复了原本在 agent.py 同级找不到的 Bug)
│   ├── net_gateway/            <-- QQBot 网关协议底层 (高可用自愈及主流量/沙箱流量物理隔离)
│   │   └── logger.py           <-- 活动日志追踪器 (已更新 agent_activity.log 重定向至 logs/)
│   └── [其他规范子包: memory/, tools/, ui/, evolution/]
├── config/
│   ├── settings.yaml           <-- 中央配置总枢纽 (包含大模型 key、NapCat ws/http、Stitch 等)
│   └── .stitch_env             <-- 物理迁移: Stitch MCP 的局部 Shell 环境配置
├── logs/                       <-- 统一的运行时日志与错误流存放所 (NEW - 让根目录干净 50%+)
│   ├── agent_activity.log      <-- 运行时白名单高纯净交互日志
│   ├── coworker_activity.log   <-- 运行时三方普通旁路日志
│   ├── gateway.log             <-- 网关输出日志 (重定向)
│   ├── gateway.err             <-- 网关标准错误输出日志 (重定向)
│   └── startup.log             <-- 启动中枢日志 (重定向)
├── scripts/                    <-- 统一的开发调试小工具包
│   ├── debug_run.py            <-- 物理迁移: 移出 tests 目录的临时开发调试测试脚本
│   ├── debug_stitch.py
│   └── deepseek_key_scanner.py
├── tests/                      <-- 纯正、标准的自动化单元测试包 (100% pytest-friendly)
│   ├── test_deadlock_fuse.py   <-- 死锁熔断器测试用例 (6.5s 快速绿屏)
│   └── test_*.py               <-- 34 项系统核心单元测试
├── main.py                     <-- 项目全局唯一的标准启动入口 (已更新核心导入路径)
├── Dockerfile & docker-compose.yml <-- 企业级构建及编排规范地标 (坚守根级)
├── Makefile                    <-- 自动化运维快捷地标 (坚守根级)
├── pytest.ini                  <-- Pytest 初始化配置文件 (坚守根级，支持 IDE 瞬间识别)
└── .gitignore & .env.example
```

---

## 2. 🚨 核心开发导入红线与避坑警示 (Critical Warnings)

接班的 AI，在你未来的开发和物理变更中，请务必严守以下三项“物理红线”：

### ⚠️ 红线 1：`agent/core/gateway.py` 内部引入 `net_gateway` 必须使用绝对导入
因为 `gateway.py` 被物理移动到了 `agent/core/` 子包下，而底层的网络协议包在 `agent/net_gateway/`，如果使用相对导入 `from .net_gateway.bot ...`，Python 会在 `agent/core/net_gateway` 下寻址从而触发 `ModuleNotFoundError`。
* **避坑标准**：**必须**使用绝对导入声明：
  ```python
  from agent.net_gateway.bot import QQGateway, main
  ```

### ⚠️ 红线 2：人设画像模板的物理加载路径必须锁定为 `parents[1]`
`agent/core/agent.py` 原本使用 `Path(__file__).parent / "default_persona.json"`，但在本轮整理中，默认人设资产已物理迁移到了静态资源文件夹 `agent/resources/` 下。
* **避坑标准**：在 `agent.py` 初始化画像缓存时，**必须**精确定位到 `parents[1]` 级目录再寻址：
  ```python
  template_file = Path(__file__).resolve().parents[1] / "resources" / "default_persona.json"
  ```
  *(注：该修复彻底解决了之前由于寻址报错导致一直使用兜底 Hardcode 数据的历史路径 Bug)*

### ⚠️ 红线 3：保持地标元配置文件在项目根目录
为了遵循企业级开发规范，诸如 `pytest.ini`、`Makefile`、`Dockerfile`、`requirements.txt` 等元文件**必须保留在根目录下**。绝对不要尝试将它们移入子包中。否则，主流 IDE（如 VSCode、PyCharm）和 pytest 框架本身将彻底丧失在根目录下直接一键拉起自动化 Pytest 测试套件的能力。

---

## 3. 🎯 已经植入并验证通过的黑科技与高可用机制

当前的代码库中，已经完成并验证了以下几大核心高可用架构 of 闭环建设。你在修改这些核心代码时，请保持其结构：

### 🛡️ ReAct 思考循环死锁熔断器 (Deadlock Fuse)
* **位置**：`agent/core/react_loop.py` ➔ `run_loop`。
* **机制**：如果同一个工具（如 `read_file`）在同一个 ReAct 思考窗口里被连续以**一模一样的参数重复调用 $\ge 4$ 次**，系统判定 LLM 陷入自我死循环或思考阻断，**死锁熔断器将立刻拉起熔断安全电闸**，阻止 ReAct 循环，并向大模型反馈警告信息从而引导其自我调整。
* **测试用例**：在 `tests/test_deadlock_fuse.py` 中有高密度的白盒模拟覆盖。

### ⚡ 双重环境变量自愈与异构容灾鉴权
* **位置**：`agent/core/llm.py`。
* **机制**：
  1. `_sync_environ_keys` 助手函数：在 LiteLLM 每次触发 `acompletion` 前，自动将大模型客户端的 `api_key` 和 `api_base` 物理注入到 `os.environ` 的 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 等全局环境变量中，彻底根治了 LiteLLM 物理双向拨测灾备切换时的 API 鉴权丢失。
  2. 容灾密钥自动借用：在灾备路由分支中，如果主鉴权 `api_key` 暂时为空，系统会自动继承全局 `deepseek_api_key` 及其 Base 端点，避免 LiteLLM 崩溃。
  3. `total_tokens` 容错：在 `Agent.__init__` 中显式初始化并赋初值 `self._total_tokens = 0`，确保孤立单元测试直调 ReAct loop 时不发生 `AttributeError`。

---

## 4. 🛠️ 运维与服务自愈重启指南

当你想重启网关或者验证自愈流程时：
1. **统一自愈启动中枢**：
   运行命令：
   ```bash
   ./bin/start.sh
   ```
   它会自动清理 python pycache、强杀残留的 stale 进程、通过 `launchd` 重启 com.myagent.qqgateway 守护进程、检测并自愈 GPT-SoVITS 离线语音服务，实现零双进程冲突的平滑过渡。
2. **日志追踪位置**：
   重构后，所有的启动、运维与活动日志已规范落盘至 `logs/` 下：
   * 追踪自愈流程：`tail -f logs/startup.log`
   * 追踪网关报错：`tail -f logs/gateway.err`
   * 追踪网关输出与 WebSocket 握手：`tail -f logs/gateway.log`
   * 追踪高纯度 RAG 和大模型会话：`tail -f logs/agent_activity.log`
