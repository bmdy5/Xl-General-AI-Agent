# 自动定位脚本所在目录
PROJECT_ROOT="/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
cd "$PROJECT_ROOT" || exit 1

echo "🔄 XL Agent QQ Gateway"
echo "================================"

# 1. 杀掉旧 gateway
echo "1. 清理旧进程..."
if launchctl list | grep -q com.myagent.qqgateway; then
    echo "   - 检测到 launchd 托管服务 com.myagent.qqgateway，由 launchctl 统一生命周期管理，跳过强杀防止自愈自启冲突"
else
    pkill -9 -f "main.py --gateway" 2>/dev/null && echo "   ✓ 旧 gateway 已停止" || echo "   - 无旧进程"
fi

# 2. 确保 NapCat 容器在运行
echo "2. 检查 NapCat 容器..."
NAPCAT_DATA="$HOME/.xlagent/napcat_data"
mkdir -p "$NAPCAT_DATA"

if docker ps --format '{{.Names}}' | grep -q "^napcat$"; then
    echo "   ✓ NapCat 容器运行中"
else
    echo "   启动 NapCat..."
    docker start napcat 2>/dev/null || docker run -d --name napcat \
        --restart always \
        -p 3000:3000 -p 3001:3001 \
        -p 5900:5900 -p 6099:6099 \
        -v "$NAPCAT_DATA":/app/.config/QQ \
        -v "$NAPCAT_DATA":/root/.config/QQ \
        mlikiowa/napcat-docker:latest -q 3870213248
    echo "   ✓ NapCat 已启动"
fi

# 3. 等 NapCat 就绪
echo "3. 等待 NapCat 就绪..."
for i in $(seq 1 15); do
    if curl -s http://localhost:3000/ -o /dev/null 2>/dev/null; then
        echo "   ✓ NapCat API 就绪"
        break
    fi
    sleep 2
done

# 4. 确保 WebSocket 配置正确
echo "4. 检查 WebSocket 配置..."
cat > .onebot11_config.json << 'NAPEOF'
{
  "network": {
    "httpServers": [
      {"name": "agent-http", "host": "0.0.0.0", "port": 3000, "enable": true}
    ],
    "websocketServers": [
      {"name": "agent-ws", "host": "0.0.0.0", "port": 3001, "enable": true}
    ],
    "httpSseServers": [],
    "httpClients": [],
    "websocketClients": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false,
  "imageDownloadProxy": "",
  "timeout": {
    "baseTimeout": 10000,
    "uploadSpeedKBps": 256,
    "downloadSpeedKBps": 256,
    "maxTimeout": 1800000
  }
}
NAPEOF
docker cp .onebot11_config.json napcat:/app/napcat/config/onebot11_3870213248.json 2>/dev/null
rm .onebot11_config.json
echo "   ✓ 配置已更新"

# 5. 清理 Python 缓存
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# 6. 启动 Gateway
echo "5. 启动 Gateway..."
if launchctl list | grep -q com.myagent.qqgateway; then
    echo "   ✓ 检测到 launchd 托管服务，正在通过 launchctl 重启以彻底避免双进程冲突..."
    launchctl unload ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null
    launchctl load ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null
    sleep 2
    if ps aux | grep -q "[m]ain.py --gateway"; then
        echo "   ✓ Gateway 已由 launchctl 重启并托管运行中"
    else
        echo "   ✗ Gateway 启动失败，请检查 launchd 配置"
    fi
else
    source venv/bin/activate
    nohup "$PROJECT_ROOT"/venv/bin/python main.py --gateway > /tmp/gateway.log 2>&1 &
    sleep 2
    if ps aux | grep -q "[m]ain.py --gateway"; then
        echo "   ✓ Gateway (Nohup 模式) 运行中"
    else
        echo "   ✗ Gateway 启动失败，查看 /tmp/gateway.log"
    fi
fi

# 7. 状态
echo ""
echo "================================"
echo "✅ Gateway 已就绪"
echo "📱 用 iPhone QQ 给 bot 发消息测试"
echo ""
echo "NapCat WebUI: http://localhost:6099/webui"
echo "Gateway 日志: /tmp/gateway.log"
echo ""
echo "如需扫码: docker exec napcat cat /app/napcat/cache/qrcode.png | open -f"
echo "================================"
