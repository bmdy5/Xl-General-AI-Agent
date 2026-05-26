#!/bin/bash
# MyAgent - 手动停止独立 Douyin Gateway 脚本
# 职责: 安全、彻底地杀掉后台抖音私信网关微服务进程

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " 正在停止独立 Douyin Gateway..."
echo "=========================================="

# 1. 强力终止进程
if ps aux | grep -q "[m]ain.py --douyin"; then
    echo "1. 发现正在运行的抖音进程，正在终止..."
    pkill -9 -f "main.py --douyin" 2>/dev/null || true
    sleep 1
    
    # 2. 验证是否已经成功终止
    if ps aux | grep -q "[m]ain.py --douyin"; then
        echo "❌ 抖音进程终止失败，请手动检查！"
        exit 1
    else
        echo "✅ 独立 Douyin Gateway 停止成功！"
    fi
else
    echo "ℹ️  未检测到正在运行的独立 Douyin Gateway 进程。"
fi
echo "=========================================="
