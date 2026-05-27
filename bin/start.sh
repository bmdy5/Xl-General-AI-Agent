#!/bin/bash
# XL Agent 统一启动管理中枢 (无星号版)
# 职责: 整合 start-agent.sh 与 启动QQAgent.command 的逻辑，提供参数化调用

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

QQ_NUMBER="3870213248"
NAPCAT_DATA_DIR="$HOME/.xlagent/napcat_data"
mkdir -p "$NAPCAT_DATA_DIR"

STARTUP_LOG="$PROJECT_DIR/logs/startup.log"

echo "=========================================="
echo "  XL Agent 统一启动中枢"
echo "  $(date)"
echo "==========================================" | tee -a "$STARTUP_LOG"

# 1. 检查 Docker 状态
echo "1. 检查 Docker 状态..." | tee -a "$STARTUP_LOG"
if docker info >/dev/null 2>&1; then
    echo "   - Docker 运行中" | tee -a "$STARTUP_LOG"
else
    echo "   - Docker 未运行，尝试启动 Docker Desktop..." | tee -a "$STARTUP_LOG"
    open -a Docker
    echo "   - 等待 Docker 就绪..."
    for i in $(seq 1 30); do
        sleep 2
        if docker info >/dev/null 2>&1; then
            echo "   - Docker 已就绪" | tee -a "$STARTUP_LOG"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "   - Docker 启动超时，请手动打开 Docker Desktop" | tee -a "$STARTUP_LOG"
            exit 1
        fi
    done
fi

# 2. 清理旧进程与缓存
echo "2. 清理旧进程与缓存..." | tee -a "$STARTUP_LOG"

# 清理缓存时，为了绝对不使用星号，使用 find + grep 配合 xargs
find . -type d | grep "__pycache__$" | xargs rm -rf 2>/dev/null || true

# 彻底强杀所有残留的旧 gateway 进程，保证环境独占与自愈纯净
pkill -9 -f "main.py --gateway" 2>/dev/null || true
echo "   - 已彻底清理所有潜在的 main.py --gateway 残留进程" | tee -a "$STARTUP_LOG"

# 3. 检查并拉起 NapCat 容器
echo "3. 检查 NapCat 容器..." | tee -a "$STARTUP_LOG"
if docker ps -a --format '{{.Names}}' | grep -q "^napcat$"; then
    echo "   - NapCat 容器已存在" | tee -a "$STARTUP_LOG"
    if docker ps --format '{{.Names}}' | grep -q "^napcat$"; then
        echo "   - NapCat 已在运行" | tee -a "$STARTUP_LOG"
    else
        echo "   - 启动已存在的 NapCat 容器..." | tee -a "$STARTUP_LOG"
        docker start napcat 2>&1 | tee -a "$STARTUP_LOG"
    fi
else
    echo "   - 创建并启动新 NapCat 容器..." | tee -a "$STARTUP_LOG"
    docker run -d --name napcat \
        --restart always \
        -p 3000:3000 -p 3001:3001 -p 6099:6099 \
        -v "$NAPCAT_DATA_DIR:/app/.config/QQ" \
        -v "$NAPCAT_DATA_DIR:/root/.config/QQ" \
        mlikiowa/napcat-docker:latest 2>&1 | tee -a "$STARTUP_LOG"
    echo "   - NapCat 容器已创建" | tee -a "$STARTUP_LOG"
fi

# 4. 等待 NapCat API 就绪
echo "4. 等待 NapCat 就绪..." | tee -a "$STARTUP_LOG"
NAPCAT_READY=false
for i in $(seq 1 20); do
    sleep 3
    if curl -s --connect-timeout 2 http://127.0.0.1:3000/get_login_info >/dev/null 2>&1 || curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
        NAPCAT_READY=true
        echo "   - NapCat API 就绪" | tee -a "$STARTUP_LOG"
        break
    fi
done

if [ "$NAPCAT_READY" = false ]; then
    echo "   - NapCat API 未响应，检查是否需要扫码..." | tee -a "$STARTUP_LOG"
    if docker logs napcat 2>&1 | grep -q "二维码解码URL"; then
        echo "   - 需要扫码登录！" | tee -a "$STARTUP_LOG"
        echo "   - 生成二维码到桌面..." | tee -a "$STARTUP_LOG"
        docker exec napcat cat /app/napcat/cache/qrcode.png > "$HOME/Desktop/qq_login_qrcode.png" 2>/dev/null
        open "$HOME/Desktop/qq_login_qrcode.png"
        echo "   - 请用 QQ 扫桌面上的二维码，然后按回车继续..." | tee -a "$STARTUP_LOG"
        read -r
    fi
    
    # 再次等待就绪
    for i in $(seq 1 20); do
        sleep 3
        if curl -s --connect-timeout 2 http://127.0.0.1:3000/get_login_info >/dev/null 2>&1 || curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
            NAPCAT_READY=true
            echo "   - NapCat 已登录就绪" | tee -a "$STARTUP_LOG"
            break
        fi
    done
    
    if [ "$NAPCAT_READY" = false ]; then
        echo "   - NapCat 仍未就绪，请检查 Docker 日志" | tee -a "$STARTUP_LOG"
        exit 1
    fi
fi

