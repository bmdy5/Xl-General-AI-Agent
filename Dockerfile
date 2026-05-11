# 使用轻量级 Python 镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统依赖 (如需编译某些库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建记忆存储目录
RUN mkdir -p /root/.xlagent

# 暴露端口 (如果有需要外部访问的端口，比如 Dashboard)
EXPOSE 8765

# 启动命令
CMD ["python", "main.py", "--gateway"]
