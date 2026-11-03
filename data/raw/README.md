# 原始评论数据登记

> 📦 **数据文件不入库（2026-11-03 起）**：本目录的评论数据体积大、可由公开语料与爬虫脚本重建，因此
> **不再存入 Git 仓库（GitHub 页面不再包含这些文件）**。2026-10-30 曾以「一次性快照」强制入库
> （提交 `1eb77a7`），2026-11-03 已用 `git rm --cached` 移出版本库：**只删 Git 索引、本地文件全部保留**，
> 并同步收紧 `.gitignore` 防止再次误跟踪。
>
> 本目录**仍入库**的只有说明与登记文件：`README.md`（本文件）、`来源说明.md`、`sources.json`、
> `sentiment_crawl/README.md`、`.gitkeep`。其余数据需按下方命令在本地重新生成。

## 0. 目录清单与重新生成方式（本地保留，不入库）

| 路径 | 本地体积（约） | 来源 / 重新生成 |
| --- | --- | --- |
| `datasets/ChnSentiCorp_htl_all.csv`<br>`datasets/waimai_10k.csv` | 3.7 MB | `bash scripts/get_data.sh --list-datasets` 查看，`bash scripts/get_data.sh --dataset chnsenticorp` / `--dataset waimai_10k` 下载（SHA256 校验 + 来源登记）；等价于 `python -m src.crawler.datasets --download chnsenticorp --target-dir data/raw/datasets` |
| `chnsenticorp_unified.csv`<br>`waimai_10k_unified.csv` | 4 MB | `python -m src.crawler.datasets --import-csv data/raw/datasets/ChnSentiCorp_htl_all.csv`（默认输出同目录 `<名字>_unified.csv`，可用 `--output` 指定） |
| `comments_labeled_balanced.csv` | 3.6 MB | `python -m src.crawler.datasets --merge-balance data/raw/waimai_10k_unified.csv data/raw/chnsenticorp_unified.csv --output data/raw/comments_labeled_balanced.csv --seed 42` |
| `BV1GJ411x7h7_comments.csv` | 11 KB | `python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2`，或 `bash scripts/get_data.sh BV1GJ411x7h7` |
| `BV1tseyzCEoF_comments.csv` | 118 KB | `python -m src.crawler bilibili --bvid BV1tseyzCEoF --page-size 50 --include-replies --max-items 500 --delay 2.5`（评论随时间变化，条数可能不同） |
| `BV1UB4y1W7Xu_comments.csv` | 30 KB | 组内提供、无公开源：向提供者索取后放回本目录（SHA256 见第 1 节） |
| `sentiment_crawl/` | 约 20 MB | 第 6 节：`python scripts/build_crawl_catalog.py` → `python scripts/crawl_shards.py` → `python scripts/build_final_dataset.py` |

> 说明：`.gitignore` 已忽略以上全部数据文件；换机或清理后只保留登记文件，按上表命令即可恢复。
> 公开语料下载与 B 站采集都会自动更新 `来源说明.md` / `sources.json`（这两个文件仍入库）。

## 1. 组内提供的视频评论（BV1UB4y1W7Xu）

文件：`BV1UB4y1W7Xu_comments.csv`（**本地保留、不入库**，154 条 / 30 KB；与下方 SHA256 一致，
可用 `sha256sum data/raw/BV1UB4y1W7Xu_comments.csv` 校验）。

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
该原始 CSV 已从版本库移除、本地保留（`data/processed/comments_cleaned.csv` 是 `BV1GJ411x7h7` 那批
采集样本的 `clean` 产物，见 `data/processed/README.md`）。

## 2. 公开数据集与标注产物

- `data/raw/datasets/`：`bash scripts/get_data.sh --dataset <name>` 下载的公开语料
  （原为 `ChnSentiCorp_htl_all.csv` 7,766 条、`waimai_10k.csv` 11,987 条）；
  下载后自动登记 `来源说明.md` / `sources.json`，可用 `--list-datasets` 查看注册表；
- `data/raw/*_unified.csv`：`python -m src.crawler.datasets --import-csv` 统一出的 `text,label`
  （原为 `chnsenticorp_unified.csv` 7,765 条、`waimai_10k_unified.csv` 11,980 条；比原始件少的
  几行是空文本/格式不合法被丢弃的记录）；
- `data/raw/comments_labeled_balanced.csv`：`--merge-balance` 合并均衡后的训练语料
  （实测外卖 11,987 + ChnSentiCorp 7,766 → 均衡 18,644 条，正负各 9,322）；
- `data/labeling/`：`python -m src.crawler.labeling export` 导出的标注候选与 manifest；
  人工标注后用 `labeling merge` 合并校验，产出可进训练管线的已标注 CSV。
  例：`python -m src.crawler.labeling export --input data/raw/BV1tseyzCEoF_comments.csv --output data/labeling/sample.csv --sample-size 500`。

> 这些 CSV 原被 `.gitignore` 排除，2026-10-30 曾随快照强制入库（约 7 MB），2026-11-03 已移出版本库；
> 命令与来源登记见 `data/raw/来源说明.md`，也可联网复现。真实规模的清洗/划分产物
> （19,711 条 → 13,797 / 1,971 / 3,943）在 `data/processed/`、`data/splits/`（同样不入库）。

