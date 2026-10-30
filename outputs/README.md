# outputs/ — 产物输出目录

2026-10-30 随提交 `1eb77a7`，本目录的产物以「一次性快照」方式强制入库（`.gitignore` 规则未改），
便于课程验收与换机备份。**唯一刻意排除的是 `outputs/settings.json`（含 B 站 Cookie 明文，绝不入库）**。
本轮交付把 `figures` / `logs` / `stats` / `predictions` 回填为**真实模型的训练与评估产物**
（小样本冒烟产物已被替换，仅保留 2026-10-17 的历史日志作对照）。

| 子目录 | 已入库内容 | 对应课程要求 |
| --- | --- | --- |
| `figures/` | 10 张 PNG：`loss_curve.png` / `accuracy_curve.png`（最新一次训练）、`loss_curve_<模型>.png` / `accuracy_curve_<模型>.png`、`confusion_matrix_<模型>.png`、`wordcloud_positive.png` / `wordcloud_negative.png` | 结果可视化四项要求 |
| `logs/` | `train_textcnn_20261030_145201.json`、`train_bilstm_attention_20261030_145328.json`（真实训练逐轮 loss / acc / lr）；`prepare_training_data.log`、`preprocessing_bundle_check.log`、`verify_checkpoints.log`、`word2vec.log`、`crawl_BV1tseyzCEoF.log`、`t1_loader_compat.log`；另有 2026-10-17 小样本冒烟训练日志（历史对照） | 实验分析 |
| `predictions/` | `error_cases_textcnn.csv`、`error_cases_bilstm_attention.csv`（真实模型、每模型置信度最高的最多 50 个错误样例） | 错误分析 |
| `stats/` | `dataset_stats.json`（**真实规模**：19,711 条、词表 9,753）、`metrics_<模型>.json`（真实测试集指标）、`model_comparison.csv`、`training_data_preparation.json`、`training_experiment_20261030.json`（数据 / 环境 / 源码 / 权重 / 图片哈希关联）、`comments_stats.json`（clean 模式 38 条，爬虫下游交接）、`checkpoint_smoke.json`、`t1_loader_compat.json` | 预处理统计与模型指标 |
| `videos/` | `index.json` + `BV1GJ411x7h7.json`（含 `crawl` / `clean` / `analyze` 三阶段落盘，2026-10-17 联调的历史产物）+ `BV1xx411c7mD.json`（占位演示） | 主流程状态与结果持久化 |
| `realdata/` | **历史数据侧闭环**：18,644 条旧语料 → 清洗 18,613 条（负 9,305 / 正 9,308）→ 划分 train 13,029 / val 1,861 / test 3,723（CSV + NPZ）+ 词表 9,308 词，约 30 MB；**未保存对应训练权重、图表与指标** | 数据规模与划分达标（历史口径） |
| `benchmarks/` | `final2_live.json`（真实联网 4 请求 / 9.3 s / 风控 0）、`final2_offline.json`（离线 mock 基准） | 爬虫效率实测 |

> ⚠️ **口径说明（2026-10-30 更新）**：
> - `figures/`、`logs/`、`stats/metrics_<模型>.json`、`predictions/` 与 `models/checkpoints/` 里的权重
>   现在是**真实模型产物**：本机 CPU 复核重训（数据版本 `753bc2014c69bdc1`），
>   TextCNN 测试 Acc **89.60%** / F1 88.76%，BiLSTM-Attention **90.08%** / F1 89.20%
>   （混淆矩阵、权重 SHA256 见 `docs/模型训练与评估-2026-10-30.md`）；
> - GPU（RTX 4060）历史实验（TextCNN 89.09% / BiLSTM 89.25%，第 4 轮）只保留在训练文档中作为对照，
>   对应当前仓库权重的是 CPU 复核那一份；
> - `realdata/` 是**旧数据版本**（18,644 条）的数据侧结果，不能与当前 `data/processed/`、`data/splits/`
>   （19,711 → 13,797 / 1,971 / 3,943）混用；
> - 训练语料为酒店 / 外卖公开语料，**没有 B 站人工标注测试集**，上述指标不能当作 B 站评论准确率。

## 复现

```bash
# ① 真实训练数据（固定公开语料，数据版本 753bc2014c69bdc1）
python -m scripts.prepare_training_data

# ② 词向量 + 两个模型（CPU 复核；GPU 环境把 --device 换成 cuda）
python -m src.embeddings --train-file data/splits/train.csv --vocab data/processed/vocab.json \
  --out models/word2vec --embed-dim 128 --window 5 --min-count 3 --epochs 10 --workers 1 --seed 42
python -m src.training.cli --model textcnn --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
python -m src.training.cli --model bilstm_attention --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
# 或一把梭（缺产物时先预处理，可选词向量）：
WITH_WORD2VEC=1 bash scripts/train.sh

# ③ 历史数据侧闭环（18,644 条旧语料；重跑会覆盖 data/processed、data/splits，慎用）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv
```

数据统计由 `python -m src.preprocessing` 生成，`dataset_stats.json` 包含总体与分集的标签分布、
词元长度、截断率、训练词表 OOV 率、排除原因与课程检查结果。数据不足时命令默认失败；
显式 `--allow-small-dataset` 的输出仅用于调试，不代表语料或实验已经达标。
视频任务落盘（`outputs/videos/`）由 `POST /api/videos` 运行时产生，说明见
[`../src/videos/README.md`](../src/videos/README.md)。
