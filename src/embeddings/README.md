# src/embeddings —— ③ 词向量模块

> 对应需求：训练 Word2Vec（或加载 GloVe），输出到 `models/word2vec/`，
> 记录维度、窗口大小、最小词频等参数。路线图阶段二。

## 职责

1. 在分词后的语料上训练 Word2Vec（默认 `sg=1` Skip-gram，`vector_size=128`，`window=5`，`min_count=3`）；
2. 或加载预训练 GloVe / 腾讯词向量，做维度对齐；
3. 把词向量按词表顺序整理成 ``[vocab_size, embed_dim]`` 矩阵，供 `src/models` 初始化 Embedding 层；
4. OOV 词向量用均值向量或小随机数初始化（需在报告中说明）。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 分词后的语料（TokenList） | `data/processed/` |
| 输入 | 词表（词 → id） | `data/processed/vocab.json` |
| 输出 | 词向量模型 `word2vec.model`（含参数说明） | `models/word2vec/` |
| 输出 | 词向量矩阵 `embedding_matrix.npy` | `models/word2vec/` |
| 输出 | 训练参数记录 `word2vec_params.json` | `models/word2vec/` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `word2vec.py` | Word2Vec 训练 / 加载 / 相似词评估 |
| `matrix.py` | 词表对齐的 embedding 矩阵构造与保存 |

## 实现状态（已完成）

- [x] `word2vec.py:train_word2vec` —— gensim Word2Vec 训练并保存模型 + `word2vec_params.json`（超参 / 语料规模 / 词表规模）
- [x] `word2vec.py:load_word2vec` / `evaluate_word2vec` —— 加载与相似词自检（OOV 跳过并记录）
- [x] `matrix.py:build_embedding_matrix` —— 按词表 id 对齐，OOV 支持 `mean` / `zeros` / `random`，`<pad>` 行恒为零
- [x] `matrix.py:save_embedding_matrix` / `load_embedding_matrix` —— `.npy` 读写
- [x] `src/embeddings/__main__.py` —— 命令行入口，一次产出词向量与 embedding 矩阵

```bash
python -m src.embeddings --train-file data/splits/train.csv \
  --vocab data/processed/vocab.json --out models/word2vec --embed-dim 128
# 训练时用 --embeddings models/word2vec/embedding_matrix.npy 初始化 Embedding 层
```

## 关联

- 路线图：`docs/开发路线图.md` 阶段二
- 上游：`src/preprocessing`；下游：`src/models`、`src/training`
- 一键脚本：`scripts/train.sh`