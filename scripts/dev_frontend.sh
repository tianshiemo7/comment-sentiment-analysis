#!/usr/bin/env bash
# 一键启动前端开发服务器（:3000）。后端请另开终端运行 scripts/serve.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/frontend"

if [ ! -d node_modules ]; then
  echo "[dev] 首次运行，安装依赖..."
  npm install
fi

echo "[dev] 前端 http://localhost:3000  (需同时运行 scripts/serve.sh)"
exec npm run dev
