# XL Agent 工业级部署方案设计 (Makefile 架构)

- **日期**: 2026-05-12
- **状态**: 提案中
- **目标**: 彻底解决腾讯云环境下的构建缓慢、脚本冲突及 DeepSeek 对话链报错问题。

## 1. 核心问题诊断

1.  **脚本冲突 (Sed-ception)**: 旧的 `deploy.sh` 动态修改 `Dockerfile` 导致字符串嵌套冲突。
2.  **源定位失效**: Debian 13 (Trixie) 更改了源文件路径，导致旧版加速逻辑失效。
3.  **对话链脆弱**: DeepSeek 严格要求 `tool_calls` 后必须紧跟 `tool` 结果，人为干预（如确认授权）会打断此链条。

## 2. 解决方案架构 (The Makefile Strategy)

我们将采用 `Makefile` + `Dockerfile (ARG)` + `Docker Compose` 的三位一体架构。

### 2.1 Makefile 指令集
- `make setup`: 初始化环境（创建目录、生成 `.env` 模板）。
- `make up`: 启动服务（自动检测网络环境并应用构建参数）。
- `make build`: 强制构建镜像（应用加速参数）。
- `make logs`: 查看 Agent 日志。
- `make qrcode`: 专门提取并查看 NapCat 扫码二维码。
- `make update`: 一键从 GitHub 同步代码并重启。

### 2.2 Dockerfile 优化
- **ARG 参数化**: 使用 `ARG USE_MIRROR=false` 控制构建过程。
- **精准换源**: 针对 Debian 13 路径（`/etc/apt/sources.list.d/debian.sources`）进行换源。
- **腾讯云优先**: 如果 `USE_MIRROR=true`，则切换到 `mirrors.tencentyun.com`。

### 2.3 健壮性补丁 (DeepSeek Fix)
- 在 `Agent.run` 循环中引入 `_repair_history` 逻辑。
- 采用 **“精准插队”** 算法：将占位符 `tool` 结果插入到 `assistant` 消息的紧后方，而不是消息列表末尾。

## 3. 实施计划

1.  **Phase 1**: 创建 `Makefile` 并优化 `Dockerfile`。
2.  **Phase 2**: 合入 `Agent.run` 历史修复补丁。
3.  **Phase 3**: 清理旧的 `deploy.sh` 和 `update.sh`，统一入口。
4.  **Phase 4**: 服务器端一键 `make update` 验证。

## 4. 预期效果
- **构建速度**: 国内服务器从 >5min 缩短至 <1min。
- **系统稳定性**: 彻底消除 `DeepseekException: An assistant message with 'tool_calls' must be followed by tool messages` 报错。
- **操作体验**: 无需关心脚本内部实现，只需记住 `make` 命令。
