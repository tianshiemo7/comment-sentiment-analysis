"""BiLSTM-Attention 情感分类模型骨架。

## 原理要点

1. **词嵌入**：与 TextCNN 相同的 ``Embedding`` 层，可用预训练词向量初始化。
2. **双向 LSTM**：正向 LSTM 读 :math:`x_1 \\to x_L`，反向 LSTM 读 :math:`x_L \\to x_1`，
   每个时刻拼接两侧隐状态 :math:`h_t = [\\overrightarrow{h_t}; \\overleftarrow{h_t}] \\in \\mathbb{R}^{2H}`，
   因此每个词都能同时看到**左侧与右侧**上下文——对「虽然…但是…」这类转折句很关键。
3. **加性注意力**：对每个时刻计算打分

   .. math::
      u_t = \\tanh(W h_t + b), \\qquad a_t = \\frac{\\exp(u_t^\\top u_w)}{\\sum_j \\exp(u_j^\\top u_w)}

   句子表示 :math:`s = \\sum_t a_t h_t`。注意力权重 :math:`a_t` 可直接可视化，
   回答「哪些词对情感判定贡献最大」（验收加分项，见 `src/visualization`）。
4. **分类头**：``Dropout`` + ``Linear(2H → num_classes)``。

## 参数量估算位置

见 :meth:`BiLSTMAttention.estimate_params`，公式（不含偏置）：

- Embedding：``vocab_size * embed_dim``
- 每层每方向 LSTM：``4 * (input_size * H + H * H)``，第 1 层 ``input_size = embed_dim``，
  之后为 ``2H``（双向拼接）
- 注意力：``attention_dim * 2H + attention_dim``（``W`` 与打分向量 ``u_w``）
- 分类头：``num_classes * 2H``
"""

from __future__ import annotations

from typing import Any

from .base import NUM_CLASSES, require_torch


class BiLSTMAttention:
    """BiLSTM-Attention 骨架：``__init__`` 只记录超参，张量构建在 :meth:`build` 内完成。"""

    name = "bilstm_attention"
    model_type = "bilstm_attention"

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        hidden_dim: int = 128,
        num_layers: int = 1,
        num_classes: int = NUM_CLASSES,
        dropout: float = 0.5,
        attention_dim: int = 64,
        bidirectional: bool = True,
        padding_idx: int = 0,
        pretrained_embeddings: Any = None,
        freeze_embeddings: bool = False,
    ) -> None:
        """记录超参数（此阶段不创建任何 torch 张量）。

        Args:
            vocab_size: 词表规模。
            embed_dim: 词向量维度。
            hidden_dim: LSTM 单向隐藏层维度 ``H``（拼接后句子表示为 ``2H``）。
            num_layers: LSTM 层数（≥1）。
            num_classes: 类别数，二分类固定为 2（顺序 ``[negative, positive]``）。
            dropout: dropout 概率。
            attention_dim: 注意力打分空间的维度。
            bidirectional: 是否双向；置 ``False`` 可做消融对比实验。
            padding_idx: 补齐位 id，固定 0。
            pretrained_embeddings: 预训练 embedding 矩阵或 ``None``。
            freeze_embeddings: 是否冻结词向量。
        """
        if num_layers < 1:
            raise ValueError("num_layers 至少为 1")
        self.hyperparams: dict[str, Any] = {
            "vocab_size": int(vocab_size),
            "embed_dim": int(embed_dim),
            "hidden_dim": int(hidden_dim),
            "num_layers": int(num_layers),
            "num_classes": int(num_classes),
            "dropout": float(dropout),
            "attention_dim": int(attention_dim),
            "bidirectional": bool(bidirectional),
            "padding_idx": int(padding_idx),
            "freeze_embeddings": bool(freeze_embeddings),
        }
        self.pretrained_embeddings = pretrained_embeddings
        self.net: Any = None

    @property
    def output_dim(self) -> int:
        """句子表示维度：双向为 ``2H``，单向为 ``H``。"""
        hp = self.hyperparams
        return 2 * hp["hidden_dim"] if hp["bidirectional"] else hp["hidden_dim"]

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        hp = self.hyperparams
        return (
            f"BiLSTMAttention(vocab_size={hp['vocab_size']}, embed_dim={hp['embed_dim']}, "
            f"hidden_dim={hp['hidden_dim']}, num_layers={hp['num_layers']}, "
            f"bidirectional={hp['bidirectional']})"
        )

    def build(self) -> Any:
        """构建网络并返回 ``torch.nn.Module``。

        TODO(实现):
            1. ``torch = require_torch()``，``import torch.nn as nn``；
            2. ``nn.Embedding``（同 TextCNN，含 ``padding_idx`` 与预训练初始化）；
            3. ``nn.LSTM(embed_dim, hidden_dim, num_layers, batch_first=True,
               bidirectional=..., dropout=...)``；
            4. 注意力：``nn.Linear(output_dim, attention_dim)`` + 可学习打分向量
               ``nn.Parameter(torch.randn(attention_dim))``；
            5. 分类头：``nn.Dropout`` + ``nn.Linear(output_dim, num_classes)``。
        """
        raise NotImplementedError("TODO: 尚未实现 BiLSTM-Attention 网络搭建")

    def forward(self, x: Any, attention_mask: Any = None):
        """前向传播。

        Args:
            x: ``LongTensor``，形状 ``[batch, max_len]``。
            attention_mask: 可选 ``[batch, max_len]`` 的 0/1 掩码，``<pad>`` 位为 0；
                传入时应把对应位置的注意力打分置为 ``-inf``（或用极小值）

        Returns:
            ``(logits, attn_weights)``：logits ``[batch, num_classes]``，
            attn_weights ``[batch, max_len]``（每行和为 1，供可视化）。

        TODO(实现):
            1. ``self.net`` 为空时先 :meth:`build`；
            2. LSTM 取 ``output, (h_n, c_n) = lstm(emb)``；
            3. 按 :meth:`build` 的注意力公式算 ``u_t`` → ``a_t``（应用 mask）；
            4. 加权求和得句子表示 → dropout → 全连接 → 返回 ``(logits, attn_weights)``。
        """
        raise NotImplementedError("TODO: 尚未实现 BiLSTM-Attention 前向传播")

    def estimate_params(self) -> dict[str, int]:
        """估算参数量（不含偏置项），返回分项与总数。

        Returns:
            ``{"embedding": int, "lstm": int, "attention": int, "classifier": int, "total": int}``
        """
        hp = self.hyperparams
        hidden = hp["hidden_dim"]
        num_directions = 2 if hp["bidirectional"] else 1

        embedding = hp["vocab_size"] * hp["embed_dim"]
        lstm = 0
        for layer in range(hp["num_layers"]):
            input_size = hp["embed_dim"] if layer == 0 else num_directions * hidden
            lstm += num_directions * 4 * (input_size * hidden + hidden * hidden)

        output_dim = self.output_dim
        attention = hp["attention_dim"] * output_dim + hp["attention_dim"]
        classifier = hp["num_classes"] * output_dim

        return {
            "embedding": embedding,
            "lstm": lstm,
            "attention": attention,
            "classifier": classifier,
            "total": embedding + lstm + attention + classifier,
        }
