---
name: 重启Agent全流程测试
description: 会话自动检测的重复模式
trigger: 重启测试全流程 | 重启 | 重新启动 | 跑一下
created: 2026-05-24T09:03:49Z
version: 5.6
usage_count: 45
success_count: 45
category: system_status
---

# 重启Agent全流程测试

## 触发
- 重启测试全流程
- 重启
- 重新启动
- 跑一下

## 步骤
1. 查找启动入口文件。
2. 查看最近代码改动。
3. 查找当前运行的Agent进程。
4. 终止当前运行的Agent进程（若存在）。
5. 确认进程已被完全清理。
6. 运行启动脚本：`bash control/start_all.sh`。
7. 测试新进程是否正常启动并运行。