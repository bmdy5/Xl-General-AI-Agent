#!/bin/bash

# =================================================================
#  XL Agent 生产环境一键部署脚本 (v2.2 - 腾讯云深度优化版)
# =================================================================

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🚀 开始部署 XL Agent (v2.2)...${NC}"

# 1. 环境检查
echo -e "${YELLOW}检查环境依赖...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ 错误: 未检测到 Docker，请先在宝塔里安装 Docker 服务。${NC}"
    exit 1
fi

# 2. 补全依赖 (自动修复之前的疏忽)
echo -e "${YELLOW}同步依赖清单...${NC}"
cat <<EOF > requirements.txt
aiohttp==3.11.11
aiofiles==24.1.0
python-dotenv==1.0.1
litellm==1.59.3
requests==2.32.3
beautifulsoup4==4.12.3
duckduckgo_search==6.3.3
playwright==1.49.1
EOF

# 3. 目录准备
mkdir -p napcat_data agent_mem
chmod -R 777 napcat_data agent_mem

# 4. 国内环境深度加速
if [[ $(curl -s --connect-timeout 2 ipinfo.io/country) == "CN" ]] || ping -c 1 -W 2 baidu.com &> /dev/null; then
    echo -e "${GREEN}检测到国内环境，正在应用极致加速 (腾讯云优先)...${NC}"
    
    # 修改 Dockerfile 里的系统源为腾讯云内网源 (更稳更快)
    sed -i 's/deb.debian.org/mirrors.tencentyun.com/g' Dockerfile 2>/dev/null || true
    sed -i 's/security.debian.org/mirrors.tencentyun.com/g' Dockerfile 2>/dev/null || true
    
    # 修改 Dockerfile 里的 Pip 源为腾讯云内网源
    if ! grep -q "mirrors.tencentyun.com" Dockerfile; then
        sed -i '/WORKDIR \/app/a RUN pip config set global.index-url http://mirrors.tencentyun.com/pypi/simple/ && pip config set global.trusted-host mirrors.tencentyun.com' Dockerfile
    fi
fi

# 5. 配置文件检查
if [ ! -f ".env" ]; then
    echo -e "${RED}⚠️ 警告: 未找到 .env 配置文件！${NC}"
    echo "请根据提示创建 .env，否则机器人将无法工作。"
fi

# 6. 启动容器
echo -e "${YELLOW}正在清理旧容器并重新构建启动...${NC}"
docker compose down
docker compose up -d --build

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 部署成功！${NC}"
    echo -e "${YELLOW}即将显示扫码二维码，请准备好手机 QQ...${NC}"
    sleep 2
    docker logs -f napcat
else
    echo -e "${RED}❌ 部署失败，请检查 Dockerfile 内容或网络。${NC}"
    exit 1
fi
