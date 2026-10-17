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

from .base import NUM_CLASSES, TorchModelMixin, require_torch


class BiLSTMAttention(TorchModelMixin):
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
        """构建网络并返回 ``torch.nn.Module``（重复调用返回同一实例）。"""
        if self.net is not None:
            return self.net
        torch = require_torch()
        import torch.nn as nn

        hp = self.hyperparams
        pretrained = self.pretrained_embeddings
        output_dim = self.output_dim
        lstm_dropout = hp["dropout"] if hp["num_layers"] > 1 else 0.0

        class _BiLSTMAttentionModule(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.embedding = nn.Embedding(
                    hp["vocab_size"], hp["embed_dim"], padding_idx=hp["padding_idx"]
                )
                if pretrained is not None:
                    weight = torch.as_tensor(pretrained, dtype=torch.float32)
                    expected = (hp["vocab_size"], hp["embed_dim"])
                    if tuple(weight.shape) != expected:
                        raise ValueError(
                            f"预训练词向量矩阵形状 {tuple(weight.shape)} 与模型期望 {expected} 不一致"
                        )
                    with torch.no_grad():
                        self.embedding.weight.copy_(weight)
                if hp["freeze_embeddings"]:
                    self.embedding.weight.requires_grad = False
                self.lstm = nn.LSTM(
                    hp["embed_dim"],
                    hp["hidden_dim"],
                    num_layers=hp["num_layers"],
                    batch_first=True,
                    bidirectional=hp["bidirectional"],
                    dropout=lstm_dropout,
                )
                self.attention_proj = nn.Linear(output_dim, hp["attention_dim"])
                self.attention_vector = nn.Parameter(torch.randn(hp["attention_dim"]))
                self.dropout = nn.Dropout(hp["dropout"])
                self.classifier = nn.Linear(output_dim, hp["num_classes"])
                self.padding_idx = int(hp["padding_idx"])

            def forward(self, x, attention_mask=None):
                if attention_mask is None:
                    mask = (x != self.padding_idx).to(torch.float32)
                else:
                    mask = attention_mask.to(torch.float32)
                embeddings = self.embedding(x) * mask.unsqueeze(-1)
                outputs, _ = self.lstm(embeddings)
                projected = torch.tanh(self.attention_proj(outputs))
                scores = torch.matmul(projected, self.attention_vector)
                scores = scores.masked_fill(mask == 0, -1e9)
                weights = torch.softmax(scores, dim=1)
                sentence = torch.bmm(weights.unsqueeze(1), outputs).squeeze(1)
                logits = self.classifier(self.dropout(sentence))
                return logits, weights

        self.net = _BiLSTMAttentionModule()
        return self.net

    def forward(self, x: Any, attention_mask: Any = None):
        """前向传播。

        Args:
            x: ``LongTensor``，形状 ``[batch, max_len]``。
            attention_mask: 可选 ``[batch, max_len]`` 的 0/1 掩码；传入时 ``<pad>``
                位置的注意力打分被置为极小值，权重为 0。

        Returns:
            ``(logits, attn_weights)``：logits ``[batch, num_classes]``，
            attn_weights ``[batch, max_len]``（每行和为 1，供可视化）。
        """
        net = self.net
        if net is None:
            net = self.build()
        return net(x, attention_mask=attention_mask)

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