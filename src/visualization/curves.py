"""训练曲线骨架：损失 / 准确率（训练 vs 验证）。

输入为 `src/training/trainer.train_model` 返回的 ``history``：
``[{"epoch": 1, "train_loss": .., "val_loss": .., "val_acc": .., ...}, ...]``
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

FIGURES_DIR = Path("outputs") / "figures"


def _setup_matplotlib():
    """导入并配置 matplotlib（中文字体 + 无界面后端）。

    返回 ``(plt, rc_params_ok)``；未安装 matplotlib 时抛出带安装提示的 ImportError。
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
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def plot_loss_curve(
    history: Sequence[dict],
    out_path: str | Path = FIGURES_DIR / "loss_curve.png",
    *,
    title: str = "训练 / 验证损失曲线",
) -> Path:
    """绘制损失曲线并保存 PNG，返回保存路径。

    Args:
        history: 每轮训练记录，需含 ``epoch`` / ``train_loss`` / ``val_loss``。
        out_path: 输出路径，默认 ``outputs/figures/loss_curve.png``。
        title: 图标题。

    TODO(实现):
        1. :func:`_setup_matplotlib`；
        2. 以 ``epoch`` 为横轴画 ``train_loss`` 与 ``val_loss`` 两条线（带 marker 与图例）；
        3. ``xlabel("Epoch")`` / ``ylabel("Loss")`` / ``grid(alpha=0.3)``；
        4. 自动创建父目录，``savefig(dpi=150, bbox_inches="tight")`` 后 ``close()``。
    """
    raise NotImplementedError("TODO: 尚未实现损失曲线绘制")


def plot_accuracy_curve(
    history: Sequence[dict],
    out_path: str | Path = FIGURES_DIR / "accuracy_curve.png",
    *,
    title: str = "训练 / 验证准确率曲线",
) -> Path:
    """绘制准确率曲线并保存 PNG，返回保存路径。

    TODO(实现): 与 :func:`plot_loss_curve` 同构；
    训练集准确率键为 ``train_acc``（trainer 需记录），验证集为 ``val_acc``；
    若历史中缺少 ``train_acc`` 则只画验证集并在图例中说明。
    """
    raise NotImplementedError("TODO: 尚未实现准确率曲线绘制")


def load_history(log_path: str | Path) -> list[dict]:
    """读取训练日志 JSON 中的 ``history`` 字段。

    TODO(实现): ``json.loads`` 后取 ``payload["history"]``；键不存在时返回空列表。
    """
    raise NotImplementedError("TODO: 尚未实现训练日志读取")
