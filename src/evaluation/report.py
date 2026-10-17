"""评估结果落盘：指标 JSON、模型对比表、错误样例。

输出目录见 `outputs/README.md`；`src/inference` 的 `/api/models` 会读取
`outputs/stats/metrics_<model>.json` 作为「真实指标」来源。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

STATS_DIR = Path("outputs") / "stats"
PREDICTIONS_DIR = Path("outputs") / "predictions"

TABLE_FIELDS = ("model", "accuracy", "precision", "recall", "f1", "notes")
METRIC_KEYS = ("accuracy", "precision", "recall", "f1")
METRIC_LABELS = {"accuracy": "Accuracy", "precision": "Precision", "recall": "Recall", "f1": "F1"}
ERROR_FIELDS = ("text", "true_label", "pred_label", "confidence")


def _format_metric(value) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return ""


def save_metrics_json(metrics: dict, model_name: str, out_dir: str | Path = STATS_DIR) -> Path:
    """把单个模型的指标写成 ``metrics_<model>.json``，返回路径。

    文件结构 ``{"model": name, "metrics": {...}, "updated_at": iso}``，
    `src.inference.service.available_models` 会读取其中的 ``metrics`` 字段。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model_name,
        "metrics": metrics,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = out_dir / f"metrics_{model_name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_metrics_table(
    rows: Iterable[dict],
    out_path: str | Path = STATS_DIR / "model_comparison.csv",
) -> Path:
    """把多模型指标写成对比表 CSV（表头 TABLE_FIELDS），返回路径。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TABLE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            record = {field: row.get(field, "") for field in TABLE_FIELDS}
            for key in METRIC_KEYS:
                record[key] = _format_metric(record[key]) if record[key] != "" else ""
            writer.writerow(record)
    return out_path


def compare_models(results: dict[str, dict]) -> list[dict]:
    """把 ``{model_name: metrics}`` 汇总为对比表行，并在 ``notes`` 标注各指标最优模型。

    Args:
        results: ``{"textcnn": {"accuracy": .., "precision": .., ...}, ...}``

    Returns:
        行列表，每行含 TABLE_FIELDS 的键。
    """
    if not results:
        return []
    best: dict[str, float] = {key: float("-inf") for key in METRIC_KEYS}
    for metrics in results.values():
        for key in METRIC_KEYS:
            try:
                best[key] = max(best[key], float(metrics.get(key, float("-inf"))))
            except (TypeError, ValueError):
                continue
    rows: list[dict] = []
    for model_name, metrics in results.items():
        notes = [
            METRIC_LABELS[key]
            for key in METRIC_KEYS
            if _is_best(metrics.get(key), best[key])
        ]
        rows.append(
            {
                "model": model_name,
                **{key: metrics.get(key, "") for key in METRIC_KEYS},
                "notes": ("、".join(notes) + " 最优") if notes else "",
            }
        )
    rows.sort(key=lambda row: _sort_value(row.get("f1")), reverse=True)
    return rows


def _sort_value(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _is_best(value, best_value) -> bool:
    try:
        return abs(float(value) - float(best_value)) < 1e-12
    except (TypeError, ValueError):
        return False


def save_error_cases(
    cases: list[dict],
    model_name: str,
    out_dir: str | Path = PREDICTIONS_DIR,
) -> Path:
    """把错误分析明细写成 ``error_cases_<model>.csv``，返回路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"error_cases_{model_name}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ERROR_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for case in cases:
            writer.writerow({field: case.get(field, "") for field in ERROR_FIELDS})
    return path
