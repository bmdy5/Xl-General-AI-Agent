#!/bin/bash

# =================================================================
#  XL Agent 自动更新与热部署脚本 (v1.0)
# =================================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🔄 正在从 GitHub 同步最新代码...${NC}"

# 1. 强制同步
git fetch origin master
git reset --hard origin/master

# 2. 继承权限
chmod +x deploy.sh

# 3. 调用部署脚本
echo -e "${GREEN}✨ 同步完成，准备重新启动服务...${NC}"
./deploy.sh
