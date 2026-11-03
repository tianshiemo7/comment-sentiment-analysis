# data/processed/ — 预处理中间产物

本目录存放预处理管线的**中间产物**，由 `src/preprocessing` 生成。

> 📦 **大 CSV 不入库（2026-11-03 起）**：本目录的大 CSV（`cleaned.csv`、`excluded.csv`、
> `comments_cleaned.csv`、`comments_excluded.csv`、`split_manifest.csv`，本地合计约 18 MB）体积大且可由
> 脚本重建，**不再存入 Git 仓库（GitHub 页面不再包含）**。2026-10-30 曾以「一次性快照」（提交 `1eb77a7`）
> 强制入库，2026-11-03 已用 `git rm --cached` 移出版本库——**只删 Git 索引、本地文件保留**。
>
> 仍入库的是小体积清单 / 快照 JSON（推理与训练入口直接读取）：`manifest.json`、`preprocessor.json`、
> `vocab.json`、`label_map.json`、`comments_manifest.json`、`comments_preprocessor.json`，以及
> `README.md` / `.gitkeep`。重新生成方式见文末「命令」。

## 当前本机产物（2026-10-30 回填，本地保留）

当前目录是**真实训练语料**产物（不再是早期小样本冒烟产物）：
外部输入 `comments_labeled.csv`（固定版公开语料，共 19,753 行）→ 清洗后有效 **19,711 条**
（负面 10,408 / 正面 9,303），排除 42 条；词表 **9,753** 项（含 PAD/UNK），数据版本 `753bc2014c69bdc1`、
pipeline 1.1.0。训练 / 验证 / 测试条数为 13,797 / 1,971 / 3,943（见 `../splits/README.md`）。

其中 `comments_cleaned.csv` / `comments_excluded.csv` / `comments_manifest.json` /
`comments_preprocessor.json` 来自 `data/raw/BV1GJ411x7h7_comments.csv` 的 `clean` 模式样本
（39 条 → 保留 38 条，排除 1 条重复文本），属于**无标签待预测数据**，不能当训练集。

## 产物清单

| 文件 | 生成者 | 是否入库 | 说明 |
| --- | --- | --- | --- |
| `comments_cleaned.csv` | `--mode clean` | 否 | 无标签评论的清洗结果，保留 `text_raw`（原文）、`text`（清洗文本）、`tokens`（分词）与采集元数据 |
| `comments_excluded.csv` | `--mode clean` | 否 | 被排除的记录，含排除原因（如 `empty_after_cleaning`、`duplicate_text`） |
| `comments_preprocessor.json` | `--mode clean` | 是 | 该批数据使用的预处理器快照（停用词 / 保护词 / 用户词典 / 表情映射） |
| `comments_manifest.json` | `--mode clean` | 是 | 输入哈希、预处理器哈希、记录哈希与 `dataset_version` |
| `cleaned.csv` / `excluded.csv` | `--mode train` | 否 | 标注数据清洗后的全量与排除清单 |
| `vocab.json` | `--mode train` | 是 | 词表（**仅用训练集构建**，`min_freq >= 3`） |
| `preprocessor.json` / `label_map.json` | `--mode train` | 是 | 推理时复用的预处理器与标签映射快照 |
| `split_manifest.csv` | `--mode train` | 否 | 最小血缘清单（不含评论正文） |
| `manifest.json` | `--mode train` | 是 | 划分/词表/哈希的总清单，下游训练入口读它 |

## 命令

```bash
# ① 正式训练语料（推荐，固定公开语料版本；同时产出 data/processed 与 data/splits）
#    首次直接运行；已有产物时加 --overwrite 覆盖重建
python -m scripts.prepare_training_data

# ② 单次预处理（无标签爬虫 CSV → 待预测数据，不划分、不建词表）
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean

# ③ 单次预处理（已标注 CSV → 清洗 + 词表 + 划分 + 编码数组）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv --mode train

# ④ 或经封装脚本（等价于 ②/③ 的 python -m src.preprocessing，附加 --seed）
bash scripts/split_data.sh --input data/raw/comments_labeled_balanced.csv
```

产物重跑需换 `--output-root` 或显式 `--overwrite`。详见 `src/preprocessing/README.md`。
