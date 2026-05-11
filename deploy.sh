#!/bin/bash

# =================================================================
#  XL Agent 生产环境一键部署脚本 (v2.1)
# =================================================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 开始部署 XL Agent...${NC}"

# 1. 环境检查
echo -e "${YELLOW}检查环境依赖...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 错误: 未检测到 Docker，请先安装 Docker。${NC}"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ 错误: 未检测到 docker compose，请先安装 Compose 插件。${NC}"
    exit 1
fi

# 2. 目录准备
echo -e "${YELLOW}准备数据目录...${NC}"
mkdir -p napcat_data agent_mem
chmod -R 777 napcat_data agent_mem

# 3. 配置文件检查
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ 错误: 未找到 .env 配置文件！${NC}"
    echo "请根据模板创建 .env 文件并填入 API_KEY。"
    exit 1
fi

# 4. 国内环境优化 (自动修复 Dockerfile 源)
IS_CN=false
if [[ $(curl -s --connect-timeout 2 ipinfo.io/country) == "CN" ]]; then
    IS_CN=true
elif ping -c 1 -W 2 baidu.com &> /dev/null; then
    IS_CN=true
fi

if [ "$IS_CN" = true ]; then
    echo -e "${GREEN}检测到国内网络环境，正在应用极致加速优化...${NC}"
    # 修改 Dockerfile 里的系统源
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' Dockerfile 2>/dev/null || true
    sed -i 's/security.debian.org/mirrors.aliyun.com/g' Dockerfile 2>/dev/null || true
    
    # 注入 Pip 加速配置到 Dockerfile (如果还没注入)
    if ! grep -q "mirrors.aliyun.com/pypi" Dockerfile; then
        sed -i '/WORKDIR \/app/a RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/' Dockerfile
    fi
    
    # 对 Debian 12 (Bookworm) 特殊处理，有些镜像使用新的 sources 格式
    # 这部分通常在构建时通过 Dockerfile 里的 RUN 指令处理更稳，
    # 我们已经在 Dockerfile 里加上了相关 RUN 指令。
fi

# 5. 停止旧容器 (如果存在)
echo -e "${YELLOW}正在清理旧容器...${NC}"
docker compose down

# 6. 启动与构建
echo -e "${GREEN}正在构建并启动容器...${NC}"
docker compose up -d --build

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 启动成功！${NC}"
    echo -e "${YELLOW}即将进入扫码界面，请准备好手机 QQ...${NC}"
    sleep 3
    # 7. 自动展示扫码日志
    docker logs -f napcat
else
    echo -e "${RED}❌ 启动失败，请检查 Docker 日志。${NC}"
    exit 1
fi
