#!/usr/bin/env bash
# 划分数据集：训练 : 验证 : 测试 ≈ 7 : 1 : 2（固定随机种子，保证可复现）。
# 现状：骨架占位，实现后请把下面 TODO 替换成真实逻辑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SEED="${SEED:-42}"
echo "[split_data] 输入: data/processed/   输出: data/splits/   seed=$SEED"

# TODO(阶段一): 按 7:1:2 划分，输入应已在 src/preprocessing 中完成清洗与分词
#   python -m src.preprocessing.split --input data/processed/ --output data/splits/ --seed "$SEED"
# 产出: data/splits/train.csv  val.csv  test.csv
# 同时在 outputs/stats/dataset_stats.json 记录各集合条数与标签分布

echo "[split_data] TODO: 尚未实现，请先完成 src/preprocessing/ 模块。"
