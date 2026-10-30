# models/ — 模型权重存放目录

本目录用于存放**训练产物**。权重文件默认被 `.gitignore` 排除（`*.pt` / `*.bin` 等），
2026-10-30 随提交 `1eb77a7` 以「一次性快照」方式强制入库（总体约定见 [`../data/README.md`](../data/README.md)）。

| 子目录 | 内容 | 命名建议 |
| --- | --- | --- |
| `checkpoints/` | 训练检查点 | `textcnn/best.pt`、`bilstm_attention/best.pt` |
| `word2vec/` | 词向量与词表对齐矩阵 | `word2vec.model`、`embedding_matrix.npy`、`word2vec_params.json` |
| `bert/` | BERT 微调权重与 tokenizer | `bert_base_chinese/` |

## 约定

- 每个模型目录保存 `config.json` 和 `metadata.json`；正式训练另保存 `experiment.json`，
  以及 `preprocessing/` 内的词表、资源配置、标签映射及 manifest 快照。
- `GET /api/models` 的指标来源为 `outputs/stats/metrics_<模型>.json`；检查点存在只说明权重可用，
  是否真正加载成功由 `src/inference/service.py:load_model` 的返回值决定（见 `src/inference/README.md`）。
- 复现训练请在报告中记录：数据集版本、随机种子（`SEED`）、超参、对应权重文件名。
- 如需把权重入库，请使用 Git LFS 并在本文件说明；当前快照为普通提交（两个模型权重各约数 MB）。

`bert/` 目前只有目录占位，没有权重。

2026-10-30 本机已完成两模型正式训练（Word2Vec + TextCNN + BiLSTM-Attention），
权重、配套预处理资源与实验记录见 [模型训练与评估](../docs/模型训练与评估-2026-10-30.md)；
`outputs/stats/metrics_<模型>.json` 为对应测试集指标。
