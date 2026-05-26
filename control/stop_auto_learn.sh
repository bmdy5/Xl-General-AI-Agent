#!/bin/bash
# MyAgent - 手动停止 Auto Learn 自主学习脚本
# 职责: 安全、彻底地杀掉自主学习进程。支持 launchd 系统级卸载与后台进程强杀。

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PLIST_FILE="$HOME/Library/LaunchAgents/com.myagent.autolearn.plist"

echo "=========================================="
echo " 正在停止 Auto Learn..."
echo "=========================================="

# 1. 优先处理 launchd 卸载
if [ -f "$PLIST_FILE" ]; then
    echo "1. 正在从 launchd 守护进程中卸载托管服务..."
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    sleep 1
fi

# 2. 强杀残留进程，确保完全停掉
if ps aux | grep -q "[m]ain.py --auto-learn"; then
    echo "2. 正在清除任何残留的自主学习进程..."
    pkill -9 -f "main.py --auto-learn" 2>/dev/null || true
    sleep 1
fi

# 3. 验证状态
if ps aux | grep -q "[m]ain.py --auto-learn"; then
    echo "❌ 自主学习进程终止失败，请手动检查！"
    exit 1
else
    echo "✅ Auto Learn 停止成功！"
fi
echo "=========================================="
