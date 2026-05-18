# XL Agent QQ Gateway 启动脚本
# 使用: bash start-agent.sh
# 一键启动 NapCat + Gateway，自动修复常见问题

set -e

PROJECT_DIR="/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent"
DOCKER_NAPCAT_IMAGE="mlikiowa/napcat-docker:latest"
QQ_NUMBER="3870213248"
NAPCAT_DATA_DIR="$HOME/napcat-data-tmp"
GATEWAY_LOG="$PROJECT_DIR/gateway.log"
STARTUP_LOG="$PROJECT_DIR/startup.log"

echo "=========================================="
echo "  XL Agent QQ Gateway 启动"
echo "  $(date)"
echo "==========================================" | tee -a "$STARTUP_LOG"

# ── 1. 检查 Docker 是否运行 ──
echo "" | tee -a "$STARTUP_LOG"
echo "1. 检查 Docker 状态..." | tee -a "$STARTUP_LOG"
if docker info &>/dev/null; then
    echo "   ✓ Docker 运行中" | tee -a "$STARTUP_LOG"
else
    echo "   ✗ Docker 未运行，启动 Docker Desktop..." | tee -a "$STARTUP_LOG"
    open -a Docker
    echo "   等待 Docker 就绪..."
    for i in $(seq 1 30); do
        sleep 2
        if docker info &>/dev/null; then
            echo "   ✓ Docker 已就绪" | tee -a "$STARTUP_LOG"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "   ✗ Docker 启动超时，请手动打开 Docker Desktop" | tee -a "$STARTUP_LOG"
            exit 1
        fi
    done
fi

# ── 2. 停止旧进程 ──
echo "" | tee -a "$STARTUP_LOG"
echo "2. 清理旧进程..." | tee -a "$STARTUP_LOG"
pkill -f "main.py --gateway" 2>/dev/null && echo "   ✓ 旧 gateway 已停止" || echo "   - 无旧 gateway 进程" | tee -a "$STARTUP_LOG"

# ── 3. 启动/检查 NapCat ──
echo "" | tee -a "$STARTUP_LOG"
echo "3. 检查 NapCat 容器..." | tee -a "$STARTUP_LOG"

# 如果容器存在但没运行，启动它
if docker ps -a --format '{{.Names}}' | grep -q "^napcat$"; then
    echo "   NapCat 容器已存在" | tee -a "$STARTUP_LOG"
    if docker ps --format '{{.Names}}' | grep -q "^napcat$"; then
        echo "   ✓ NapCat 已在运行" | tee -a "$STARTUP_LOG"
    else
        echo "   启动已存在的容器..." | tee -a "$STARTUP_LOG"
        docker start napcat 2>&1 | tee -a "$STARTUP_LOG"
    fi
else
    echo "   创建新 NapCat 容器..." | tee -a "$STARTUP_LOG"
    docker run -d --name napcat \
        -p 3020:3000 -p 3001:3001 -p 6099:6099 \
        -v "$NAPCAT_DATA_DIR:/app/.config/QQ" \
        -v "$NAPCAT_DATA_DIR:/root/.config/QQ" \
        -e ACCOUNT=3870213248 \
        mlikiowa/napcat-docker:latest 2>&1 | tee -a "$STARTUP_LOG"
    echo "   ✓ NapCat 容器已创建" | tee -a "$STARTUP_LOG"
fi

# ── 4. 等待 NapCat 就绪 ──
echo "" | tee -a "$STARTUP_LOG"
echo "4. 等待 NapCat 就绪..." | tee -a "$STARTUP_LOG"
NAPCAT_READY=false
for i in $(seq 1 20); do
    sleep 3
    if curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
        NAPCAT_READY=true
        echo "   ✓ NapCat API 就绪" | tee -a "$STARTUP_LOG"
        break
    fi
done

