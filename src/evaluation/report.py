"""评估结果落盘骨架：指标 JSON、模型对比表、错误样例。

输出目录见 `outputs/README.md`；`src/inference` 的 `/api/models` 会读取
`outputs/stats/metrics_<model>.json` 作为「真实指标」来源。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

STATS_DIR = Path("outputs") / "stats"
PREDICTIONS_DIR = Path("outputs") / "predictions"

TABLE_FIELDS = ("model", "accuracy", "precision", "recall", "f1", "notes")


def save_metrics_json(metrics: dict, model_name: str, out_dir: str | Path = STATS_DIR) -> Path:
    """把单个模型的指标写成 ``metrics_<model>.json``，返回路径。

    TODO(实现): 自动创建目录；``json.dumps(..., ensure_ascii=False, indent=2)``；
    文件结构建议 ``{"model": name, "metrics": {...}, "updated_at": iso}``。
    """
    raise NotImplementedError("TODO: 尚未实现指标 JSON 落盘")


def save_metrics_table(
    rows: Iterable[dict],
    out_path: str | Path = STATS_DIR / "model_comparison.csv",
) -> Path:
    """把多模型指标写成对比表 CSV（表头 :data:`TABLE_FIELDS`），返回路径。

    TODO(实现): 标准库 ``csv.DictWriter``，编码 ``utf-8-sig``；缺失字段留空。
    """
    raise NotImplementedError("TODO: 尚未实现对比表落盘")


def compare_models(results: dict[str, dict]) -> list[dict]:
    """把 ``{model_name: metrics}`` 汇总为对比表行并标记最优列。

    Args:
        results: ``{"textcnn": {"accuracy": .., "precision": .., ...}, ...}``

    Returns:
        行列表，每行含 :data:`TABLE_FIELDS` 的键；``notes`` 记录「F1 最优」等标记。

    TODO(实现):
        - 遍历所有模型与 ``METRIC_KEYS``，找出每列最大值对应的模型；
        - 在 ``notes`` 中标注该模型在哪些指标上最优（报告中对比表的加粗依据）。
    """
    raise NotImplementedError("TODO: 尚未实现多模型对比汇总")


def save_error_cases(
    cases: list[dict],
    model_name: str,
    out_dir: str | Path = PREDICTIONS_DIR,
) -> Path:
    """把错误分析明细写成 ``error_cases_<model>.csv``，返回路径。

    TODO(实现): 表头 ``text,true_label,pred_label,confidence``；编码 ``utf-8-sig``。
    """
    raise NotImplementedError("TODO: 尚未实现错误样例落盘")
