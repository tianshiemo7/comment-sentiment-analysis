# 原始评论数据登记

> ⚠️ **原始 CSV 未入库**：`data/raw/*.csv` 被根目录忽略规则排除（原始数据不入版本控制）。
> clone 仓库后本目录只有本 README、`来源说明.md` 与 `sources.json`；第 3 节的命令需要
> 先自行取得对应 CSV，否则会报 `FileNotFoundError`。取得方式见第 4 节。

## 1. 组内提供的视频评论（BV1UB4y1W7Xu）

文件：BV1UB4y1W7Xu_comments.csv。

本文件由用户提供，并从用户指定的本地文件复制到本目录；本步骤没有重新采集，也没有修改原始CSV。
SHA256：075d7a9c69e533a44e4294c66dbc78727242ca357974850f67d1acc9e6593697。
（取得后可用 `sha256sum data/raw/BV1UB4y1W7Xu_comments.csv` 校验是否同一份。）

共154条评论，视频编号为BV1UB4y1W7Xu。其中level=1为99条，level=2为55条，无情感标签。
采集时间与评论时间分别保存在collected_at_utc、ctime_utc；点赞与回复关系等原始字段同时保留。

处理命令：

```bash
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean
```

首次处理保留149条，排除5条完全重复的清洗文本；被排除记录在comments_excluded.csv中保留，原始CSV仍完整保存154条。
清洗后保留97条一级评论和52条二级回复；默认保留回复层级，未把无标签评论当作监督训练集。

下游读取data/processed/comments_cleaned.csv，使用已训练模型的预处理器处理text_raw再进行情感预测。
该CSV及处理产物被.gitignore忽略，不随源码自动提交。

## 2. 公开数据集与标注产物

- `data/raw/datasets/`：`bash scripts/get_data.sh --dataset <name>` 下载的公开语料（体积大，不入库）；
  下载后自动登记 `来源说明.md` / `sources.json`，可用 `--list-datasets` 查看注册表；
- `data/raw/*_unified.csv`：`python -m src.crawler.datasets --import-csv` 统一出的 `text,label`；
- `data/raw/comments_labeled_balanced.csv`：`--merge-balance` 合并均衡后的训练语料
  （实测外卖 11,987 + ChnSentiCorp 7,766 → 均衡 18,644 条，正负各 9,322）；
- `data/labeling/`：`python -m src.crawler.labeling export` 导出的标注候选与 manifest；
  人工标注后用 `labeling merge` 合并校验，产出可进训练管线的已标注 CSV。

> 这些 CSV 体积较大（约 7 MB）被 `.gitignore` 排除，不入库；命令与来源登记见
> `data/raw/来源说明.md`，可联网复现。

## 3. 爬虫实测样本与来源登记

`data/raw/来源说明.md` 与 `data/raw/sources.json` **会入库**，用于课程验收「数据来源与采集时间已注明」。
它们由 `src/crawler/sources.py` 在每次采集后自动生成/合并，当前记录了一条 2026-10-17 的本机实测：

- 视频：`BV1GJ411x7h7`（2 页一级评论，39 条，匿名可见子集）；
- 采集方式：`python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2`；
- 限速 / robots / 重试 / 耗时等合规与效率信息写在表格「备注」列；
- 对应 CSV（`BV1GJ411x7h7_comments.csv`）属于原始数据，**不入库**，可用同一命令复现
  （评论会随时间变化，条数可能不同）。

## 4. 如何取得数据

三种方式，任选一种（都能产出同样列名的 CSV）：

1. **自行采集**：用本仓库爬虫，`python -m src.crawler bilibili --bvid BV1UB4y1W7Xu --max-pages 10`
   （输出到 `data/raw/BV1UB4y1W7Xu_comments.csv`，列名见 `src/crawler/bilibili.py` 的 `COLUMNS`，
   并自动登记来源说明）。
2. **向组内索取**：该文件是组内成员本地采集/整理后手工放入的，找提供者复制。
3. **换用公开数据集**：按 `data/README.md` 的推荐（ChnSentiCorp / 外卖评论）走公开数据路线，
   并同步更新本文件与 `data/README.md` 的来源说明。

> 说明：这份数据是**无标签**评论，只用于打通「采集 → 清洗 → 待预测」链路，
> 不能当作监督训练集；训练集仍需另行准备带 `positive`/`negative` 标注的数据。
