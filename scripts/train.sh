#!/usr/bin/env bash
# 一键训练：预处理 + 训练 + 评估 + 出图。
# 现状：骨架占位，实现后请把下面 TODO 替换成真实逻辑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[train] 训练两种模型用于对比：cnn / bilstm_attention"

# TODO(阶段二): 训练词向量
#   python -m src.embeddings.word2vec --input data/splits/train.csv --output models/word2vec/

# TODO(阶段三): 训练模型（可循环两个模型）
#   python -m src.training.train --model cnn              --config configs/cnn.yaml
#   python -m src.training.train --model bilstm_attention --config configs/bilstm_attention.yaml
# 产出: models/checkpoints/*_best.pt + 同名 .json（超参与指标）

# TODO(阶段四): 评估与可视化
#   python -m src.evaluation.report     --model cnn
#   python -m src.visualization.plots   --run-dir outputs/logs/

echo "[train] TODO: 尚未实现，请按 docs/开发路线图.md 的阶段二~四完成。"
