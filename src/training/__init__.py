"""⑤ 训练模块：统一训练/验证循环、早停、检查点与日志。

入口：`config.py`（超参）、`trainer.py`（训练循环）、`cli.py`（命令行）。
训练相关代码需要 torch，均在函数内部导入。
"""

from .config import TrainConfig
from .trainer import EarlyStopping, set_seed, train_model

__all__ = ["TrainConfig", "train_model", "EarlyStopping", "set_seed"]