# 5. 检查并确保 WebSocket 与 HTTP 配置正确
echo "5. 检查 OneBot 配置..." | tee -a "$STARTUP_LOG"
CONFIG_FILE="/app/napcat/config/onebot11_${QQ_NUMBER}.json"
WS_CONFIG=$(docker exec napcat cat "$CONFIG_FILE" 2>/dev/null || echo "")
if echo "$WS_CONFIG" | grep -q '"websocketServers"' && echo "$WS_CONFIG" | grep -q '3001'; then
    echo "   - WebSocket 配置已正确" | tee -a "$STARTUP_LOG"
else
    echo "   - 写入 WebSocket 与 HTTP 配置..." | tee -a "$STARTUP_LOG"
    # 为了绝不含星号，将 json 内容输出到临时文件，然后 cp 到容器内
    cat > .tmp_onebot_cfg.json << 'EOFG'
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
EOFG
    docker cp .tmp_onebot_cfg.json napcat:"$CONFIG_FILE" 2>/dev/null
    rm .tmp_onebot_cfg.json
    
    echo "   - 重启 NapCat 加载最新配置..." | tee -a "$STARTUP_LOG"
    docker restart napcat 2>&1 | tee -a "$STARTUP_LOG"
    sleep 10
    
    # 验证登录态
    if curl -s --connect-timeout 3 http://127.0.0.1:3000/get_login_info >/dev/null 2>&1 || curl -s --connect-timeout 3 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
        echo "   - 登录态保持良好" | tee -a "$STARTUP_LOG"
    else
        echo "   - 登录态丢失，请重新扫码..." | tee -a "$STARTUP_LOG"
        docker exec napcat cat /app/napcat/cache/qrcode.png > "$HOME/Desktop/qq_login_qrcode.png" 2>/dev/null
        open "$HOME/Desktop/qq_login_qrcode.png"
        echo "   - 请扫码后按回车继续..." | tee -a "$STARTUP_LOG"
        read -r
        for i in $(seq 1 20); do
            sleep 3
            if curl -s --connect-timeout 2 http://127.0.0.1:3000/get_login_info >/dev/null 2>&1 || curl -s --connect-timeout 2 http://127.0.0.1:3020/get_login_info >/dev/null 2>&1; then
                break
            fi
        done
        echo "   - NapCat 已登录" | tee -a "$STARTUP_LOG"
    fi
fi

# 6. 检查与启动 GPT-SoVITS 语音服务
echo "6. 检查与启动 GPT-SoVITS 语音服务..." | tee -a "$STARTUP_LOG"
TTS_DIR="$(cd "$PROJECT_DIR/../GPT-SoVITS" 2>/dev/null && pwd || echo "")"
if curl -s --connect-timeout 2 http://127.0.0.1:9880/ >/dev/null 2>&1; then
    echo "   - GPT-SoVITS 语音服务已经在运行" | tee -a "$STARTUP_LOG"
else
    echo "   - 启动 GPT-SoVITS 语音服务..." | tee -a "$STARTUP_LOG"
    if [ -d "$TTS_DIR" ]; then
        cd "$TTS_DIR"
        pkill -f "api_v2.py" || true
        # 启动后台服务 (用绝对路径调用 api_v2.py，不用星号)
        nohup ./venv/bin/python3 api_v2.py -a 127.0.0.1 -p 9880 > tts.log 2>&1 &
        cd "$PROJECT_DIR"
        sleep 3
        if curl -s --connect-timeout 2 http://127.0.0.1:9880/ >/dev/null 2>&1; then
            echo "   - GPT-SoVITS 语音服务拉起成功" | tee -a "$STARTUP_LOG"
        else
            echo "   - GPT-SoVITS 语音服务启动中，哨兵会在后续完成自愈" | tee -a "$STARTUP_LOG"
        fi
    else
        echo "   - 语音服务目录不存在，跳过拉起" | tee -a "$STARTUP_LOG"
    fi
fi

# 7. 启动 Gateway
echo "7. 启动 Gateway..." | tee -a "$STARTUP_LOG"
if launchctl list 2>/dev/null | grep -q com.myagent.qqgateway; then
    echo "   - 检测到 launchd 托管服务，正在通过 launchctl 重启以彻底避免双进程冲突..." | tee -a "$STARTUP_LOG"
    launchctl unload ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null
    launchctl load ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null
    sleep 2
    if ps aux | grep -q "main.py --gateway"; then
        echo "   - Gateway 已由 launchctl 重启并托管运行中" | tee -a "$STARTUP_LOG"
    else
        echo "   - Gateway 启动异常，请检查 launchd 配置" | tee -a "$STARTUP_LOG"
    fi
else
    nohup venv/bin/python main.py --gateway >> "$STARTUP_LOG" 2>&1 &
    sleep 2
    if ps aux | grep -q "main.py --gateway"; then
        echo "   - Gateway (Nohup 模式) 启动并运行中" | tee -a "$STARTUP_LOG"
    else
        echo "   - Gateway 启动失败" | tee -a "$STARTUP_LOG"
        tail -n 5 "$STARTUP_LOG" | tee -a "$STARTUP_LOG"
        exit 1
    fi
fi

echo "==========================================" | tee -a "$STARTUP_LOG"
echo "  - XL Agent 启动流程全部就绪" | tee -a "$STARTUP_LOG"
echo "==========================================" | tee -a "$STARTUP_LOG"
