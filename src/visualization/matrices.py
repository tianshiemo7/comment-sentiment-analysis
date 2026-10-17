"""热力图：混淆矩阵、Attention 权重（加分项）。"""

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

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _save(fig, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    plt.close(fig)
    return path


def _square(matrix: Sequence[Sequence[int]]) -> list[list[float]]:
    rows = [[float(cell) for cell in row] for row in matrix]
    if not rows or any(len(row) != len(rows) for row in rows):
        raise ValueError("混淆矩阵必须是方阵")
    return rows


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
        matrix: 2×2 矩阵 ``[[TN, FP], [FN, TP]]``（`src.evaluation.metrics.confusion_matrix`）。
        out_path: 输出路径；多模型对比时按 ``confusion_matrix_<model>.png`` 命名。
        labels: 类别名，与矩阵行列顺序一致（行 = 真实，列 = 预测）。
        normalize: 是否按行归一化（显示比例，便于对比）。
    """
    values = _square(matrix)
    if len(labels) != len(values):
        raise ValueError("类别名数量必须与混淆矩阵维度一致")
    if normalize:
        display = []
        for row in values:
            total = sum(row)
            display.append([(cell / total if total else 0.0) for cell in row])
    else:
        # seaborn 的 fmt="d" 只接受整数，这里把 float 计数转回 int
        display = [[int(round(cell)) for cell in row] for row in values]

    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(6, 5))
    try:
        import seaborn as sns  # noqa: PLC0415 - 可选依赖

        sns.heatmap(
            display,
            annot=True,
            fmt=".2f" if normalize else "d",
            cmap="Blues",
            xticklabels=list(labels),
            yticklabels=list(labels),
            ax=ax,
        )
    except ImportError:  # 没有 seaborn 时退回 imshow + 手动标注
        image = ax.imshow(display, cmap="Blues")
        fig.colorbar(image, ax=ax)
        for row_index, row in enumerate(display):
            for column_index, cell in enumerate(row):
                text = f"{cell:.2f}" if normalize else f"{int(round(cell))}"
                ax.text(column_index, row_index, text, ha="center", va="center", color="black")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(list(labels))
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(list(labels))
    ax.set_xlabel("预测标签")
    ax.set_ylabel("真实标签")
    ax.set_title(title)
    return _save(fig, out_path)


def plot_attention_heatmap(
    tokens: Sequence[str],
    weights: Sequence[float],
    out_path: str | Path = FIGURES_DIR / "attention_heatmap.png",
    *,
    title: str = "Attention 权重分布",
    top_k: int | None = 30,
) -> Path:
    """绘制单条样本的 Attention 权重（哪些词对情感判定贡献最大）。

    Args:
        tokens: 分词结果（与权重一一对应，需已剔除 ``<pad>``）。
        weights: 归一化注意力权重（和约为 1）。
        top_k: 只显示权重最高的前 k 个词，None 表示全部显示。
    """
    words = [str(token) for token in tokens]
    values = [float(weight) for weight in weights]
    if len(words) != len(values):
        raise ValueError("tokens 与 weights 长度必须一致")
    if not words:
        raise ValueError("没有可绘制的 Attention 数据")
    pairs = sorted(zip(values, words), key=lambda item: item[0], reverse=True)
    if top_k is not None and top_k >= 0:
        pairs = pairs[:top_k]
    pairs = list(reversed(pairs))

    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(8, max(3.0, 0.28 * len(pairs) + 1.5)))
    ax.barh([word for _weight, word in pairs], [weight for weight, _word in pairs], color="#4C72B0")
    ax.set_xlabel("Attention 权重")
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.3)
    return _save(fig, out_path)
