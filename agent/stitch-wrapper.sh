#!/bin/bash
# Stitch MCP wrapper — 直接调用 stitch-mcp 二进制，不走 npx
export GOOGLE_CLOUD_PROJECT=stitch-496215
export PATH="/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin:/Users/xiaofeng/.nvm/versions/node/v25.8.0/bin:$PATH"
export CLOUDSDK_PYTHON=/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3
exec /Users/xiaofeng/.npm/_npx/d7bcf1e9427e7044/node_modules/.bin/stitch-mcp "$@"
