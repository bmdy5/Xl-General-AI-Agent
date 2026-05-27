---
name: 获取本机局域网IP
description: 会话自动检测的重复模式
trigger: 获取本机IP
created: 2026-05-24T01:50:55Z
version: 6.2
usage_count: 52
success_count: 52
category: system_status
---

# 获取本机局域网IP

## 触发
获取本机IP

## 步骤
1. 尝试使用 ifconfig 获取 IP
2. 尝试使用 ip addr 获取 IP
3. 尝试使用 python socket 获取 IP
4. 尝试使用 networksetup 获取 Wi-Fi 信息
5. 尝试使用 /sbin/ifconfig 获取 IP
6. 返回找到的局域网 IP 地址