# models/ — 模型权重存放目录

本目录用于存放**训练产物**。权重文件默认被 `.gitignore` 排除（`*.pt` / `*.bin` 等），
2026-10-30 随提交 `1eb77a7` 以「一次性快照」方式强制入库（总体约定见 [`../data/README.md`](../data/README.md)）。

> ⚠️ **当前入库的是小样本冒烟训练结果**：训练集只有 16 条（`data/splits/train.csv`）、
> `max_len=16`、`epochs=3`，保存的最佳轮次是 **1 epoch**，两个模型的验证准确率都是 **0.5**，
> 只证明「训练 → 早停 → 检查点 → 评估 → 出图」链路能跑通，**不能作为课程实验指标**。
> 正式训练请用真实语料：先按根 README 第四节用 `data/raw/comments_labeled_balanced.csv` 重跑预处理，
> 再执行 `bash scripts/train.sh`（可加 `WITH_WORD2VEC=1`）。

## 已入库的文件

| 文件 | 内容 |
| --- | --- |
| `checkpoints/textcnn/best.pt` | TextCNN 最佳检查点（约 4.7 KB） |
| `checkpoints/textcnn/config.json` / `metadata.json` | 该次训练的超参与最佳轮指标（`epoch=1`、`val_acc=0.5`、`val_loss≈0.776`） |
| `checkpoints/bilstm_attention/best.pt` | BiLSTM-Attention 最佳检查点（约 8.3 KB） |
| `checkpoints/bilstm_attention/config.json` / `metadata.json` | 超参与指标（`epoch=1`、`val_acc=0.5`、`val_loss≈0.696`） |
| `word2vec/word2vec.model` | gensim Word2Vec 模型（`vector_size=128`、`window=5`、`sg=1`、`epochs=3`） |
| `word2vec/embedding_matrix.npy` | 与词表对齐的 embedding 矩阵（冒烟样例，词表仅 4 个词） |
| `word2vec/word2vec_params.json` | 词向量参数与语料统计（`corpus_sentences=16`、`corpus_tokens=102`、`vocab_size=4`） |

`bert/` 目前只有目录占位，没有权重。

## 约定

- 模型元信息（超参、训练轮次、指标、标签顺序）随权重一起保存为 `config.json` + `metadata.json`，供 `GET /api/models` 读取；
- 复现训练请在报告中记录：数据集版本、随机种子（`SEED=42`）、超参、对应权重文件名；
- 权重若要长期入库，建议改用 Git LFS（当前这份冒烟检查点是普通提交入库的，体积很小）。
