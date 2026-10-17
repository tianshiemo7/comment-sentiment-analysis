#!/usr/bin/env bash
# 统一评论采集入口；参数原样传给 Python 框架。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python -m src.crawler.spider "$@"
