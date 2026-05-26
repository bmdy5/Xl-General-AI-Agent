#!/bin/bash
# MyAgent - 手动启动 QQ Gateway 脚本
# 职责: 干净、安全地拉起 QQ 网关服务。兼容 launchd 系统级托管与 nohup 后台降级。

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PLIST_FILE="$HOME/Library/LaunchAgents/com.myagent.qqgateway.plist"

echo "=========================================="
echo " 正在启动 QQ Gateway..."
echo "=========================================="

# 1. 强力且干净地终止任何潜在残留的裸网关进程，防止双实例冲突
echo "1. 正在清理任何已运行的 QQ 网关进程..."
pkill -9 -f "main.py --gateway" 2>/dev/null || true
sleep 1

# 2. 检查 launchd 托管是否配置
if [ -f "$PLIST_FILE" ]; then
    echo "2. 检测到 launchd 托管服务 plist 配置..."
    echo "   正在执行干净的 launchctl unload & load 重新装载..."
    
    # 强制先卸载以防冲突
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    sleep 1
    
    # 装载启动
    launchctl load "$PLIST_FILE"
    
    sleep 2
    if launchctl list 2>/dev/null | grep -q com.myagent.qqgateway; then
        echo "✅ QQ Gateway 已成功通过 launchd 托管启动！"
    else
        echo "❌ launchctl 托管启动失败，正在降级为 nohup 模式..."
        nohup venv/bin/python -u main.py --gateway >> logs/gateway.log 2>&1 &
    fi
else
    echo "1. 未检测到 launchd 托管 plist 文件，正在以 nohup 后台模式拉起..."
    
    # 彻底清理残留
    pkill -9 -f "main.py --gateway" 2>/dev/null || true
    sleep 1
    
    mkdir -p logs
    nohup venv/bin/python -u main.py --gateway >> logs/gateway.log 2>&1 &
    
    sleep 2
    if ps aux | grep -q "[m]ain.py --gateway"; then
        echo "✅ QQ Gateway (Nohup 后台模式) 启动成功！"
    else
        echo "❌ QQ Gateway 启动失败，请检查日志！"
        tail -n 10 logs/gateway.log
        exit 1
    fi
fi

echo "   - 实时日志文件: $PROJECT_DIR/logs/gateway.log"
echo "=========================================="
