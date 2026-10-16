"""评估指标骨架。

标签顺序固定为 ``LABELS = ("negative", "positive")``，与 `src/models/base.py`
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


def compute_metrics(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    *,
    average: str = "binary",
) -> dict[str, float]:
    """计算二分类指标。

    Args:
        y_true: 真实标签（``0 = negative``，``1 = positive``）。
        y_pred: 预测标签，与 ``y_true`` 等长。
        average: ``"binary"`` 直接算正类指标；``"macro"`` 算宏平均（两个类一起看）。

    Returns:
        ``{"accuracy": float, "precision": float, "recall": float, "f1": float}``

    TODO(实现):
        - ``accuracy_score``、``precision_score``、``recall_score``、``f1_score``
          （``zero_division=0`` 避免除零告警）；
        - 额外记录宏平均版 ``macro_precision`` 等键，便于报告对比；
        - 断言 ``len(y_true) == len(y_pred)``，不等时抛 ``ValueError``。
    """
    raise NotImplementedError("TODO: 尚未实现指标计算")


def confusion_matrix(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    """计算混淆矩阵。

    Returns:
        ``{"matrix": [[tn, fp], [fn, tp]], "tn": int, "fp": int, "fn": int, "tp": int,
           "labels": ("negative", "positive")}``

    TODO(实现): 用 ``sklearn.metrics.confusion_matrix(labels=[0, 1])``，
    注意返回顺序是行=真实、列=预测，需按 ``LABELS`` 顺序填写。
    """
    raise NotImplementedError("TODO: 尚未实现混淆矩阵")


def error_analysis(
    texts: Sequence[str],
    y_true: Sequence[int],
    y_pred: Sequence[int],
    probs: Sequence[float] | None = None,
    *,
    top_k: int = 50,
) -> list[dict]:
    """挑出预测错误的样本，返回错误明细列表。

    Args:
        texts: 原文。
        y_true / y_pred: 真实与预测标签 id。
        probs: 可选，正类预测概率（用于按「错得离谱」程度排序）。
        top_k: 最多返回多少条。

    Returns:
        ``[{"text": str, "true_label": str, "pred_label": str, "confidence": float}, ...]``
        按置信度从高到低排序（高置信度错误最能说明问题）。

    TODO(实现):
        - 遍历筛选 ``y_true != y_pred`` 的位置；
        - 置信度取预测类概率（若只给正类概率，预测为 positive 时用它，否则用 1-p）；
        - 排序后截断 ``top_k``。
    """
    raise NotImplementedError("TODO: 尚未实现错误分析")


def labels_to_ids(labels: Sequence[str]) -> list[int]:
    """把 ``positive`` / ``negative`` 字符串标签转成 0/1（未知标签抛 ValueError）。"""
    mapping = {name: idx for idx, name in enumerate(LABELS)}
    result: list[int] = []
    for label in labels:
        key = str(label).strip().lower()
        if key not in mapping:
            raise ValueError(f"未知标签 {label!r}，只支持 {LABELS}")
        result.append(mapping[key])
    return result


def ids_to_labels(ids: Sequence[int]) -> list[str]:
    """把 0/1 转回 ``positive`` / ``negative``。"""
    return [LABELS[int(i)] for i in ids]
