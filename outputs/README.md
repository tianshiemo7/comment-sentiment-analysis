# outputs/ — 产物输出目录

2026-10-30 随提交 `1eb77a7`，本目录的产物以「一次性快照」方式强制入库（`.gitignore` 规则未改），
便于课程验收与换机备份。**唯一刻意排除的是 `outputs/settings.json`（含 B 站 Cookie 明文，绝不入库）**。

| 子目录 | 已入库内容 | 对应课程要求 |
| --- | --- | --- |
| `figures/` | 10 张 PNG：`loss_curve.png` / `accuracy_curve.png`（最新一次训练）、`loss_curve_<模型>.png` / `accuracy_curve_<模型>.png`、`confusion_matrix_<模型>.png`、`wordcloud_positive.png` / `wordcloud_negative.png` | 结果可视化四项要求 |
| `logs/` | `crawl_BV1tseyzCEoF.log` + 5 份 `train_<模型>_<时间>.json`（每轮 loss / acc / 学习率） | 实验分析 |
| `predictions/` | `error_cases_textcnn.csv`、`error_cases_bilstm_attention.csv` | 错误分析 |
| `stats/` | `dataset_stats.json`（冒烟样例 24 条）、`comments_stats.json`（clean 模式 38 条）、`metrics_<模型>.json`、`model_comparison.csv` | 预处理统计完整性 |
| `videos/` | `index.json` + `BV1GJ411x7h7.json`（含 `crawl` / `clean` / `analyze` 三阶段落盘）+ `BV1xx411c7mD.json`（占位演示） | 主流程状态与结果持久化 |
| `realdata/` | **真实规模闭环**：18,644 条语料 → 清洗 18,613 条（负 9,305 / 正 9,308）→ 划分 train 13,029 / val 1,861 / test 3,723（CSV + NPZ）+ 词表 9,308 词，约 30 MB | 数据规模与划分达标 |
| `benchmarks/` | `final2_live.json`（真实联网 4 请求 / 9.3 s / 风控 0）、`final2_offline.json`（离线 mock 基准） | 爬虫效率实测 |

> ⚠️ **口径说明**：`figures/`、`logs/`、`stats/`、`predictions/` 与 `models/checkpoints/` 里的检查点
> 都来自**小样本冒烟**（27 行演示数据 → 24 条有效，train/val/test = 16/4/4，`max_len=16`，1 epoch，
> 两个模型 val_acc=0.5），只能证明链路跑通，**不能写进实验结论**。
> `realdata/` 保存的是真实规模的数据侧结果，**没有对应的训练权重、图表与指标**。

## 复现

```bash
# ① 冒烟（演示样例，只验证链路；产物即当前提交里的这一份）
python -m src.preprocessing --input data/sample/bilibili_demo.csv --allow-small-dataset
bash scripts/train.sh

# ② 真实规模（18,644 条公开语料；重跑预处理会覆盖 data/processed、data/splits）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv --overwrite
WITH_WORD2VEC=1 bash scripts/train.sh
```

数据统计由 `python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv` 生成，
`dataset_stats.json` 包含总体与分集的标签分布、词元长度、截断率、训练词表 OOV 率、排除原因与课程检查结果。
数据不足时命令默认失败；显式 `--allow-small-dataset` 的输出仅用于调试，不代表语料或实验已经达标。
