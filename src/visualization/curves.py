"""训练曲线：损失 / 准确率（训练 vs 验证）。

输入为 `src/training/trainer.train_model` 返回的 ``history``：
``[{"epoch": 1, "train_loss": .., "val_loss": .., "val_acc": .., ...}, ...]``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

FIGURES_DIR = Path("outputs") / "figures"


def _setup_matplotlib():
    """导入并配置 matplotlib（中文字体 + 无界面后端）。

    返回 ``plt``；未安装 matplotlib 时抛出带安装提示的 ImportError。
    """
    try:
        import matplotlib  # noqa: PLC0415 - 函数内导入，避免导入期依赖

        matplotlib.use("Agg")  # 无 GUI 环境（服务器/CI）也能出图
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "绘图需要 matplotlib，请执行：python -m pip install matplotlib"
        ) from exc

    # 中文标签不乱码
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _validated_history(history: Sequence[dict]) -> list[dict]:
    rows = [dict(row) for row in history or [] if isinstance(row, dict)]
    if not rows:
        raise ValueError("训练历史为空，无法绘图")
    return rows


def _epochs(rows: Sequence[dict]) -> list[int]:
    return [int(row.get("epoch", index + 1)) for index, row in enumerate(rows)]


def _save(fig, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    plt.close(fig)
    return path


def plot_loss_curve(
    history: Sequence[dict],
    out_path: str | Path = FIGURES_DIR / "loss_curve.png",
    *,
    title: str = "训练 / 验证损失曲线",
) -> Path:
    """绘制损失曲线并保存 PNG，返回保存路径。"""
    rows = _validated_history(history)
    plt = _setup_matplotlib()
    epochs = _epochs(rows)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(epochs, [row.get("train_loss") for row in rows], marker="o", label="训练集")
    ax.plot(epochs, [row.get("val_loss") for row in rows], marker="s", label="验证集")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Loss")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend()
    return _save(fig, out_path)


def plot_accuracy_curve(
    history: Sequence[dict],
    out_path: str | Path = FIGURES_DIR / "accuracy_curve.png",
    *,
    title: str = "训练 / 验证准确率曲线",
) -> Path:
    """绘制准确率曲线并保存 PNG；缺少 train_acc 时只画验证集。"""
    rows = _validated_history(history)
    plt = _setup_matplotlib()
    epochs = _epochs(rows)
    fig, ax = plt.subplots(figsize=(8, 5))
    has_train = any(row.get("train_acc") is not None for row in rows)
    if has_train:
        ax.plot(epochs, [row.get("train_acc") for row in rows], marker="o", label="训练集")
    else:
        ax.plot([], [], marker="o", label="训练集（本日志未记录）")
    ax.plot(epochs, [row.get("val_acc") for row in rows], marker="s", label="验证集")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Accuracy")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend()
    return _save(fig, out_path)


def load_history(log_path: str | Path) -> list[dict]:
    """读取训练日志 JSON 中的 ``history`` 字段；缺失或损坏时返回空列表。"""
    path = Path(log_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    history = payload.get("history") if isinstance(payload, dict) else None
    return list(history) if isinstance(history, list) else []
