#!/bin/bash
# Debug stitch-mcp
export GOOGLE_CLOUD_PROJECT=stitch-496215
export PATH="/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin:$PATH"
export CLOUDSDK_PYTHON=/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/venv/bin/python3
echo "STDOUT:" 
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' | npx stitch-mcp 2>/tmp/s_err.txt
echo "---"
echo "STDERR:"
cat /tmp/s_err.txt
