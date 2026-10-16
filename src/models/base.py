"""模型公共协议与检查点工具（torch 延迟导入）。

设计原则：**模块顶层不导入 torch**，保证无 torch 环境也能 `import src.models`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

#: 类别顺序（必须与 src/inference/service.py 的标签顺序一致，不得调换）
LABELS = ("negative", "positive")
NUM_CLASSES = len(LABELS)

CHECKPOINT_DIR = Path("models") / "checkpoints"


@runtime_checkable
class SentimentModel(Protocol):
    """情感分类模型协议：两个模型类都实现这两个方法即可被训练循环复用。"""

    def forward(self, x):  # pragma: no cover - 协议声明
        """输入 ``x``，返回 logits 或 ``(logits, attn_weights)``。"""
        ...

    def estimate_params(self) -> dict[str, int]:  # pragma: no cover - 协议声明
        """返回参数量估算字典（``total`` 为总数）。"""
        ...


def require_torch() -> Any:
    """导入并返回 ``torch`` 模块；缺失时抛出带安装提示的 ImportError。

    所有需要 torch 的函数都应先调用本函数，这样：
    - 骨架阶段无 torch 也能 import 本包；
    - 真正训练时给出可执行的安装命令，而不是裸 ImportError。
    """
    try:
        import torch  # noqa: PLC0415 - 故意放在函数内，避免导入期依赖
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "未检测到 PyTorch，无法构建/训练模型。安装方式（CPU 版）：\n"
            "    python -m pip install torch --index-url "
            "https://download.pytorch.org/whl/cpu\n"
            "（CUDA 版把 cpu 换成 cu121 等对应版本）"
        ) from exc
    return torch


def checkpoint_path(model_name: str, filename: str = "best.pt") -> Path:
    """返回检查点路径 ``models/checkpoints/<model_name>/<filename>``。"""
    return CHECKPOINT_DIR / model_name / filename


def save_checkpoint(state: dict, path: str | Path) -> Path:
    """保存训练检查点（模型权重 + 词表 + 超参），返回写入路径。

    Args:
        state: 至少包含 ``model_state`` / ``config`` / ``vocab``；建议同时存
            ``epoch`` / ``optimizer_state`` / ``metrics`` 以便断点续训。

    TODO(实现):
        1. ``torch = require_torch()``；
        2. ``torch.save(state, path)``，自动创建父目录；
        3. 同时在同目录写一份 ``config.json``（``ensure_ascii=False``）便于人工查看。
    """
    raise NotImplementedError("TODO: 尚未实现检查点保存")


def load_checkpoint(path: str | Path, map_location: str = "cpu") -> dict:
    """加载训练检查点。

    TODO(实现): ``torch.load(path, map_location=map_location, weights_only=False)``；
    文件不存在抛 ``FileNotFoundError`` 并提示检查点目录约定。
    """
    raise NotImplementedError("TODO: 尚未实现检查点加载")
