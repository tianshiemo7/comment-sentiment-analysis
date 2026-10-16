# data/processed/ — 预处理中间产物

本目录存放预处理管线的**中间产物**，由 `src/preprocessing` 生成，默认不入库（见根目录忽略规则）。

## 产物清单

| 文件 | 生成者 | 说明 |
| --- | --- | --- |
| `comments_cleaned.csv` | `--mode clean` | 无标签评论的清洗结果，保留 `text_raw`（原文）、`text`（清洗文本）、`tokens`（分词）与采集元数据 |
| `comments_excluded.csv` | `--mode clean` | 被排除的记录，含排除原因（如 `empty_after_cleaning`、`duplicate_text`） |
| `comments_preprocessor.json` | `--mode clean` | 该批数据使用的预处理器快照（停用词 / 保护词 / 用户词典 / 表情映射） |
| `comments_manifest.json` | `--mode clean` | 输入哈希、预处理器哈希、记录哈希与 `dataset_version` |
| `cleaned.csv` / `excluded.csv` | `--mode train` | 标注数据清洗后的全量与排除清单 |
| `vocab.json` | `--mode train` | 词表（**仅用训练集构建**，`min_freq >= 3`） |
| `preprocessor.json` / `label_map.json` | `--mode train` | 推理时复用的预处理器与标签映射快照 |
| `split_manifest.csv` | `--mode train` | 最小血缘清单（不含评论正文） |
| `manifest.json` | `--mode train` | 划分/词表/哈希的总清单，下游训练入口读它 |

## 命令

```bash
# 无标签爬虫 CSV → 待预测数据（不划分、不建词表）
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean

# 已标注 CSV → 训练集/验证集/测试集 + 词表 + 编码数组
python -m src.preprocessing --input data/raw/comments_labeled.csv --mode train
```

产物重跑需换输出目录或显式 `--overwrite`。详见 `src/preprocessing/README.md`。
