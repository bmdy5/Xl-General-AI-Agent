#!/bin/bash
# MyAgent 启动脚本
# 用法: ./run.sh                    # 交互模式
#       ./run.sh "你的问题"          # 单次模式

cd "$(dirname "$0")"
source .venv/bin/activate
exec python main.py "$@"
