---
name: 用亮哥已有Chrome打开网页并读取内容
description: 会话自动检测的重复模式
trigger: 用已有浏览器打开网页
created: 2026-05-26T03:32:39Z
version: 10.1
usage_count: 91
success_count: 91
category: development
---

# 用亮哥已有Chrome打开网页并读取内容

## 触发
用已有浏览器打开网页

## 步骤
1. 使用 open -a 'Google Chrome' <URL> 打开指定网页
2. 使用 osascript 中的 AppleScript 通过 execute JavaScript 获取当前标签页内容