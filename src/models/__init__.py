"""④ 模型模块：TextCNN 与 BiLSTM-Attention（BERT 微调预留）。

本包**不在导入期引入 torch**：模型类只记录超参数，构建网络张量的逻辑放在
`build` / `forward` 内部，未安装 torch 时给出明确的安装提示。
"""

from .base import SentimentModel, load_checkpoint, save_checkpoint
from .bilstm_attention import BiLSTMAttention
from .cnn import TextCNN

__all__ = [
    "SentimentModel",
    "TextCNN",
    "BiLSTMAttention",
    "save_checkpoint",
    "load_checkpoint",
]
