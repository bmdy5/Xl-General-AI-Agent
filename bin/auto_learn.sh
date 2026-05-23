#!/bin/bash
# MyAgent 自主学习定时任务脚本
# 每天自动触发，浏览网页学习 1 小时

cd "$(dirname "$0")/.."
source venv/bin/activate
exec python main.py --auto-learn
