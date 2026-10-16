"""数据统计骨架。

对应需求：输出 `outputs/stats/dataset_stats.json`，包含
**词表规模、平均文本长度、标签分布**（报告第 4 章必须给出）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

STATS_PATH = Path("outputs") / "stats" / "dataset_stats.json"


def label_distribution(labels: Iterable[str]) -> dict[str, int]:
    """统计标签分布。

    TODO(实现): 用 ``collections.Counter`` 统计，键为 ``label`` 取值，值为条数。
    """
    raise NotImplementedError("TODO: 尚未实现标签分布统计")


def length_stats(tokenized_texts: Sequence[Sequence[str]]) -> dict[str, float]:
    """统计 token 长度分布。

    Returns:
        ``{"count": int, "mean": float, "median": float, "p90": float, "max": int}``

    TODO(实现): 用 ``statistics`` 或 ``numpy`` 计算；空语料返回全 0。
    """
    raise NotImplementedError("TODO: 尚未实现长度统计")


def dataset_stats(
    tokenized_texts: Sequence[Sequence[str]],
    labels: Sequence[str] | None = None,
    vocab=None,
    *,
    max_len: int | None = None,
    out_path: str | Path = STATS_PATH,
) -> dict:
    """汇总统计并写入 JSON。

    Args:
        tokenized_texts: 分词后的语料。
        labels: 与语料一一对应的标签（测试集可能没有标签，可传 ``None``）。
        vocab: :class:`~src.preprocessing.vocab.Vocab`，用于汇报词表规模。
        max_len: 统一长度，用于报告截断比例（超长样本占比）。
        out_path: 输出 JSON 路径，默认 ``outputs/stats/dataset_stats.json``。

    Returns:
        统计字典，结构示例::

            {
              "total": 10000,
              "vocab_size": 15234,
              "min_freq": 3,
              "max_len": 200,
              "length": {"mean": 32.1, "p90": 60, "max": 210},
              "labels": {"positive": 5010, "negative": 4990},
              "truncated_ratio": 0.012
            }

    TODO(实现): 组合上面两个统计函数 + 词表规模 + 截断比例，写 JSON（``ensure_ascii=False``、``indent=2``）。
    """
    raise NotImplementedError("TODO: 尚未实现统计汇总与落盘")


def load_stats(path: str | Path = STATS_PATH) -> dict:
    """读取统计 JSON；文件不存在时返回空字典。"""
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
