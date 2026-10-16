"""热力图骨架：混淆矩阵、Attention 权重（加分项）。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

FIGURES_DIR = Path("outputs") / "figures"


def _setup_matplotlib():
    """导入并配置 matplotlib（中文字体 + 无界面后端）。"""
    try:
        import matplotlib  # noqa: PLC0415 - 函数内导入，避免导入期依赖

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "绘图需要 matplotlib，请执行：python -m pip install matplotlib"
        ) from exc

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def plot_confusion_matrix(
    matrix: Sequence[Sequence[int]],
    out_path: str | Path = FIGURES_DIR / "confusion_matrix.png",
    *,
    labels: Sequence[str] = ("negative", "positive"),
    normalize: bool = False,
    title: str = "混淆矩阵",
) -> Path:
    """绘制混淆矩阵热力图并保存 PNG，返回保存路径。

    Args:
        matrix: 2×2 矩阵 ``[[TN, FP], [FN, TP]]``（``src.evaluation.metrics.confusion_matrix``）。
        out_path: 输出路径，默认 ``outputs/figures/confusion_matrix.png``
            （多模型对比时按 ``confusion_matrix_<model>.png`` 命名）。
        labels: 类别名，与矩阵行列顺序一致。
        normalize: 是否按行归一化（显示比例，便于对比）。
        title: 图标题。

    TODO(实现):
        1. :func:`_setup_matplotlib`；seaborn 可用则 ``sns.heatmap(annot=True, fmt="d"/".2f", cmap="Blues")``，
           不可用则退回 ``plt.imshow`` + 手动 ``plt.text`` 标注；
        2. 设置 xlabel「预测标签」、ylabel「真实标签」、xticks/yticks 为 ``labels``；
        3. 保存到 ``out_path`` 后关闭图像。
    """
    raise NotImplementedError("TODO: 尚未实现混淆矩阵热力图")


def plot_attention_heatmap(
    tokens: Sequence[str],
    weights: Sequence[float],
    out_path: str | Path = FIGURES_DIR / "attention_heatmap.png",
    *,
    title: str = "Attention 权重分布",
    top_k: int | None = 30,
) -> Path:
    """绘制单条样本的 Attention 权重（加分项：哪些词对情感判定贡献最大）。

    Args:
        tokens: 分词结果（与权重一一对应，需已剔除 ``<pad>``）。
        weights: 归一化注意力权重（和约为 1）。
        out_path: 输出路径，默认 ``outputs/figures/attention_heatmap.png``。
        title: 图标题。
        top_k: 只显示权重最高的前 k 个词，``None`` 表示全部显示。

    TODO(实现):
        1. 按权重降序取前 ``top_k`` 个词；
        2. 画横向条形图（``barh``），权重越大条越长，中文词需设置字体；
        3. 保存并关闭。报告中配合典型样例（反讽、转折句）分析。
    """
    raise NotImplementedError("TODO: 尚未实现 Attention 权重可视化")