if [ "$NAPCAT_READY" = false ]; then
    echo "   ⚠️  NapCat API 未响应，检查是否需要扫码..." | tee -a "$STARTUP_LOG"
    if docker logs napcat 2>&1 | grep -q "二维码解码URL"; then
        echo "   ⚠️  需要扫码登录！" | tee -a "$STARTUP_LOG"
        echo "   生成二维码到桌面..." | tee -a "$STARTUP_LOG"
        docker exec napcat cat /app/napcat/cache/qrcode.png > /Users/xiaofeng/Desktop/qq_login_qrcode.png 2>/dev/null
        open /Users/xiaofeng/Desktop/qq_login_qrcode.png
        echo "   请用 QQ 扫桌面上的二维码，然后按回车继续..." | tee -a "$STARTUP_LOG"
        read -r
    fi
    
    # 再次等待就绪
    for i in $(seq 1 20); do
        sleep 3
        if curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
            NAPCAT_READY=true
            echo "   ✓ NapCat 已登录" | tee -a "$STARTUP_LOG"
            break
        fi
    done
    
    if [ "$NAPCAT_READY" = false ]; then
        echo "   ✗ NapCat 仍未就绪，请检查 Docker 日志" | tee -a "$STARTUP_LOG"
        exit 1
    fi
fi

# ── 5. 确保 WebSocket + HTTP 配置正确 ──
echo "" | tee -a "$STARTUP_LOG"
echo "5. 检查 OneBot 配置..." | tee -a "$STARTUP_LOG"
CONFIG_FILE="/app/napcat/config/onebot11_${QQ_NUMBER}.json"
WS_CONFIG=$(docker exec napcat cat "$CONFIG_FILE" 2>/dev/null || echo "")
if echo "$WS_CONFIG" | grep -q '"websocketServers"' && echo "$WS_CONFIG" | grep -q '3001'; then
    echo "   ✓ WebSocket 已配置" | tee -a "$STARTUP_LOG"
else
    echo "   写入 WebSocket + HTTP 配置..." | tee -a "$STARTUP_LOG"
    docker exec napcat sh -c "cat > $CONFIG_FILE" << 'EOF'
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
EOF
    # 重启 NapCat 加载配置
    echo "   重启 NapCat 加载配置..." | tee -a "$STARTUP_LOG"
    docker restart napcat 2>&1 | tee -a "$STARTUP_LOG"
    sleep 10
    
    # 检查是否登录还在（持久化数据卷应该保留了登录态）
    if curl -s --connect-timeout 3 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
        echo "   ✓ 登录态保持" | tee -a "$STARTUP_LOG"
    else
        echo "   ⚠️ 登录态丢失，请重新扫码..." | tee -a "$STARTUP_LOG"
        docker exec napcat cat /app/napcat/cache/qrcode.png > /Users/xiaofeng/Desktop/qq_login_qrcode.png 2>/dev/null
        open /Users/xiaofeng/Desktop/qq_login_qrcode.png
        echo "   请扫码后按回车继续..." | tee -a "$STARTUP_LOG"
        read -r
        # 等待登录
        for i in $(seq 1 20); do
            sleep 3
            curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1 && break
        done
        echo "   ✓ NapCat 已登录" | tee -a "$STARTUP_LOG"
    fi
fi

# ── 6. 启动 Gateway ──
echo "" | tee -a "$STARTUP_LOG"
echo "6. 启动 Gateway..." | tee -a "$STARTUP_LOG"
cd "$PROJECT_DIR"
nohup venv/bin/python main.py --gateway >> "$GATEWAY_LOG" 2>&1 &
sleep 3

if ps aux | grep -q "[m]ain.py --gateway"; then
    echo "   ✓ Gateway 运行中" | tee -a "$STARTUP_LOG"
else
    echo "   ✗ Gateway 启动失败" | tee -a "$STARTUP_LOG"
    tail -5 "$GATEWAY_LOG" | tee -a "$STARTUP_LOG"
    exit 1
fi

# ── 7. 最终状态 ──
echo "" | tee -a "$STARTUP_LOG"
echo "==========================================" | tee -a "$STARTUP_LOG"
echo "  ✅ XL Agent 已就绪" | tee -a "$STARTUP_LOG"
echo "  ⏰ $(date)" | tee -a "$STARTUP_LOG"
echo "==========================================" | tee -a "$STARTUP_LOG"
echo ""
echo "Bot 账号: $QQ_NUMBER"
echo "WebUI:    http://localhost:6099/webui"
echo "日志:     tail -f $GATEWAY_LOG"
echo "启动日志: $STARTUP_LOG"
echo ""
echo "用 QQ 给 bot 发消息测试"
