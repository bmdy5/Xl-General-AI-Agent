#!/bin/bash
# XL Agent - 强力自愈重启中枢 (restart.sh)
# 职责: 一键强杀残留进程并干净后台启动 QQ Gateway 大脑

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo "  🌸 小萤大脑 QQ Gateway 自愈重启中枢"
echo "  $(date)"
echo "=========================================="

# 1. 检查 launchd 托管状态并执行安全重启
if launchctl list 2>/dev/null | grep -q com.myagent.qqgateway; then
    echo "1. 发现 launchd 系统级保活托管服务 (com.myagent.qqgateway)..."
    echo "   - 正在通过 launchctl 执行干净、安全的卸载与重新装载重启，防止双进程冲突..."
    launchctl unload ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null || true
    sleep 1.5
    # 彻底强杀残留，确保端口 8000 释放
    pkill -9 -f "main.py --gateway" 2>/dev/null || true
    sleep 0.5
    launchctl load ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null || true
    echo "   - launchd 托管重启信令已下发并重载完毕"
else
    echo "1. 未发现 launchd 托管服务，正在执行 nohup 独立强力重启..."
    pkill -9 -f "main.py --gateway" 2>/dev/null || true
    sleep 1.5
    echo "   - 残留进程已全部强力清理完毕"

    # 2. 后台启动 Gateway
    echo "2. 正在以独占模式后台启动最新版 QQ Gateway..."
    if [ -f "venv/bin/python" ]; then
        nohup venv/bin/python -u main.py --gateway >> logs/gateway.log 2>&1 &
    elif [ -f ".venv/bin/python" ]; then
        nohup .venv/bin/python -u main.py --gateway >> logs/gateway.log 2>&1 &
    else
        nohup python3 -u main.py --gateway >> logs/gateway.log 2>&1 &
    fi
fi

sleep 2.5

# 4. 验证启动状态
if ps aux | grep "main.py --gateway" | grep -v grep >/dev/null; then
    PID=$(ps aux | grep "main.py --gateway" | grep -v grep | awk '{print $2}' | head -n 1)
    echo "✅ QQ Gateway 启动并接管成功！"
    echo "   - 进程 PID: $PID"
    echo "   - 实时日志: tail -f logs/gateway.log"
else
    echo "❌ QQ Gateway 启动失败！请检查 logs/gateway.log"
    exit 1
fi
