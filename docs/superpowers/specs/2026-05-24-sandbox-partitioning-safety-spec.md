# Spec: XL Agent 智能安全分区沙箱规范 ("方案A+")

本规范定义了 XL Agent 在物理路径安全拦截、Bash 高危操作狙击、以及长期记忆静默沉淀方面的全新标准，彻底在“大模型高度自动化”与“工业级系统安全保护”之间取得完美闭环。

---

## 1. 📂 智能安全分区（Safe-Zone Partitioning）

系统将项目物理空间划分为两个清晰的对立区域，采取不同的权限判定逻辑：

### 📌 绝对保护区 (Protected Zone)
*   **物理范围**：
    *   `agent/` 及其下所有嵌套子目录（源码核心包）。
    *   项目根目录下的所有地标元文件：`main.py`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `pytest.ini`, `requirements.txt` 等。
*   **权限规则**：凡是 `write_file` / `edit_file` / `bash` 的写操作目标包含此区域内的任何路径，**必须升级为 `WRITE` 或 `DANGEROUS` 权限，触发人工审批拦截**。

### 📌 自由安全区 (Safe Zone)
*   **物理范围**：
    *   `logs/` 全局运行日志文件夹。
    *   `.my-agent/memory/` 及其子目录（SQLite 长期记忆与 RAG 存储区）。
    *   `agent_mem/`、`napcat_data/` 等临时数据与运行缓存文件夹。
    *   其他任何非源码、非系统地标的临时导出文件。
*   **权限规则**：对此区域的文件创建、修改、追加等操作，**100% 自动静默放行，零人工打扰**。

---

## 2. 🛡️ 隐式高危 Bash 命令限制审计

虽然大模型被限制使用 `rm`，但系统必须对可能伪装成“非删除命令”的隐式破坏行为进行动态审计：

| 高危行为模式 | 正则审计特征 | 风险等级 | 说明 |
| :--- | :--- | :--- | :--- |
| **标准物理删除** | `\brm\b`, `\brmdir\b`, `\bshred\b` | `DANGEROUS` | 直接删除文件或目录 |
| **重定向清空写入** | `>\s{0,}[a-zA-Z0-9_\-\.\/]+` | `DANGEROUS` | 当重定向的目标文件处于绝对保护区时，会瞬间清空源码 |
| **物理转移覆写** | `\bmv\b` | `DANGEROUS` | 将源码移动或将外来文件转移覆盖源码文件 |
| **Python 代码删除** | `\bos\.remove\b`, `\bshutil\.rmtree\b` | `DANGEROUS` | 利用 python 命令行脚本绕过 shell `rm` 阻断器 |
| **杀系统进程** | `\bpkill\b`, `\bkill(all)?\b` | `DANGEROUS` | 破坏系统高可用性，杀掉网关或 TTS 进程 |

---

## 3. 🧠 记忆自律管理

*   小萤应通过长期记忆文件 [`xl_identity.md`](file:///Users/xiaofeng/.my-agent/memory/xl_identity.md) 和系统提示词确立行为红线：
    *   *“我是亮哥的专属助手小萤，在没有得到亮哥通过/同意前，绝对不修改任何 `agent/` 目录下的核心源码，绝不运行任何含有破坏性质的 bash 指令。”*
*   `save_memory` 的 `add` / `replace` / `search` / `merge_to_core` 动作降级为默认 `SAFE`，自动落盘；仅在调用 `remove` 物理销毁某条长期记忆时，才触发 `WRITE` 确认。

---

## 4. 防坑与升级红线 (Critical Warnings for AI)

接班的 AI，在你未来的开发或维护中，请绝对遵循本沙箱规范：
1.  **绝对禁止**将 `is_path_protected()` 的判定降级或剔除。
2.  **绝对禁止**在没有对 `input_args["file_path"]` 绝对路径标准化的情况下直接写入。
3.  **绝对禁止**在 `BashTool` 中放开对重定向和高级脚本语言删除命令的拦截。
