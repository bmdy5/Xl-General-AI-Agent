---
name: 下载Google项目中的最新Screen
description: 会话自动检测的重复模式
trigger: 根据Google项目链接生成网站；触发词：下载屏幕
created: 2026-05-25T09:33:19Z
version: 3.4
usage_count: 23
success_count: 23
category: development
---

# 下载Google项目中的最新Screen

## 触发
- 根据Google项目链接生成网站
- 触发词：下载屏幕

## 步骤
1. 设置认证TOKEN（获取OAuth Token）
2. 列出项目所有screens
3. 定位目标screen（如按名称筛选）
4. 构造下载URL并执行HTTP请求，下载screen内容
5. 保存为本地HTML文件