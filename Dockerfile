# XL Agent — 工业级 Dockerfile (v3.0)
# Debian 13 (Trixie) + ARG 镜像源切换
FROM python:3.11-slim

ARG USE_MIRROR=false

ENV PYTHONUNBUFFERED=1
ENV XLA_MEM_DIR=/root/.xlagent

WORKDIR /app

# ── 系统源：Debian 13 使用 debian.sources 格式 ──
# 去掉协议头匹配，兼容 http/https 两种 URL
RUN if [ "$USE_MIRROR" = "true" ]; then \
      echo "切换腾讯云 Debian 镜像源..." && \
      sed -i 's|deb.debian.org|mirrors.tencentyun.com|g' /etc/apt/sources.list.d/debian.sources && \
      sed -i 's|security.debian.org|mirrors.tencentyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi

# ── 系统依赖 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Pip 源 ──
RUN if [ "$USE_MIRROR" = "true" ]; then \
      pip config set global.index-url http://mirrors.tencentyun.com/pypi/simple/ && \
      pip config set global.trusted-host mirrors.tencentyun.com; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p ${XLA_MEM_DIR}

EXPOSE 8765

CMD ["python", "main.py", "--gateway"]
