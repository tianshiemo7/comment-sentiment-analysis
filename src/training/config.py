"""训练超参数容器。

默认值与 `.env.example` 对齐，命令行可覆盖（见 `cli.py`）。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


LOG_DIR = Path("outputs") / "logs"


@dataclass
class TrainConfig:
    """训练配置。

    Attributes:
        model_name: ``textcnn`` / ``bilstm_attention``。
        train_path / val_path: 训练与验证集路径。
        max_len: 序列统一长度，默认取环境变量 ``MAX_LEN``（200）。
        batch_size: 批大小，默认 ``BATCH_SIZE``（64）。
        epochs: 最大训练轮数，默认 ``EPOCHS``（20）。
        learning_rate: 学习率，默认 ``LEARNING_RATE``（1e-3）。
        weight_decay: 权重衰减（Adam L2 正则）。
        patience: 早停耐心值（验证集连续多少轮不提升就停止）。
        min_delta: 视为「有提升」的最小变化量。
        seed: 随机种子，默认 ``SEED``（42）。
        device: ``"auto"`` / ``"cpu"`` / ``"cuda"``。
        grad_clip: 梯度裁剪阈值，``0`` 表示不裁剪。
        log_every: 每多少 step 打印一次训练日志。
        num_workers: DataLoader 工作进程数。
        log_dir: 训练日志目录。
        checkpoint_dir: 检查点目录。
    """

    model_name: str = "textcnn"
    train_path: Path = Path("data") / "splits" / "train.csv"
    val_path: Path = Path("data") / "splits" / "val.csv"
    max_len: int = field(default_factory=lambda: _env_int("MAX_LEN", 200))
    batch_size: int = field(default_factory=lambda: _env_int("BATCH_SIZE", 64))
    epochs: int = field(default_factory=lambda: _env_int("EPOCHS", 20))
    learning_rate: float = field(default_factory=lambda: _env_float("LEARNING_RATE", 1e-3))
    weight_decay: float = 0.0
    patience: int = 3
    min_delta: float = 1e-4
    seed: int = field(default_factory=lambda: _env_int("SEED", 42))
    device: str = "auto"
    grad_clip: float = 5.0
    log_every: int = 50
    num_workers: int = 0
    log_dir: Path = LOG_DIR
    checkpoint_dir: Path = Path("models") / "checkpoints"

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的字典（Path → str），用于写入日志。"""
        payload = asdict(self)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in payload.items()}

    def validate(self) -> None:
        """参数合法性检查（在学习率、批大小等非法时尽早报错）。"""
        if self.batch_size < 1:
            raise ValueError("batch_size 必须 ≥ 1")
        if self.epochs < 1:
            raise ValueError("epochs 必须 ≥ 1")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate 必须 > 0")
        if self.max_len < 1:
            raise ValueError("max_len 必须 ≥ 1")
        if self.patience < 1:
            raise ValueError("patience 必须 ≥ 1")
