#!/bin/bash
# MyAgent - 手动启动独立 Douyin Gateway 脚本
# 职责: 干净、安全地在后台拉起抖音私信网关微服务进程

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " 正在启动独立 Douyin Gateway..."
echo "=========================================="

# 1. 彻底清理历史残留进程，避免端口冲突
echo "1. 正在清理历史残留的抖音进程..."
pkill -9 -f "main.py --douyin" 2>/dev/null || true
sleep 1

# 2. 确保日志目录存在
mkdir -p logs

# 3. 在后台拉起服务
echo "2. 正在通过项目虚拟环境 python 启动抖音网关..."
nohup venv/bin/python -u main.py --douyin >> logs/douyin_gateway.log 2>&1 &

# 4. 验证进程是否拉起成功
sleep 2
if ps aux | grep -q "[m]ain.py --douyin"; then
    echo "✅ 独立 Douyin Gateway 启动成功！"
    echo "   - 实时日志文件: $PROJECT_DIR/logs/douyin_gateway.log"
    echo "   - 进程正在后台运行"
else
    echo "❌ 独立 Douyin Gateway 启动失败，请检查日志！"
    tail -n 10 logs/douyin_gateway.log
    exit 1
fi
echo "=========================================="
