#!/usr/bin/env bash
# 一键训练：预处理（可选）→ 词向量（可选）→ 两模型训练 → 测试评估 → 出图。
#
# 常用环境变量：
#   INPUT_FILE=...        标注 CSV（data/splits 不存在时用它跑预处理）
#   ALLOW_SMALL_DATASET=1 小样本调试（样例数据必须开）
#   MODELS="textcnn bilstm_attention"
#   WITH_WORD2VEC=1       先训练 Word2Vec 并用 embedding_matrix.npy 初始化
#   TRAIN_ARGS="--epochs 20 --batch-size 64"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python}"
INPUT_FILE="${INPUT_FILE:-data/raw/comments.csv}"
MODELS="${MODELS:-textcnn bilstm_attention}"
WITH_WORD2VEC="${WITH_WORD2VEC:-0}"
TRAIN_ARGS="${TRAIN_ARGS:-}"

if [ ! -f data/splits/train.csv ]; then
  echo "[train] 未找到 data/splits/train.csv，先跑预处理：$INPUT_FILE"
  if [ ! -f "$INPUT_FILE" ]; then
    echo "[train] 缺少输入数据：$INPUT_FILE" >&2
    echo "[train] 请先准备已标注 CSV（见 data/README.md），或设 INPUT_FILE=/path/to/comments.csv" >&2
    exit 1
  fi
  SMALL_FLAG=""
  [ "${ALLOW_SMALL_DATASET:-0}" = "1" ] && SMALL_FLAG="--allow-small-dataset"
  "$PYTHON" -m src.preprocessing --input "$INPUT_FILE" $SMALL_FLAG
fi

if [ ! -f data/processed/vocab.json ]; then
  echo "[train] 缺少 data/processed/vocab.json，请先完成预处理" >&2
  exit 1
fi

EMBEDDING_ARGS=""
if [ "$WITH_WORD2VEC" = "1" ]; then
  echo "[train] 训练 Word2Vec + embedding 矩阵"
  "$PYTHON" -m src.embeddings --train-file data/splits/train.csv \
    --vocab data/processed/vocab.json --out models/word2vec
  EMBEDDING_ARGS="--embeddings models/word2vec/embedding_matrix.npy"
fi

for MODEL in $MODELS; do
  echo "[train] ===== $MODEL ====="
  "$PYTHON" -m src.training.cli --model "$MODEL" $EMBEDDING_ARGS $TRAIN_ARGS
done

echo "[train] 完成：检查点 models/checkpoints/、日志 outputs/logs/、指标 outputs/stats/、图表 outputs/figures/"
