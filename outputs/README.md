# outputs/ — 产物输出目录

> 📦 **大体积 / 运行期评论产物不入库（2026-11-03 起）**：本目录中 `realdata/`（约 30 MB 的旧数据版本闭环产物）
> 与 `videos/` 下的运行期任务 JSON 已用 `git rm --cached` 移出版本库——**只删 Git 索引、本地文件保留**，
> GitHub 仓库页面不再包含这些文件。2026-10-30 它们曾随「一次性快照」（提交 `1eb77a7`）强制入库；
> 本次同时核对 `.gitignore`（`outputs/realdata/*`、`outputs/videos/*` 等）保证不会再被误跟踪。
>
> 仍入库的是小体积、报告验收需要的产物：`figures/`（10 张 PNG）、`logs/`、`stats/`、`predictions/`、
> `benchmarks/`，以及各级 `.gitkeep` 与本 README。**唯一始终排除的是 `outputs/settings.json`
> （含 B 站 Cookie 明文，绝不入库）**。

| 子目录 | 本地内容 | 是否入库 | 对应课程要求 |
| --- | --- | --- | --- |
| `figures/` | 10 张 PNG：`loss_curve.png` / `accuracy_curve.png`（当前 shopping-v1 训练）、`loss_curve_<模型>.png` / `accuracy_curve_<模型>.png`、`confusion_matrix_<模型>.png`、`wordcloud_positive.png` / `wordcloud_negative.png` | 是 | 结果可视化四项要求 |
| `logs/` | 2026-10-17 / 10-30 历史训练日志（`train_*_202610{17,30}_*.json`、`train_*_run.log`）、`prepare_training_data.log`、`preprocessing_bundle_check.log`、`verify_checkpoints.log`、`word2vec.log`、`crawl_BV1tseyzCEoF.log`、`t1_loader_compat.log`。当前 shopping-v1 的训练日志随实验目录（`outputs/experiments/shopping-v1/`，本地、不入库）保存 | 是（历史对照） | 实验分析 |
| `predictions/` | `error_cases_textcnn.csv`、`error_cases_bilstm_attention.csv`（shopping-v1 真实模型测试集错误样例，每模型置信度最高的最多 50 条）；运行期批量导出 CSV 不入库 | 是 | 错误分析 |
| `stats/` | `dataset_stats.json`（**shopping-v1 真实规模**：62,622 条、词表 20,069）、`metrics_<模型>.json`（当前测试指标）、`model_comparison.csv`；历史文件 `training_data_preparation.json`、`training_experiment_20261030.json`、`comments_stats.json`、`checkpoint_smoke.json`、`t1_loader_compat.json` 保留作对照 | 是 | 预处理统计与模型指标 |
| `videos/` | 运行期落盘：`index.json` + `<BV>.json` + `<BV>/crawl.json` / `clean.json` / `analyze.json`（视频分析任务）；2026-10-17 联调历史产物已移出版本库，本地保留 | 否（仅 `.gitkeep` 入库） | 主流程状态与结果持久化 |
| `realdata/` | **历史数据侧闭环**（旧数据版本，18,644 条 → 清洗 18,613 条（负 9,305 / 正 9,308）→ 划分 train 13,029 / val 1,861 / test 3,723（CSV + NPZ）+ 词表 9,308 词，约 30 MB；**未保存对应训练权重、图表与指标**），数据版本 `6740e89ff5121749`、pipeline 1.0.0 | 否（全部移出版本库） | 数据规模与划分达标（历史口径） |
| `benchmarks/` | `final2_live.json`（真实联网 4 请求 / 9.3 s / 风控 0）、`final2_offline.json`（离线 mock 基准） | 是 | 爬虫效率实测 |

> ⚠️ **口径说明（2026-11-03 更新）**：
> - **当前部署与图表/指标为 shopping-v1（2026-11-02 电商实验）**：`online_shopping_10_cats` 清洗后
>   62,622 条（负面 30,991 / 正面 31,631），train 43,836 / val 6,262 / test 12,524，数据版本
>   `dd18f284bd13662c`；TextCNN 测试 Acc **92.95%** / F1 93.02%，BiLSTM-Attention **93.67%** / F1 93.73%。
>   `figures/`、`stats/dataset_stats.json`、`stats/metrics_<模型>.json`、`stats/model_comparison.csv`、
>   `predictions/error_cases_*.csv` 与 `models/checkpoints/` 权重一致；详见
>   [`docs/电商模型接入与验收-2026-11-02.md`](../docs/电商模型接入与验收-2026-11-02.md)。
> - `logs/train_*_2026{1017,1030}_*.json`、`stats/training_experiment_20261030.json`、
>   `stats/checkpoint_smoke.json`、`stats/t1_loader_compat.json` 属 **2026-10-17 / 10-30 历史实验**
>   （酒店 / 外卖语料，数据版本 `753bc2014c69bdc1`；本机 CPU 复核 TextCNN 89.60% / BiLSTM-Attention 90.08%，
>   GPU 历史实验 89.09% / 89.25%），只作对照，**不要与当前指标混用**。
> - `realdata/` 是更早的旧数据版本（18,644 条）的数据侧结果，不能与当前 `data/processed/`、`data/splits/`
>   （19,711 → 13,797 / 1,971 / 3,943，历史实验口径）混用。
> - 训练语料为电商 / 酒店外卖公开语料，**没有 B 站人工标注测试集**，上述指标不能当作 B 站评论准确率。

## 复现

```bash
# ① 当前 shopping-v1（电商）实验：原始语料 data/raw/datasets/online_shopping_10_cats.csv（本地生成，
#    见 data/raw/README.md），复现输入、词向量、检查点与训练日志保存在
#    outputs/experiments/shopping-v1/（本地、不入库）；步骤与哈希见 docs/电商模型接入与验收-2026-11-02.md。
#    部署用权重与预处理包已入库：models/checkpoints/<模型>/。

# ② 历史实验（酒店 / 外卖，数据版本 753bc2014c69bdc1；同时重建 data/processed、data/splits、outputs/stats）
python -m scripts.prepare_training_data

python -m src.embeddings --train-file data/splits/train.csv --vocab data/processed/vocab.json \
  --out models/word2vec --embed-dim 128 --window 5 --min-count 3 --epochs 10 --workers 1 --seed 42
python -m src.training.cli --model textcnn --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
python -m src.training.cli --model bilstm_attention --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
# 或一把梭（缺产物时先预处理，可选词向量）：
WITH_WORD2VEC=1 bash scripts/train.sh

# ③ 历史数据侧闭环 realdata/（旧数据版本 18,644 条；需先按 data/raw/README.md 重新生成
#    data/raw/comments_labeled_balanced.csv；重跑会覆盖 outputs/realdata/，慎用）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv \
  --output-root outputs/realdata --overwrite
```

数据统计由预处理命令生成（`outputs/stats/dataset_stats.json` 含总体与分集的标签分布、
词元长度、截断率、训练词表 OOV 率、排除原因与课程检查结果）。数据不足时命令默认失败；
显式 `--allow-small-dataset` 的输出仅用于调试，不代表语料或实验已经达标。
视频任务落盘（`outputs/videos/`）由界面「视频分析」/ `POST /api/videos` 运行时产生，说明见
[`../src/videos/README.md`](../src/videos/README.md)。
