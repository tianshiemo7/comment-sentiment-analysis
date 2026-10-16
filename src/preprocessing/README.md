# src/preprocessing —— ② 数据清洗与预处理

> 对应需求：中文 jieba 分词 / 英文分词小写化；去停用词、URL、HTML 标签、特殊符号；
> `max_len` 统一截断补齐；词表 `min_freq ≥ 3` 并输出统计；7:1:2 划分。
> 对应验收项：`docs/验收检查清单.md` 二、数据 与 三、预处理。

## 职责

1. **清洗**：去 URL、HTML 标签、特殊符号、重复样本、空样本（`clean.py`）；
2. **分词**：中文 jieba、英文空格分词 + 小写化，去停用词（`tokenize.py`）；
3. **词表**：`min_freq ≥ 3` 建词表，含 `<pad>` / `<unk>` 特殊符号（`vocab.py`）；
4. **编码**：按 `MAX_LEN=200` 截断 / 补齐为定长 id 序列（`tokenize.encode_ids`）；
5. **统计**：词表规模、平均文本长度、标签分布 → `outputs/stats/dataset_stats.json`（`stats.py`）；
6. **划分**：训练 : 验证 : 测试 ≈ 7 : 1 : 2，固定种子 `SEED=42`（`split.py`）。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 原始评论 `csv/xlsx`（字段 `text`，可选 `label`） | `data/raw/`、`data/sample/` |
| 输出 | 清洗分词结果（`tokens` 列） | `data/processed/` |
| 输出 | 划分后的训练/验证/测试集 | `data/splits/{train,val,test}.csv` |
| 输出 | 词表 id→词 映射 | `data/processed/vocab.json` |
| 输出 | 统计 JSON | `outputs/stats/dataset_stats.json` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `clean.py` | 文本清洗与去重 |
| `tokenize.py` | 分词、停用词、id 编码与截断补齐 |
| `vocab.py` | 词表构建（`Vocab` 数据结构） |
| `stats.py` | 数据集统计并写 JSON |
| `split.py` | 分层随机划分 train/val/test |
| `dataset.py` | csv/xlsx 读写（统一字段 `text` / `label`） |

## 待实现清单（TODO）

- [ ] `clean.py:remove_url` / `remove_html` / `remove_special_chars` —— 正则规则实现
- [ ] `clean.py:clean_text` —— 组合规则 + 去空白 + 可选小写化
- [ ] `clean.py:deduplicate` —— 去重与空样本过滤
- [ ] `tokenize.py:tokenize` —— jieba / 英文分词
- [ ] `tokenize.py:load_stopwords` —— 停用词表加载（默认 `data/stopwords.txt`，缺失时用内置最小表）
- [ ] `tokenize.py:encode_ids` —— `max_len` 截断 + `<pad>` 补齐
- [ ] `vocab.py:build_vocab` —— 词频统计 + `min_freq` 过滤 + `<pad>/<unk>` 占位
- [ ] `stats.py:dataset_stats` —— 词表规模 / 平均长度 / 标签分布
- [ ] `split.py:split_dataset` —— 7:1:2（默认带 `seed=42`）
- [ ] `dataset.py:load_records` / `save_records` —— 统一读写，xlsx 依赖 openpyxl

## 关联

- 路线图：`docs/开发路线图.md` 阶段一第 2、3、4 步
- 上游：`src/crawler`；下游：`src/embeddings`、`src/models`
- 一键脚本：`scripts/split_data.sh`
