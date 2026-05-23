# Spec: XL Agent 全项目文件收纳与模块化解耦规范

本规范旨在遵循企业级微服务与现代开源项目（Best Practices）的标准开发规范，在**不破坏工业级工具链（Docker, Pytest, Makefile, IDE）默认寻址机制**的前提下，对项目根目录及 `agent/` 目录中散落的各种零碎文件进行极致规范的归纳与整理，建立高内聚、低耦合的清晰目录架构。

---

## 1. 核心设计原则 (Design Principles)

### 📌 物理资产绝对收拢原则
* 非代码配置文件、静态角色资产、运行时生成的各日志文件一律严禁停留在代码开发包中或散落在根级。
* 分门别类收入专门设立的 `logs/`（日志）、`agent/resources/`（静态资源）、`config/`（配置）等子包下。

### 📌 地标元文件坚守根级原则
* 遵循企业规范，保留项目的元配置与启动地标在根目录下：`.env.example`, `.gitignore`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `README.md`, `requirements.txt`, `pytest.ini` 以及唯一运行入口 `main.py`。
* 确保所有 Docker 自动化构建、Pytest 测试用例自动搜集、Makefile 快捷命令及 IDE 白盒集成开箱即用，免配置零负担。

### 📌 源码级高内聚解耦原则
* 属于 Agent 底层组装、系统配置、清理自愈、外观代理的 Python 代码（`bootstrap.py`, `config.py`, `cleanup.py`, `gateway.py`）必须从包根级移入 `agent/core/` 核心子包中，彻底实现 `agent/` 外观的清爽与统一高层接口化。

---

## 2. 重构前后物理文件对照蓝图 (Architecture Blueprint)

### 重构前：散乱游离的结构
```text
Xl-General-AI-Agent/ (根目录)
├── agent/
│   ├── .stitch_env             <-- 散落的临时环境变量
│   ├── default_persona.json     <-- 散落的角色设定
│   ├── bootstrap.py            <-- 散落的核心代码
│   ├── config.py               <-- 散落的核心代码
│   ├── cleanup.py              <-- 散落的核心代码
│   ├── gateway.py              <-- 散落的核心代码
│   └── core/, runner/, ...
├── tests/
│   └── debug_run.py            <-- 散落的辅助调试脚本
├── agent_activity.log          <-- 散落在根级的日志
├── coworker_activity.log       <-- 散落在根级的日志
├── gateway.log                 <-- 散落在根级的日志
├── gateway.err                 <-- 散落在根级的日志
├── startup.log                 <-- 散落在根级的日志
├── main.py
└── [各类元配置文件]
```

### 重构后：标准企业开发规范结构 (100% 整洁美观规范)
```text
Xl-General-AI-Agent/ (根目录)
├── agent/                      <-- 干净的 Python 源码包
│   ├── core/
│   │   ├── bootstrap.py        <-- 归位核心引导
│   │   ├── config.py           <-- 归位核心配置加载
│   │   ├── cleanup.py          <-- 归位核心清理自愈
│   │   ├── gateway.py          <-- 归位网关代理 Facade
│   │   └── [原有核心组件]
│   ├── resources/
│   │   ├── default_persona.json <-- 归位静态画像资产
│   │   └── gallery.html, sagiri_emotions/
│   └── [其他规范包: memory/, tools/, net_gateway/, ...]
├── config/
│   ├── settings.yaml
│   └── .stitch_env             <-- 归位 Stitch 局部配置
├── logs/                       <-- 统一的运行时日志与错误流收纳所 (NEW)
│   ├── agent_activity.log
│   ├── coworker_activity.log
│   ├── gateway.log
│   ├── gateway.err
│   └── startup.log
├── scripts/                    <-- 统一的开发调试小工具收纳包
│   ├── debug_run.py            <-- 归位调试测试脚本
│   ├── debug_stitch.py
│   └── deepseek_key_scanner.py
├── tests/                      <-- 纯净标准的自动化测试用例包 (100% pytest-friendly)
│   └── test_*.py
├── main.py                     <-- 项目全局唯一的标准启动入口
├── Dockerfile                  <-- 企业容器规范构建地标
├── docker-compose.yml          <-- 服务编排元配置
├── Makefile                    <-- 自动化运维快捷地标
├── README.md                   <-- 项目全局自述地标
├── requirements.txt            <-- 项目物理依赖地标
├── pytest.ini                  <-- 测试框架标准初始化地标
└── .gitignore & .env.example
```

---

