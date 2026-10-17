"""评估指标：Accuracy / Precision / Recall / F1、混淆矩阵与错误分析。

标签顺序固定为 LABELS = ("negative", "positive")，与 `src/models/base.py`
和 `src/inference/service.py` 保持一致，不得调换。
"""

from __future__ import annotations

from typing import Sequence

#: 类别顺序（与模型输出 logits 顺序一致）
LABELS = ("negative", "positive")
METRIC_KEYS = ("accuracy", "precision", "recall", "f1")


def _require_sklearn():
    """导入 sklearn 的 metrics 模块；缺失时抛出带安装提示的 ImportError。"""
    try:
        from sklearn import metrics  # noqa: PLC0415 - 函数内导入，避免导入期依赖
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "评估指标需要 scikit-learn，请执行：python -m pip install scikit-learn"
        ) from exc
    return metrics


def _validate_pair(y_true: Sequence[int], y_pred: Sequence[int]) -> None:
    if len(y_true) != len(y_pred):
        raise ValueError(f"真实标签数 {len(y_true)} 与预测标签数 {len(y_pred)} 不一致")
    if not len(y_true):
        raise ValueError("评估集为空，无法计算指标")


def compute_metrics(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    *,
    average: str = "binary",
) -> dict[str, float]:
    """计算二分类指标。

    Args:
        y_true: 真实标签（0 = negative，1 = positive）。
        y_pred: 预测标签，与 y_true 等长。
        average: "binary" 直接算正类指标；"macro" 算宏平均（两个类一起看）。

    Returns:
        正类指标 accuracy / precision / recall / f1，外加 macro_* 宏平均版，便于报告对比。
    """
    _validate_pair(y_true, y_pred)
    if average not in {"binary", "macro"}:
        raise ValueError("average 只支持 binary 或 macro")
    metrics = _require_sklearn()
    result = {
        "accuracy": float(metrics.accuracy_score(y_true, y_pred)),
        "precision": float(metrics.precision_score(y_true, y_pred, average=average, zero_division=0)),
        "recall": float(metrics.recall_score(y_true, y_pred, average=average, zero_division=0)),
        "f1": float(metrics.f1_score(y_true, y_pred, average=average, zero_division=0)),
    }
    # 报告里同时给出宏平均，便于观察类别平衡时两种口径的差异
    result.update({
        "macro_precision": float(metrics.precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_recall": float(metrics.recall_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_f1": float(metrics.f1_score(y_true, y_pred, average="macro", zero_division=0)),
    })
    return result


def confusion_matrix(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    """计算混淆矩阵。

    Returns:
        ``{"matrix": [[tn, fp], [fn, tp]], "tn": int, "fp": int, "fn": int, "tp": int,
        "labels": ("negative", "positive")}``（行 = 真实，列 = 预测）。
    """
    _validate_pair(y_true, y_pred)
    metrics = _require_sklearn()
    matrix = metrics.confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = (int(matrix[0][0]), int(matrix[0][1]), int(matrix[1][0]), int(matrix[1][1]))
    return {
        "matrix": [[tn, fp], [fn, tp]],
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
        "labels": LABELS,
    }


def error_analysis(
    texts: Sequence[str],
    y_true: Sequence[int],
    y_pred: Sequence[int],
    probs: Sequence[float] | None = None,
    *,
    top_k: int = 50,
) -> list[dict]:
    """挑出预测错误的样本，按置信度从高到低排序（高置信度错误最能说明问题）。

    Args:
        texts: 原文。
        y_true / y_pred: 真实与预测标签 id。
        probs: 可选，正类预测概率；预测为 positive 时取它，否则取 1-p。
        top_k: 最多返回多少条。

    Returns:
        ``[{"text", "true_label", "pred_label", "confidence"}, ...]``。
    """
    _validate_pair(y_true, y_pred)
    if len(texts) != len(y_true):
        raise ValueError(f"文本数 {len(texts)} 与标签数 {len(y_true)} 不一致")
    if probs is not None and len(probs) != len(y_true):
        raise ValueError(f"概率数 {len(probs)} 与标签数 {len(y_true)} 不一致")
    rows: list[dict] = []
    for index, (text, true_label, pred_label) in enumerate(zip(texts, y_true, y_pred)):
        if int(true_label) == int(pred_label):
            continue
        if probs is None:
            confidence = 1.0
        else:
            probability = float(probs[index])
            confidence = probability if int(pred_label) == 1 else 1.0 - probability
        rows.append(
            {
                "text": str(text),
                "true_label": LABELS[int(true_label)],
                "pred_label": LABELS[int(pred_label)],
                "confidence": round(confidence, 6),
            }
        )
    rows.sort(key=lambda row: row["confidence"], reverse=True)
    if top_k is not None and top_k >= 0:
        rows = rows[:top_k]
    return rows


def labels_to_ids(labels: Sequence[str]) -> list[int]:
    """把 positive / negative 字符串标签转成 0/1（未知标签抛 ValueError）。"""
    mapping = {name: idx for idx, name in enumerate(LABELS)}
    result: list[int] = []
    for label in labels:
        key = str(label).strip().lower()
        if key not in mapping:
            raise ValueError(f"未知标签 {label!r}，只支持 {LABELS}")
        result.append(mapping[key])
    return result


def ids_to_labels(ids: Sequence[int]) -> list[str]:
    """把 0/1 转回 positive / negative。"""
    return [LABELS[int(i)] for i in ids]
