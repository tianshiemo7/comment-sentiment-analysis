# data/splits/ — 训练 / 验证 / 测试划分

本目录存放按 **训练 : 验证 : 测试 ≈ 7 : 1 : 2** 划分后的数据，由 `src/preprocessing` 的 train 模式生成。
这些文件被 `.gitignore` 排除，2026-10-30 随提交 `1eb77a7` 以「一次性快照」方式强制入库
（概述见 [`../README.md`](../README.md)）。

> ⚠️ **当前目录是小样本冒烟划分**：由 `data/sample/bilibili_demo.csv`（27 行演示数据，24 条有效）划分，
> **train 16 / val 4 / test 4**，只用于验证训练链路。真实规模的划分（18,613 条清洗结果 →
> **train 13,029 / val 1,861 / test 3,723**，CSV + NPZ 齐全）保存在 `outputs/realdata/data/splits/`。

## 产物

每个集合两个文件，CSV 给人看、NPZ 给模型读：

| 文件 | 内容 |
| --- | --- |
| `train.csv` / `val.csv` / `test.csv` | 文本、标签、分词与元数据（含 `video_id`、`split`） |
| `train.npz` / `val.npz` / `test.npz` | 定长 id 数组：`input_ids`（`max_len=200`）、`labels`，同一词表编码 |

## 划分规则（详见 `src/preprocessing/split.py`）

- 有 `video_id`（爬虫输出的 `bvid` 会自动映射）时**按视频分组划分**，避免同一视频的评论跨集合造成泄漏；
- 无分组字段时退化为**按标签分层划分**，并在 `dataset_stats.json` 里给出警告；
- 固定随机种子（默认 `SEED=42`）保证可复现；
- 划分后做完整性校验：`text` / `comment_id` / `group` 三个维度都不允许跨集合重复。

## 注意

- **词表只用训练集构建**，验证集与测试集不参与 `vocab` 拟合，因此第 4 章报告里的
  `oov_ratio` 才有意义；
- 课程硬性指标（训练 ≥ 8000、测试 ≥ 2000）由 `src/preprocessing` 校验，未达标默认报错；
  小样本调试需显式 `--allow-small-dataset`，该模式产物不可用于正式实验。
