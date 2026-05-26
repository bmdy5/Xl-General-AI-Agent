---
name: QQ图片下载与分析
description: 会话自动检测的重复模式
trigger: 用户发送CQ:image格式的图片消息
created: 2026-05-25T12:19:37Z
version: 1.5
usage_count: 5
success_count: 5
category: development
---

# QQ图片下载与分析

## 触发
用户发送CQ:image格式的图片消息

## 步骤
1. 使用curl下载图片链接到/tmp目录
2. 调用图片分析工具分析图片内容