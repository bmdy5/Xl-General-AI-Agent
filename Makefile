# XL Agent — 工业级 Makefile (v3.0)
# 用法: make setup | up | build | logs | qrcode | update

.PHONY: setup up build logs qrcode update clean

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