## 3. 全局重构迁移清单 (Physical Migration Checklist)

### 3.1 非代码资产与日志迁移
1. **角色画像**：
   * 原位置：`agent/default_persona.json`
   * 新位置：`agent/resources/default_persona.json`
2. **Stitch 配置**：
   * 原位置：`agent/.stitch_env`
   * 新位置：`config/.stitch_env`
3. **辅助调试脚本**：
   * 原位置：`tests/debug_run.py`
   * 新位置：`scripts/debug_run.py`
4. **物理日志重定向**：
   * 创建全局 `logs/` 文件夹。
   * 修改启动脚本 `bin/start.sh` 和配置，将 `gateway.log`、`gateway.err` 和 `startup.log` 的输出路径全部规范化重定向写入至 `logs/`。
   * 修改系统日志器（Python Logger）配置，使运行时产生的 `agent_activity.log` 和 `coworker_activity.log` 也自动存储至 `logs/` 目录下。

### 3.2 源码底层重构物理搬迁
1. **引导依赖注入器**：
   * 原位置：`agent/bootstrap.py`
   * 新位置：`agent/core/bootstrap.py`
2. **自适应配置加载**：
   * 原位置：`agent/config.py`
   * 新位置：`agent/core/config.py`
3. **环境自愈清理**：
   * 原位置：`agent/cleanup.py`
   * 新位置：`agent/core/cleanup.py`
4. **QQ Gateway Facade 代理**：
   * 原位置：`agent/gateway.py`
   * 新位置：`agent/core/gateway.py`

---

## 4. 全局导入重定向规范 (Global Import Alignment)

为保证全项目重构后代码的编译与运行 100% 畅通无阻，必须对以下全局导入点进行同步重定向修改：

| 引用文件 | 原导入语句 | 重构后新导入语句 | 说明 |
| :--- | :--- | :--- | :--- |
| `agent/core/agent.py` | `from ..config import settings` | `from .config import settings` | 核心 config 已在同级目录 |
| `agent/core/agent.py` | `Path(__file__).parent / "default_persona.json"` | `Path(__file__).resolve().parents[1] / "resources" / "default_persona.json"` | **完美修复人设寻址路径 Bug** |
| `agent/core/bootstrap.py` | `from .config import settings` | `from .config import settings` | bootstrap 已在同级目录 |
| `agent/tools/mcp/notebooklm_client.py` | `from agent.config import settings` | `from agent.core.config import settings` | 更新全局 config 路径 |
| `main.py` | `from agent.bootstrap import ...` | `from agent.core.bootstrap import ...` | 主入口引导类寻址变更 |
| `main.py` | `from agent.cleanup import ...` | `from agent.core.cleanup import ...` | 主入口清理类寻址变更 |
| `agent/runner/auto_learn.py` (等) | `from ..bootstrap import build_agent` | `from ..core.bootstrap import build_agent` | Runner 层构建引导寻址变更 |
| `agent/runner/gateway.py` | `from ..gateway import QQGateway` | `from ..core.gateway import QQGateway` | Runner 网关启动寻址变更 |
| `tests/test_*.py` (全测试用例) | `from agent.bootstrap import ...` | `from agent.core.bootstrap import ...` | 测试套件构建引导寻址变更 |
| `tests/test_*.py` (全测试用例) | `from agent.gateway import QQGateway` | `from agent.core.gateway import QQGateway` | 测试套件网关 Facade 寻址变更 |

---

## 5. 校验与双向阻断验证计划 (Verification & Anti-Regression Plan)

1. **第一阶段：文件精确搬迁与 Git 跟踪**：
   * 使用 `git mv`（或在环境中直接物理移动并添加）完成搬迁，保持 Git 历史提交记录可追溯。
2. **第二阶段：代码导入声明全局替换与审查**：
   * 精确修改导入路径。特别注意保障 Python 导入语法中相对导入与绝对导入的精确控制。
3. **第三阶段：TDD 单元测试终极验证**：
   * 在命令行中强制以正确路径规范运行：
     ```bash
     PYTHONPATH=. venv/bin/pytest tests/
     ```
   * 确保除涉及外部真实网络依赖的 E2E 用例外，其余核心系统单元测试全部 **100% 绿屏跑通**，不引入 any 功能性回归问题。
4. **第四阶段：生产守护进程加载验证**：
   * 运行 `./bin/start.sh`，通过 `logs/startup.log` 和 `logs/gateway.log` 验证重构后系统守护进程自愈拉起是否完美，保证 0-Downtime 平滑上线。
