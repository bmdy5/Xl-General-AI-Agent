#!/bin/bash
cd "$(dirname "$0")/.."
source venv/bin/activate

echo "👑 XL Agent — Pixel Dashboard"
echo ""

# 杀旧进程
lsof -ti:8765 | xargs kill -9 2>/dev/null
sleep 1

# 启动
python main.py --dashboard &
PID=$!
sleep 2

# 检查
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8765 | grep -q 200; then
    echo "✅ Dashboard ready: http://localhost:8765"
    echo "   浏览器打开上述地址"
    echo ""
    echo "   另一个终端: python main.py --dashboard-learn"
    echo "   或者在这个终端直接对话:"
else
    echo "❌ 启动失败，检查错误:"
    cat /tmp/xl_dashboard.log 2>/dev/null
fi

# 保持运行
wait $PID
