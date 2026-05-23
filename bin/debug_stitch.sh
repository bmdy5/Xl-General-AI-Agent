#!/bin/bash
# Debug stitch-mcp
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export GOOGLE_CLOUD_PROJECT=stitch-496215
export PATH="$HOME/.stitch-mcp/google-cloud-sdk/bin:$PATH"
export CLOUDSDK_PYTHON="$SCRIPT_DIR/../venv/bin/python3"
echo "STDOUT:" 
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' | npx stitch-mcp 2>/tmp/s_err.txt
echo "---"
echo "STDERR:"
cat /tmp/s_err.txt
