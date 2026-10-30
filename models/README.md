# models/ — 模型权重存放目录

本目录用于存放**训练产物**。权重文件体积较大，已通过 `.gitignore` 排除，仅保留目录结构。

| 子目录 | 内容 | 命名建议 |
| --- | --- | --- |
| `checkpoints/` | 训练检查点 | `textcnn/best.pt`、`bilstm_attention/best.pt` |
| `word2vec/` | 词向量与词表对齐矩阵 | `word2vec.model`、`embedding_matrix.npy`、`word2vec_params.json` |
| `bert/` | BERT 微调权重与 tokenizer | `bert_base_chinese/` |

## 约定

- 每个模型目录保存 `config.json` 和 `metadata.json`；本次另保存 `experiment.json`，
  以及 `preprocessing/` 内的词表、资源配置、标签映射及 manifest 快照。
- `GET /api/models` 的指标来源为 `outputs/stats/metrics_<模型>.json`；检查点存在不代表真实推理入口已经接通。
- 复现训练请在报告中记录：数据集版本、随机种子（`SEED`）、超参、对应权重文件名。
- 如需把权重入库，请使用 Git LFS 并在本文件说明。

2026-10-30 本机已完成两模型训练，权重和资源均在上述目录。
环境、命令、数据版本、指标和哈希见 [模型训练与评估](../docs/模型训练与评估-2026-10-30.md)。
