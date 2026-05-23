#!/bin/bash
# Stitch MCP wrapper — 直接调用 stitch-mcp 二进制，不走 npx
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export GOOGLE_CLOUD_PROJECT=stitch-496215
export PATH="$HOME/.stitch-mcp/google-cloud-sdk/bin:$HOME/.nvm/versions/node/v25.8.0/bin:$PATH"
export CLOUDSDK_PYTHON="$SCRIPT_DIR/../venv/bin/python3"
exec "$HOME/.npm/_npx/d7bcf1e9427e7044/node_modules/.bin/stitch-mcp" "$@"
