# data/splits/ — 训练 / 验证 / 测试划分

本目录存放按 **训练 : 验证 : 测试 ≈ 7 : 1 : 2** 划分后的数据，由 `src/preprocessing` 的 train 模式生成。

> 📦 **CSV / NPZ 不入库（2026-11-03 起）**：本目录的 `*.csv` 与 `*.npz`（本地合计约 17 MB）体积大且可由
> `python -m scripts.prepare_training_data` 重建，**不再存入 Git 仓库（GitHub 页面不再包含）**。
> 2026-10-30 曾以「一次性快照」（提交 `1eb77a7`）强制入库，2026-11-03 已用 `git rm --cached`
> 移出版本库——**只删 Git 索引、本地文件保留**。版本库中只保留本 README 与 `.gitkeep`。

## 当前本机划分（2026-10-30，本地保留）

真实训练语料（固定版公开语料，数据版本 `753bc2014c69bdc1`，pipeline 1.1.0）：
清洗后有效 **19,711 条** → **train 13,797 / val 1,971 / test 3,943**，词表 9,753 项。

> 历史口径的划分（18,613 条清洗结果 → **train 13,029 / val 1,861 / test 3,723**）另存于
> `outputs/realdata/data/splits/`（旧数据版本，已同样移出版本库）。

## 产物

每个集合两个文件，CSV 给人看、NPZ 给模型读（**均不入库，本地重新生成**）：

| 文件 | 内容 |
| --- | --- |
| `train.csv` / `val.csv` / `test.csv` | 文本、标签、分词与元数据（含 `video_id`、`split`） |
| `train.npz` / `val.npz` / `test.npz` | 定长 id 数组：`input_ids`（`max_len=200`）、`labels`，同一词表编码 |

## 重新生成

```bash
# 推荐：固定公开语料版本，一次性重建 data/processed 与 data/splits（已有产物需 --overwrite）
python -m scripts.prepare_training_data

# 或从已准备好的标注语料直接预处理（产物同样写入 data/processed / data/splits）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv --mode train
# 等价封装：
bash scripts/split_data.sh --input data/raw/comments_labeled_balanced.csv
```

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
