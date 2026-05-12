# XL Agent Makefile 部署架构替换计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Makefile + Dockerfile (ARG) + Docker Compose 的三位一体架构，彻底解决腾讯云环境下的构建缓慢及脚本维护混乱问题。

**Architecture:** 
1. **Makefile**: 作为统一入口，通过 `make` 命令封装复杂的 Docker 命令。
2. **Dockerfile**: 引入 `ARG USE_MIRROR` 和针对 Debian 13 (Trixie) 的精准换源逻辑。
3. **Docker Compose**: 配合 Makefile 实现参数化构建，并保持与 NapCat 的网络通信。

**Tech Stack:** Makefile, Docker, Docker Compose, Python 3.10-slim (Debian 13)

---

### Task 1: 创建 Makefile 核心入口

**Files:**
- Create: `Makefile`

- [ ] **Step 1: 编写 Makefile**

```makefile
# XL Agent Deployment Makefile

IMAGE_NAME = xl-agent
CONTAINER_NAME = xl-agent
USE_MIRROR ?= false

.PHONY: setup up build stop restart logs qrcode update help

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  setup    Initialize environment (create directories, .env template)"
	@echo "  build    Build docker image (use USE_MIRROR=true for Tencent Cloud)"
	@echo "  up       Start services in background"
	@echo "  stop     Stop services"
	@echo "  restart  Restart services"
	@echo "  logs     View agent logs"
	@echo "  qrcode   Show NapCat QR code for login"
	@echo "  update   Pull from git and restart"

setup:
	mkdir -p agent_mem napcat_data
	if [ ! -f .env ]; then cp .env.example .env || touch .env; fi

build:
	docker compose build --build-arg USE_MIRROR=$(USE_MIRROR)

up:
	docker compose up -d

stop:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f xl-agent

qrcode:
	@echo "Fetching NapCat QR code..."
	@docker compose logs napcat | grep -i "qrcode" || echo "QR Code not found in logs, please check WebUI: http://localhost:6099"

update:
	git pull origin master
	$(MAKE) build
	$(MAKE) up
```

- [ ] **Step 2: 提交 Makefile**

```bash
git add Makefile
git commit -m "feat: add Makefile for unified deployment entry"
```

---

### Task 2: 优化 Dockerfile (支持 Debian 13 换源)

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: 修改 Dockerfile 引入 ARG 和换源逻辑**

```dockerfile
FROM python:3.10-slim

# 允许构建时传入是否换源
ARG USE_MIRROR=false

WORKDIR /app

# 针对 Debian 13 (Trixie) 的精准换源逻辑
# 注意：Debian 13 的源文件路径是 /etc/apt/sources.list.d/debian.sources
RUN if [ "$USE_MIRROR" = "true" ]; then \
    sed -i 's/deb.debian.org/mirrors.tencentyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.tencentyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN if [ "$USE_MIRROR" = "true" ]; then \
    pip install --no-cache-dir -r requirements.txt -i https://mirrors.tencentyun.com/pypi/simple; \
    else \
    pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . .

RUN mkdir -p /root/.xlagent
EXPOSE 8765

CMD ["python", "main.py", "--gateway"]
```

- [ ] **Step 2: 提交 Dockerfile**

```bash
git add Dockerfile
git commit -m "perf: optimize Dockerfile with ARG mirror support for Debian 13"
```

---

### Task 3: 更新 Docker Compose 配合参数化构建

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: 在 build 段添加 args**

```yaml
# 在 xl-agent 服务下的 build 部分修改
  xl-agent:
    build:
      context: .
      args:
        - USE_MIRROR=${USE_MIRROR:-false}
```

- [ ] **Step 2: 提交变更**

```bash
git add docker-compose.yml
git commit -m "build: support USE_MIRROR build arg in docker-compose"
```

---

### Task 4: 清理旧脚本及遗留配置

**Files:**
- Delete: `deploy.sh`, `update.sh`, `run.sh`

- [ ] **Step 1: 删除冗余脚本**

```bash
rm deploy.sh update.sh run.sh
```

- [ ] **Step 2: 提交清理动作**

```bash
git add .
git commit -m "cleanup: remove obsolete deployment scripts"
```

---

### Task 5: 最终验证与环境初始化

- [ ] **Step 1: 初始化环境**

Run: `make setup`
Expected: `agent_mem` 和 `napcat_data` 目录被创建。

- [ ] **Step 2: 模拟腾讯云构建测试**

Run: `make build USE_MIRROR=true`
Expected: 构建日志中显示使用腾讯云镜像源，构建速度显著提升。

- [ ] **Step 3: 启动并检查日志**

Run: `make up && make logs`
Expected: 容器成功启动，Agent 正常运行。
