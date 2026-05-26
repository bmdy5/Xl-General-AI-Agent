#!/bin/bash
# MyAgent - 一键启动所有服务总控脚本
# 职责: 整合运行环境依赖自愈（Docker, NapCat, GPT-SoVITS, QQ Gateway）并最终拉起 Douyin Gateway

set -e

# 定位项目根目录 (控制脚本在 control/ 目录下，根目录需向外跳一级)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=========================================="
echo " 正在一键启动 MyAgent 全套系统服务..."
echo "=========================================="

# 1. 优先调用系统的统一启动中枢，自愈拉起环境依赖和 QQ 大脑网关
if [ -f "bin/start.sh" ]; then
    echo "1. 执行系统统一环境自愈并拉起核心服务..."
    bash bin/start.sh
else
    echo "❌ 错误: 未找到 bin/start.sh 系统初始化脚本！"
    exit 1
fi

echo ""
echo "=========================================="
echo " 🎉 MyAgent 系统核心服务一键拉起就绪！"
echo "=========================================="
