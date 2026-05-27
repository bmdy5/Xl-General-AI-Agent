---
name: 重启QQ Gateway
description: 会话自动检测的重复模式
trigger: 重启 gateway
created: 2026-05-24T09:29:22Z
version: 1.3
usage_count: 3
success_count: 3
category: development
---

# 重启QQ Gateway

## 触发
重启 gateway

## 步骤
1. 检查当前进程状态
2. 执行 make gateway-restart 命令
3. 验证新进程是否启动
4. 检查日志确认重启成功