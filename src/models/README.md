# src/models —— ④ 模型模块

> 对应需求：两种深度模型搭建、训练与对比（报告第 5 章须阐述原理）。
> 路线图阶段三。

## 职责

| 模型 | 文件 | 特点 | 角色 |
| --- | --- | --- | --- |
| TextCNN | `cnn.py` | 多尺寸卷积核提取 n-gram 特征，训练快 | 基线 |
| BiLSTM-Attention | `bilstm_attention.py` | 双向上下文 + 注意力权重，可做 Attention 可视化 | 主模型 / 加分项 |
| BERT 微调（可选） | 预留，建议新建 `bert.py` | 效果最好、成本最高 | 加分项 |

公共约定：

- 词表 id 约定与 `src/preprocessing/vocab.py` 一致（0 = `<pad>`，1 = `<unk>`）；
- 分类头输出 2 类 logits（顺序 `[negative, positive]`），与 `src/inference` 的标签顺序保持一致；
- Embedding 层可用 `src/embeddings/matrix.py` 的预训练矩阵初始化，`padding_idx=0`。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 定长 id 序列 `[batch, max_len]` | `src/preprocessing/tokenize.encode_ids` |
| 输入 | 预训练 embedding 矩阵（可选） | `models/word2vec/embedding_matrix.npy` |
| 输出 | logits `[batch, 2]`；BiLSTM-Attention 另可返回注意力权重 `[batch, max_len]` | 训练循环 |
| 输出 | 检查点 `best.pt` / `last.pt` | `models/checkpoints/<model_name>/` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `base.py` | 模型协议、检查点保存/加载 |
| `cnn.py` | `TextCNN` 类骨架（超参记录 + 参数量估算） |
| `bilstm_attention.py` | `BiLSTMAttention` 类骨架（含注意力说明） |

## 实现状态（已完成）

- [x] `cnn.py:TextCNN.build` / `forward` —— 多尺寸 Conv1d + max-over-time + dropout + 全连接，按 `<pad>` 掩码过滤补齐位置
- [x] `bilstm_attention.py:BiLSTMAttention.build` / `forward` —— Embedding + 双向 LSTM + 加性注意力（返回 `(logits, attn_weights)`，`<pad>` 权重为 0）
- [x] `base.py:save_checkpoint` / `load_checkpoint` —— `torch.save` / `torch.load`，同目录附 `config.json` / `metadata.json`
- [x] `base.py:TorchModelMixin` —— 让两个包装类支持 `model(x)` / `train()` / `eval()` / `state_dict()` / `load_state_dict()`

快速自检：

```bash
python -m pytest tests/test_models.py -q
```

## 关联

- 路线图：`docs/开发路线图.md` 阶段三
- 下游：`src/training`、`src/evaluation`、`src/visualization`、`src/inference`
- 报告：`docs/课程设计报告大纲.md` 第 5 章