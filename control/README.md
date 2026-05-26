# 🛠️ MyAgent 进程手动控制与启停指南

为了维持项目根目录的清爽，本文件夹（`control/`）收拢了全套系统的所有进程控制、手动启动、安全停止和一键总控的脚本。所有服务**完全排除了开机自启动**，提供纯手动的掌控权。

---

## 🧭 服务架构速览

下表汇总了 MyAgent 三大核心服务在运行时的端口、日志文件路径和进程标识，方便日常监控和调试：

| 服务组件 | 本地监听端口 | 后台日志文件路径 (从根目录算起) | 进程过滤特征 |
| :--- | :---: | :--- | :--- |
| **QQ大脑网关** | `8000` | `logs/gateway.log` | `main.py --gateway` |
| **抖音私信网关** | `9000` | `logs/douyin_gateway.log` | `main.py --douyin` |
| **网页自主学习** | - | `logs/auto_learn.log` | `main.py --auto-learn` |
| **NapCat (QQ)** | `3001` (WS) / `3000` (HTTP) | Docker container log | Docker 容器 `napcat` |

---

## 📜 启停控制脚本清单

所有控制脚本均支持从任意目录下调用，它们会自动向外一级定位并接管父级项目根目录。

> [!NOTE]
> 运行前，请先确保赋予了可执行权限：
> ```bash
> chmod +x control/*.sh
> ```

### 1. 抖音私信网关 (Douyin)
- **手动启动**：
  ```bash
  ./control/start_douyin.sh
  ```
  *职责：后台拉起独立抖音网关，自动清理可能残留的历史旧进程并输出日志。*
- **手动停止**：
  ```bash
  ./control/stop_douyin.sh
  ```
  *职责：安全、彻底地强杀抖音网关进程并优雅关闭 CDP 浏览器调试上下文。*

### 2. QQ大脑网关 (QQ Brain)
- **手动启动**：
  ```bash
  ./control/start_qq_gateway.sh
  ```
  *职责：优先加载 macOS launchd 守护进程托管；若未配置托管则以降级 `nohup` 模式在后台拉起。*
- **手动停止**：
  ```bash
  ./control/stop_qq_gateway.sh
  ```
  *职责：安全卸载 launchd 托管，并彻底强杀一切残留的 QQ 网关进程。*

### 3. 自主学习服务 (Auto Learn)
- **手动启动**：
  ```bash
  ./control/start_auto_learn.sh
  ```
  *职责：优先通过 launchd 定时计划拉起服务，或降级 `nohup` 在后台拉起。*
- **手动停止**：
  ```bash
  ./control/stop_auto_learn.sh
  ```
  *职责：卸载 launchd 定时托管并强杀自主学习子进程。*

---

## ⚡ 一键全局总控 (极速推荐)

在日常启动和彻底清理系统资源时，建议使用以下两个总控脚本：

> [!TIP]
> ### 🚀 一键启动核心系统
> ```bash
> ./control/start_all.sh
> ```
> * **执行流程**：自愈检测本地 Docker 环境 -> 唤醒 NapCat 容器并等待就绪 -> 启动 GPT-SoVITS 语音服务 -> 托管/降级启动 QQ 大脑。
> * **特点**：全自愈拉起 AI 大脑运行环境。**抖音网关保持完全隔离关闭状态**，需使用专用的 `./control/start_douyin.sh` 脚本显式纯手动开启。
> 
> ### 🛑 一键彻底清理停机
> ```bash
> ./control/stop_all.sh
> ```
> * **执行流程**：停止抖音网关 -> 停止 QQ 网关并卸载 launchd 托管 -> 停止自主学习服务 -> 物理停用并释放后台 NapCat 容器 -> 强制清理所有潜在的 Python 残留。
> * **特点**：100% 洁净停机，彻底释放 CPU 和内存资源，无僵尸进程。

---

## 🩺 状态自检指令

在运行过程中，如果你想确认有哪些子服务正在工作，可以使用以下命令快速自检：

* **检查进程状态**：
  ```bash
  ps aux | grep -E "main.py --(gateway|douyin|auto-learn)" | grep -v grep
  ```
* **检查 launchd 托管状态**：
  ```bash
  launchctl list | grep myagent
  ```
* **检查 Docker 容器**：
  ```bash
  docker ps | grep napcat
  ```
