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


class TorchModelMixin:
    """让「只记录超参的模型包装类」像一个轻量 nn.Module 使用。

    骨架设计里 __init__ 不创建张量，真正的网络在 build 里构建并赋给 self.net。
    本混入类补齐训练/推理侧真正要用的几个转发方法，使模型对象可按普通 nn.Module 调用。
    """

    #: 由 build 赋值；未构建时为 None
    net: Any = None

    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)

    def module(self):
        """返回底层 nn.Module，必要时先 build。"""
        net = getattr(self, "net", None)
        if net is None:
            net = self.build()
        return net

    def train(self, mode: bool = True):
        self.module().train(mode)
        return self

    def eval(self):
        return self.train(False)

    def to(self, device):
        self.module().to(device)
        return self

    def parameters(self):
        return self.module().parameters()

    def state_dict(self):
        return self.module().state_dict()

    def load_state_dict(self, state_dict, strict: bool = True):
        return self.module().load_state_dict(state_dict, strict=strict)


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

    Returns:
        写入的检查点文件路径（`.pt`）。同目录下另写一份 `config.json` 与
        `metadata.json`，不依赖 torch 也能查看超参和最优指标。
    """
    import json  # noqa: PLC0415 - 仅保存时需要

    torch = require_torch()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state, path)

    # 便于人工查看与报告引用：超参与指标单独落一份纯文本（不含张量）
    config = state.get("config") or {}
    metadata = {
        "model_name": state.get("model_name"),
        "model_type": state.get("model_type"),
        "epoch": state.get("epoch"),
        "metrics": state.get("metrics") or {},
        "hyperparams": state.get("hyperparams") or {},
        "labels": list(LABELS),
    }
    (path.parent / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (path.parent / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_checkpoint(path: str | Path, map_location: str = "cpu") -> dict:
    """加载训练检查点。

    Raises:
        FileNotFoundError: 检查点不存在（提示目录约定与训练命令）。
    """
    torch = require_torch()
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"检查点不存在：{path}。约定路径为 models/checkpoints/<model_name>/best.pt，"
            "请先运行 `python -m src.training.cli --model <model_name>` 或 `bash scripts/train.sh`。"
        )
    return torch.load(path, map_location=map_location, weights_only=False)