## 3. 爬虫实测样本与来源登记

`data/raw/来源说明.md` 与 `data/raw/sources.json` **仍入库**，用于课程验收「数据来源与采集时间已注明」。
它们由 `src/crawler/sources.py` 在每次采集后自动生成/合并，当前记录了一条 2026-10-17 的本机实测：

- 视频：`BV1GJ411x7h7`（2 页一级评论，39 条，匿名可见子集）；
- 采集方式：`python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2`；
- 限速 / robots / 重试 / 耗时等合规与效率信息写在表格「备注」列；
- 对应 CSV（`BV1GJ411x7h7_comments.csv`）**本地保留、不入库**，当前 **59 条**一级评论：
  首次采集 39 条（`collected_at_utc=2026-10-17T04:42:49Z`，即来源登记表里记录的那一批）
  + 当天补采 20 条（`05:43:59Z`）；可用同一命令复现（评论会随时间变化，条数可能不同）。

## 4. 如何取得数据

三种方式，任选一种（都能产出同样列名的 CSV，文件生成在本地、不入库）：

1. **自行采集**：用本仓库爬虫，`python -m src.crawler bilibili --bvid BV1UB4y1W7Xu --max-pages 10`
   （输出到 `data/raw/BV1UB4y1W7Xu_comments.csv`，列名见 `src/crawler/bilibili.py` 的 `COLUMNS`，
   并自动登记来源说明）。
2. **向组内索取**：该文件是组内成员本地采集/整理后手工放入的，找提供者复制。
3. **换用公开数据集**：按 `data/README.md` 的推荐（ChnSentiCorp / 外卖评论）走公开数据路线
   （`bash scripts/get_data.sh --dataset chnsenticorp`），并同步更新本文件与 `data/README.md` 的来源说明。

> 说明：这份数据是**无标签**评论，只用于打通「采集 → 清洗 → 待预测」链路，
> 不能当作监督训练集；训练集仍需另行准备带 `positive`/`negative` 标注的数据。

## 5. 待训练语料（BV1tseyzCEoF，2026-10-17，本地保留、不入库）

文件：`data/raw/BV1tseyzCEoF_comments.csv`（约 118 KB，UTF-8 BOM）。

- 视频：`BV1tseyzCEoF`《【闪客】大白话半小时理解网络！》
- 规模：**470 条** = 一级评论 266 + 楼中楼回复 204（`level=1/2`，无情感标签）
- 采集：项目爬虫 + 已登录账号；`page-size=50`、楼中楼全量、`max-items=500`、限速 2.5s/请求；
  0 风控、约 4 分钟；来源登记见 `来源说明.md` / `sources.json`
- 校验：`SHA256 = 6b92bf40c4b682f12a47ac1f3082bf19dc75b3b14a4a5e5ee8fc5012308aa8b7`
- 用途：**原始语料，暂存为后续训练集候选**；尚未清洗、未标注，因此暂不能直接用于监督训练。
  后续可先 `python -m src.crawler.labeling export` 抽样标注，合并后再走 `src/preprocessing` 训练管线。

## 6. 跨分区大规模采集数据集（2026-10-18，本地保留、不入库）

目录：`data/raw/sentiment_crawl/`（约 20 MB，UTF-8 BOM）。
版本库中只保留 [`sentiment_crawl/README.md`](sentiment_crawl/README.md)（采集规划、字段说明、质量核对与复现命令）。

- 来源：B 站排行榜接口按 **18 个分区**各取 12 个视频，共 **216 个视频**，每个视频 50 条一级评论
  （`mode=time`、`ps=50`、`max_pages=2`、不采楼中楼）；
- 规模：**10,800 条**原始评论，18 个分区各 600 条；`rpid` 全局唯一、无空文本；
- 文件：
  - `comments_raw.csv`：**总表**（原始评论 + `category/rid/split`，**未清洗**）；
  - `train.csv` / `test.csv`：按 `bvid` 分组、按分区分层 8:2 划分（train 9,000 / test 1,800）；
  - `crawl_plan.csv` / `videos_meta.csv` / `dataset_stats.json` / `bvids.txt`；
  - `shard{1,2,3}/`：3 进程分片采集的原始 CSV 与断点 JSON（`.checkpoints/`）；
  - `all_comments_raw.csv`、`*_merged.csv`、`catalog.json`、`ranking_snapshot.json`、`shard*.log`：分片合并与审计用；
- 采集：3 进程分片、单进程限速 3.0s/请求，风控 0 次，耗时约 13 分钟；来源登记见 `来源说明.md` / `sources.json`；
- 复现（依次执行，输出全部落在 `data/raw/sentiment_crawl/`）：
  ```bash
  python scripts/build_crawl_catalog.py --per-category 12 --out-dir data/raw/sentiment_crawl
  python scripts/crawl_shards.py --catalog-dir data/raw/sentiment_crawl --workers 3 --delay 3.0
  python scripts/build_final_dataset.py --base-dir data/raw/sentiment_crawl --test-ratio 0.2 --seed 42
  ```
  重新采集会覆盖这些文件（评论随时间变化，条数可能不同）；
- 用途：训练/测试语料候选；**尚无情感标签、未清洗**，需人工标注后才能用于监督训练。
