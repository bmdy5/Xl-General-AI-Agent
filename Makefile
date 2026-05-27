# XL Agent — 工业级 Makefile (v3.0)
# 用法: make setup | up | build | logs | qrcode | update

.PHONY: setup up build logs qrcode update clean gateway-restart

# ── 检测国内环境 ──
IS_CN := $(shell curl -s --connect-timeout 2 ipinfo.io/country 2>/dev/null | grep -q CN && echo true || echo false)

# ── 初始化环境 ──
setup:
	@echo "📁 创建运行时目录..."
	mkdir -p napcat_data agent_mem
	chmod -R 777 napcat_data agent_mem
	@if [ ! -f .env ]; then \
		echo "⚠️  未找到 .env，生成模板..."; \
		echo 'MYAGENT_MODEL=openai/gpt-4o' > .env; \
		echo 'MYAGENT_API_KEY=your-api-key' >> .env; \
		echo 'MYAGENT_API_BASE=' >> .env; \
		echo "✅ .env 模板已生成，请编辑填入真实 Key"; \
	else \
		echo "✅ .env 已存在"; \
	fi
	@echo "✅ 初始化完成"

# ── 构建镜像 ──
build:
ifeq ($(IS_CN),true)
	@echo "🇨🇳 检测到国内环境，启用腾讯云镜像加速..."
	docker compose build --build-arg USE_MIRROR=true
else
	@echo "🌍 国际环境，使用默认源..."
	docker compose build --build-arg USE_MIRROR=false
endif
	@echo "✅ 构建完成"

# ── 启动服务 ──
up: setup
ifeq ($(IS_CN),true)
	docker compose build --build-arg USE_MIRROR=true
else
	docker compose build --build-arg USE_MIRROR=false
endif
	docker compose up -d
	@echo "✅ 服务已启动"
	@echo "📱 扫码登录: make qrcode"
	@echo "📋 查看日志: make logs"

# ── 查看 Agent 日志 ──
logs:
	docker compose logs -f --tail=50 xl-agent

# ── 提取扫码二维码 ──
qrcode:
	@echo "📱 获取 NapCat 登录二维码..."
	@docker logs napcat 2>&1 | grep -A 1 "qrcode\|二维码\|login\|扫描" | tail -20 || echo "⚠️  未找到二维码，尝试查看完整日志: docker logs napcat"

# ── 一键更新并重启 ──
update:
	@echo "🔄 拉取最新代码..."
	git pull origin master
	@echo "🔨 重新构建..."
	docker compose build --build-arg USE_MIRROR=$(IS_CN)
	docker compose up -d --force-recreate
	@echo "✅ 更新完成"

# ── 停止并清理 ──
clean:
	docker compose down
	@echo "✅ 已停止"



# ── 重启 QQ Gateway（改完代码后执行） ──
gateway-restart:
	@echo "🔄 正在重启 QQ Gateway..."
	@if launchctl list 2>/dev/null | grep -q com.myagent.qqgateway; then \
		echo "   - 检测到 launchd 托管服务，正在通过 launchctl 执行干净、安全的卸载与重新装载重启..."; \
		launchctl unload ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null || true; \
		sleep 1; \
		launchctl load ~/Library/LaunchAgents/com.myagent.qqgateway.plist 2>/dev/null || true; \
	else \
		echo "   - 未检测到 launchd 服务，正在通过 nohup 独立强力重启..."; \
		pkill -9 -f "main.py --gateway" 2>/dev/null || true; \
		sleep 1; \
		mkdir -p logs; \
		nohup venv/bin/python -u main.py --gateway >> logs/gateway.log 2>&1 & \
	fi

	@echo "✅ QQ Gateway 重启完成"
	@echo "   查看日志: tail -f logs/gateway.log"

# ── 重启独立 Douyin Gateway（改完代码后执行） ──
douyin-restart:
	@echo "⚠️ 独立 Douyin Gateway 已按照主人要求物理下线并禁用，不再生成日志。"

# ── 一键物理技能增量去重 ──

skills-dedup:
	@echo "🔄 正在一键增量合并 skills/ 冗余技能..."
	PYTHONPATH=. venv/bin/python agent/skills/cleanup.py --incremental




