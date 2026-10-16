"""数据集划分骨架。

对应需求：训练 : 验证 : 测试 ≈ 7 : 1 : 2，固定随机种子，划分方式写入报告。
"""

from __future__ import annotations

import os
from pathlib import Path

from .dataset import Record

#: 与 .env.example 保持一致
DEFAULT_SEED = int(os.getenv("SEED", "42"))
DEFAULT_RATIOS = (0.7, 0.1, 0.2)

SPLITS_DIR = Path("data") / "splits"
SPLIT_FILES = {"train": "train.csv", "val": "val.csv", "test": "test.csv"}


def split_dataset(
    records: list[Record],
    ratios: tuple[float, float, float] = DEFAULT_RATIOS,
    *,
    seed: int = DEFAULT_SEED,
    stratify: bool = True,
) -> dict[str, list[Record]]:
    """把记录划分为 train / val / test。

    Args:
        records: :class:`~src.preprocessing.dataset.Record` 列表。
        ratios: ``(train, val, test)`` 比例，默认 ``(0.7, 0.1, 0.2)``。
        seed: 随机种子，默认 ``42``（保证可复现）。
        stratify: 是否按 ``label`` 分层抽样，保持正负比例一致。

    Returns:
        ``{"train": [...], "val": [...], "test": [...]}``

    TODO(实现):
        - 校验 ``ratios`` 之和为 1、均非负；
        - ``random.Random(seed).shuffle`` 或 ``sklearn.model_selection.train_test_split``
          （``stratify=labels``）实现；sklearn 不可用时退化为 ``random`` 洗牌切分；
        - 测试集条数按四舍五入取整，保证三份合计等于总数。
    """
    raise NotImplementedError("TODO: 尚未实现数据集划分")


def split_dataset_to_files(
    records: list[Record],
    *,
    ratios: tuple[float, float, float] = DEFAULT_RATIOS,
    seed: int = DEFAULT_SEED,
    stratify: bool = True,
    out_dir: str | Path = SPLITS_DIR,
) -> dict[str, Path]:
    """划分并落盘到 ``data/splits/{train,val,test}.csv``，返回各文件路径。

    TODO(实现): 调用 :func:`split_dataset` 后逐个 ``dataset.save_records``。
    """
    raise NotImplementedError("TODO: 尚未实现划分落盘")
