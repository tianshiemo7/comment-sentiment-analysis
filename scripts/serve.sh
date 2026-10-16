#!/usr/bin/env bash
# 启动 FastAPI 推理服务（:8000），供前端 http://localhost:3000 调用。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-8000}"

echo "[serve] http://localhost:${PORT}  (接口文档: /docs)"
exec python -m uvicorn src.inference.app:app --host "$HOST" --port "$PORT" --reload
