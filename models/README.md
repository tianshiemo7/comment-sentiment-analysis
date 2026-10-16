# models/ — 模型权重存放目录

本目录用于存放**训练产物**。权重文件体积较大，已通过 `.gitignore` 排除，仅保留目录结构。

| 子目录 | 内容 | 命名建议 |
| --- | --- | --- |
| `checkpoints/` | 训练检查点（按 epoch / best 保存） | `cnn_best.pt`、`bilstm_attention_best.pt` |
| `word2vec/` | 词向量文件 | `word2vec_100d.bin`、`glove_100d.txt` |
| `bert/` | BERT 微调权重与 tokenizer | `bert_base_chinese/` |

## 约定

- 模型元信息（超参、训练轮次、指标）随权重一起保存为同名 `.json`，例如
  `checkpoints/bilstm_attention_best.json`，供 `GET /api/models` 读取。
- 复现训练请在报告中记录：数据集版本、随机种子（`SEED`）、超参、对应权重文件名。
- 如需把权重入库，请使用 Git LFS 并在本文件说明。
