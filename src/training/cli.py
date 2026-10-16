"""训练命令行入口骨架。

用法（实现后）::

    python -m src.training.cli --model textcnn --epochs 20 --batch-size 64
    python -m src.training.cli --model bilstm_attention --lr 1e-3 --seed 42
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m src.training.cli",
        description="训练 TextCNN / BiLSTM-Attention 情感分类模型（骨架阶段仅打印配置）",
    )
    parser.add_argument("--model", default="textcnn", choices=["textcnn", "bilstm_attention"])
    parser.add_argument("--train", default=str(Path("data") / "splits" / "train.csv"))
    parser.add_argument("--val", default=str(Path("data") / "splits" / "val.csv"))
    parser.add_argument("--vocab", default=str(Path("data") / "processed" / "vocab.json"))
    parser.add_argument("--embeddings", default=None, help="预训练词向量矩阵 .npy（可选）")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--max-len", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析参数并启动训练。

    Returns:
        进程退出码，0 表示成功。

    TODO(实现):
        1. 用 :func:`build_parser` 解析 ``argv``，命令行未给的项沿用 ``TrainConfig`` 默认值；
        2. 加载词表与数据集（``src.preprocessing``），构造 :func:`trainer.build_dataloader`；
        3. 按 ``--model`` 构造 ``TextCNN`` / ``BiLSTMAttention``（可选载入预训练 embedding）；
        4. 调用 :func:`trainer.train_model`，打印 best 指标与日志/检查点路径。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    print("[骨架] 训练入口尚未实现，收到配置：")
    for key, value in sorted(vars(args).items()):
        print(f"  {key} = {value}")
    print("TODO: 见 src/training/trainer.py:train_model")
    return 0


if __name__ == "__main__":  # pragma: no cover - 手动执行入口
    raise SystemExit(main())
