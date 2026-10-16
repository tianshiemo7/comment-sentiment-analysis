"""TextCNN 情感分类模型骨架（Kim, 2014 风格）。

## 原理要点

1. **词嵌入**：`Embedding(vocab_size, embed_dim, padding_idx=0)`，可用预训练词向量初始化
   （见 `src/embeddings/matrix.py`）；`<pad>` 行不参与梯度更新。
2. **多尺寸一维卷积**：对 ``[B, L, E]`` 的序列，用宽度为 :math:`k`（如 2/3/4）的卷积核
   在词方向滑动，每个窗口覆盖 :math:`k` 个连续词，等价于提取 **k-gram 特征**。
   每种尺寸取 ``num_filters`` 个通道，得到 ``[B, num_filters, L-k+1]``。
3. **最大池化**：对每个通道在时间维取 max（``max-over-time``），把变长文本压成
   ``num_filters`` 维定长向量，同时抓住「最强的那个 n-gram 信号」——
   这正是短文本情感分类中 CNN 表现好的原因（关键词/短语触发情感）。
4. **Dropout + 全连接**：拼多尺寸特征后 dropout 正则，接 ``Linear(→ num_classes)`` 输出 logits。

## 参数量估算位置

见 :meth:`TextCNN.estimate_params`，公式（不含偏置）：

- Embedding：``vocab_size * embed_dim``
- 每种尺寸 k 的卷积核：``num_filters * embed_dim * k``
- 分类头：``num_classes * num_filters * len(filter_sizes)``
"""

from __future__ import annotations

from typing import Any, Sequence

from .base import NUM_CLASSES, require_torch


class TextCNN:
    """TextCNN 骨架：``__init__`` 只记录超参，张量构建在 :meth:`build` 内完成。"""

    name = "textcnn"
    model_type = "textcnn"

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        num_filters: int = 100,
        filter_sizes: Sequence[int] = (2, 3, 4),
        num_classes: int = NUM_CLASSES,
        dropout: float = 0.5,
        padding_idx: int = 0,
        pretrained_embeddings: Any = None,
        freeze_embeddings: bool = False,
    ) -> None:
        """记录超参数（此阶段不创建任何 torch 张量）。

        Args:
            vocab_size: 词表规模（`len(vocab)`）。
            embed_dim: 词向量维度。
            num_filters: 每种卷积核尺寸的通道数。
            filter_sizes: 卷积核宽度列表，默认 ``(2, 3, 4)`` 即 2/3/4-gram。
            num_classes: 类别数，二分类固定为 2（顺序 ``[negative, positive]``）。
            dropout: dropout 概率。
            padding_idx: 补齐位 id，固定 0（与词表约定一致）。
            pretrained_embeddings: 预训练 embedding 矩阵或 ``None``。
            freeze_embeddings: 是否冻结词向量（微调实验可对比）。
        """
        self.hyperparams: dict[str, Any] = {
            "vocab_size": int(vocab_size),
            "embed_dim": int(embed_dim),
            "num_filters": int(num_filters),
            "filter_sizes": tuple(int(k) for k in filter_sizes),
            "num_classes": int(num_classes),
            "dropout": float(dropout),
            "padding_idx": int(padding_idx),
            "freeze_embeddings": bool(freeze_embeddings),
        }
        self.pretrained_embeddings = pretrained_embeddings
        #: 真实实现在 build() 里赋值，例如 ``self.net = nn.Sequential(...)``
        self.net: Any = None

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        hp = self.hyperparams
        return (
            f"TextCNN(vocab_size={hp['vocab_size']}, embed_dim={hp['embed_dim']}, "
            f"num_filters={hp['num_filters']}, filter_sizes={hp['filter_sizes']})"
        )

    def build(self) -> Any:
        """构建网络并返回 ``torch.nn.Module``。

        TODO(实现):
            1. ``torch = require_torch()``，``import torch.nn as nn``；
            2. ``nn.Embedding(vocab_size, embed_dim, padding_idx=padding_idx)``，
               若 ``pretrained_embeddings`` 非空则 ``weight.data.copy_`` 初始化，
               ``freeze_embeddings`` 为真时置 ``requires_grad=False``；
            3. 每种 ``k in filter_sizes``：``nn.Conv1d(embed_dim, num_filters, k)``；
            4. ``nn.Dropout`` + ``nn.Linear(num_filters * len(filter_sizes), num_classes)``；
            5. 用 :class:`torch.nn.Module` 子类或 ``nn.Module`` 容器封装，赋值给 ``self.net`` 并返回。
        """
        raise NotImplementedError("TODO: 尚未实现 TextCNN 网络搭建")

    def forward(self, x: Any):
        """前向传播。

        Args:
            x: ``LongTensor``，形状 ``[batch, max_len]``（补齐后的 id 序列）。

        Returns:
            logits ``[batch, num_classes]``（顺序 ``[negative, positive]``）。

        TODO(实现):
            1. ``self.net`` 为空时先调用 :meth:`build`；
            2. ``emb = embedding(x)`` → ``[B, L, E]``，转置为 ``[B, E, L]`` 供 Conv1d 使用；
            3. 每个卷积核 ``conv(emb)`` → ``relu`` → ``max(dim=2)`` 取 max-over-time；
            4. 拼接各尺寸特征 → dropout → 全连接 → 返回 logits。
        """
        raise NotImplementedError("TODO: 尚未实现 TextCNN 前向传播")

    def estimate_params(self) -> dict[str, int]:
        """估算参数量（不含偏置项），返回分项与总数。

        Returns:
            ``{"embedding": int, "conv": int, "classifier": int, "total": int}``
        """
        hp = self.hyperparams
        embedding = hp["vocab_size"] * hp["embed_dim"]
        conv = sum(hp["num_filters"] * hp["embed_dim"] * k for k in hp["filter_sizes"])
        classifier = hp["num_classes"] * hp["num_filters"] * len(hp["filter_sizes"])
        return {
            "embedding": embedding,
            "conv": conv,
            "classifier": classifier,
            "total": embedding + conv + classifier,
        }
