#!/usr/bin/env bash
set -euo pipefail
cd /home/pergel/mcp
export PYTHONPATH="/home/pergel/mcp/apps/mcp-server/src:/home/pergel/mcp/packages/core/src:/home/pergel/mcp/packages/hello/src:/home/pergel/mcp/packages/geo/src"
exec /home/pergel/mcp/.venv/bin/uvicorn mcp_app.main:app --host 127.0.0.1 --port 8780 "$@"
