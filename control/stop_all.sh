#!/bin/bash
# MyAgent - 一键停止所有服务总控脚本
# 职责: 安全、彻底地停掉抖音网关、QQ网关、自动学习和 Docker NapCat 容器并完成资源清理

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " 正在一键终止 MyAgent 全套系统服务..."
echo "=========================================="

# 1. 停止独立抖音网关
if [ -f "control/stop_douyin.sh" ]; then
    bash control/stop_douyin.sh
fi

# 2. 停止 QQ Gateway 网关
if [ -f "control/stop_qq_gateway.sh" ]; then
    bash control/stop_qq_gateway.sh
fi

# 3. 停止自动学习服务
if [ -f "control/stop_auto_learn.sh" ]; then
    bash control/stop_auto_learn.sh
fi

# 4. 强力停掉并清理 Docker 中的 NapCat 容器，释放系统资源
if docker ps --format '{{.Names}}' | grep -q "^napcat$"; then
    echo "4. 正在停止后台 NapCat 容器..."
    docker stop napcat >/dev/null || true
    echo "✅ NapCat 容器已成功停止。"
fi

# 5. 最终二次兜底，强杀任何潜在的残留进程
pkill -9 -f "main.py --gateway" 2>/dev/null || true
pkill -9 -f "main.py --douyin" 2>/dev/null || true
pkill -9 -f "main.py --auto-learn" 2>/dev/null || true

echo "=========================================="
echo " 🌟 MyAgent 全套服务已全部停止并完成清理！"
echo "=========================================="
