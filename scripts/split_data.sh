#!/usr/bin/env bash
# 清洗、分词、划分、训练词表与模型数组。Windows 也可直接运行 python -m src.preprocessing。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "$#" -eq 0 ]; then
  set -- --input "${INPUT_FILE:-data/raw/comments.csv}"
fi
exec "${PYTHON:-python}" -m src.preprocessing --seed "${SEED:-42}" "$@"
