---
name: browser_agent超时排查
description: 会话自动检测的重复模式
trigger: browser_agent超时
created: 2026-05-26T04:44:34Z
version: 6.5
usage_count: 55
success_count: 55
category: development
---

# browser_agent超时排查

## 触发
browser_agent超时

## 步骤
1. 检查browser_agent是否连续超时
2. 检查网关进程是否存在
3. 检查端口监听状态
4. 查看相关代码和配置