#!/bin/bash
PROJECT_ROOT="/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
cd "$PROJECT_ROOT" || exit 1
exec bash bin/start.sh "$@"
