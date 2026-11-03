# data/ — 数据集

课程要求：训练集 ≥ 8000 条、测试集 ≥ 2000 条，正负样本基本平衡，按 **训练 : 验证 : 测试 ≈ 7 : 1 : 2** 划分。

## 目录说明

| 目录 | 内容 | 是否入库 |
| --- | --- | --- |
| `raw/` | 原始公开数据集、爬虫直接产出 | 否（评论数据本地生成；仅 `README.md` / `来源说明.md` / `sources.json` / `sentiment_crawl/README.md` 入库） |
| `processed/` | 清洗、分词、去停用词后的中间数据 | 否（大 CSV 不入库；小体积 manifest / 词表 / 预处理快照仍入库） |
| `splits/` | 最终用于训练的 `train.csv` / `val.csv` / `test.csv` | 否（`*.csv`、`*.npz` 均不入库） |
| `uploads/` | **界面上传**的数据项目与单独评论（`<项目id>/`、`solo_comments.json`） | 否（运行时数据，随使用产生） |
| `sample/` | 字段格式样例，供照抄字段名 | 是 |

## 评论数据文件不入库（2026-11-03 起）

评论数据资源体积大、且可由公开语料与脚本重建，因此**不再存入 Git 仓库（GitHub 仓库页面不再包含这些文件）**。
2026-10-30 曾以「一次性快照」强制入库（提交 `1eb77a7`，约 136 MB）；2026-11-03 以 `git rm --cached`
把其中 490 个评论数据文件移出版本库——**只删 Git 索引、不删本地文件**，工作区数据完整保留；同时收紧
`.gitignore`，保证这些路径之后不会再被误跟踪。

| 路径 | 本地体积（约） | 原用途 | 本地重新生成 |
| --- | --- | --- | --- |
| `raw/` | 31 MB | 公开语料原始件 / 统一格式 / 合并均衡训练语料 / 爬虫实测 / `sentiment_crawl/` 大规模采集（含分片断点与日志） | 见 [`raw/README.md`](raw/README.md) |
| `processed/` 大 CSV | 18 MB | `cleaned.csv`、`excluded.csv`、`comments_cleaned.csv`、`comments_excluded.csv`、`split_manifest.csv` | `python -m scripts.prepare_training_data --overwrite`（或 `python -m src.preprocessing ...`，见下文） |
| `splits/` | 17 MB | 划分后的 `train/val/test`：CSV（人看）+ NPZ（模型读） | 同上（该命令同时产出 `processed/` 与 `splits/`） |
| `../outputs/realdata/` | 30 MB | 旧数据版本（18,644 条）的历史闭环产物 | 见 [`../outputs/README.md`](../outputs/README.md) |
| `../outputs/videos/` 运行期 JSON | 约 0.4 MB | 视频分析任务的运行时落盘（`index.json`、`<BV>.json`、`<BV>/`） | 界面「视频分析」/ `POST /api/videos` 运行时生成 |

仍入库的只有说明与登记文件：本文件、`raw/README.md`、`raw/来源说明.md`、`raw/sources.json`、
`raw/sentiment_crawl/README.md`、各级 `.gitkeep`、`sample/` 格式样例，以及 `processed/` 下的小体积
`manifest.json`、`preprocessor.json`、`vocab.json`、`label_map.json`、`comments_manifest.json`、
`comments_preprocessor.json`（推理 / 训练入口直接读取，需随代码走）。

> Git 历史提交中的旧文件仍留在历史里，本次只保证当前版本库与后续提交不再包含评论数据资源；
> 如需从 GitHub 历史彻底抹除，需要改写历史并强制推送（另行评估，不属于本次清理）。

## 当前本机数据快照（2026-10-30 历史实验，本地保留）

> 当前**部署模型**用的是 shopping-v1 电商语料（62,622 条，train 43,836 / val 6,262 / test 12,524，
> 数据版本 `dd18f284bd13662c`，词表 20,069），其训练输入与词向量保存在 `outputs/experiments/shopping-v1/`
> （本地、不入库），见 [`../docs/电商模型接入与验收-2026-11-02.md`](../docs/电商模型接入与验收-2026-11-02.md)。
> 本节描述的是根目录 `data/processed`、`data/splits` 里保留的**上一版**实验数据（本地保留，不入库），
> 两套数据不要混用。

真实训练语料 = 固定版公开语料（ChnSentiCorp 酒店 7,766 条 + 外卖 11,987 条），由
`python -m scripts.prepare_training_data` 制备：有效 19,711 条（负面 10,408 / 正面 9,303），排除 42 条。
`data/processed/`、`data/splits/` 为对应产物：train 13,797 / val 1,971 / test 3,943，词表 9,753（含 PAD/UNK），
数据版本 `753bc2014c69bdc1`、pipeline 1.1.0。`raw/` 下另有公开语料原始件、统一格式 CSV、合并均衡语料
（18,644 条）与 B 站爬虫实测 CSV；`raw/sentiment_crawl/` 是 216 视频 / 10,800 条的大规模采集。
逐项体积、规模与重新生成命令见 [`raw/README.md`](raw/README.md)；原始文件登记与 SHA256 见
`raw/来源说明.md`、`raw/sources.json`。

`../outputs/realdata/` 另存的是**旧数据版本**（18,644 条 → 13,029 / 1,861 / 3,723，词表 9,308）的历史闭环结果，
仅供参考，不能与当前数据版本混用，详见 [`../outputs/README.md`](../outputs/README.md)。

## 界面「上传爬取数据」产生的数据项目

在界面右上角上传爬虫 CSV，后端会落盘到 `data/uploads/<项目id>/` 并自动跑一遍 clean 模式管线：

```
data/uploads/<project_id>/
├── initial.csv     ① 初始数据（上传件解析后的统一 CSV）
├── clean.csv       ② 筛选后结果（清洗 + 分词，保留 text_raw 原文）
├── excluded.csv    ③ 排除记录（含 exclude_reason）
├── sentiment.csv   逐条情感（真实模型；加载失败降级 rule_placeholder）
└── analysis.json   聚合情感（占比 / 词频 / 长度分布）
```

单独评论（界面②段）集中存在 `data/uploads/solo_comments.json`。
目录布局与处理链路详见 [`uploads/README.md`](uploads/README.md) 与
[`../src/artifacts/README.md`](../src/artifacts/README.md)；接口契约见 `docs/API契约.md` 第四点五节。

## 统一字段约定

- 文本字段：`text`（单条评论文本）
- 监督训练标签字段：`label`，仅取 `positive` / `negative`，固定 `negative=0、positive=1`。
- `neutral`、`uncertain`、`irrelevant` 在训练入口单独排除；爬取评论没有标签时留空。
- `neutral` 是单条预测异常 / 无有效文本时的兜底标注（置信度 0.5），不是已训练的第三类，
  也不是模型低置信度的自动分类；真实模型输出 `positive` / `negative` 两类。
- 测试集样例额外含 `id` 列

## 推荐公开数据集

| 数据集 | 语言 | 规模 | 说明 |
| --- | --- | --- | --- |
| ChnSentiCorp 酒店子集 | 中文 | 7766 条原始记录 | 本项目固定版本的酒店评论，原始类别不均衡 |
| 外卖评论数据集 | 中文 | 11987 条原始记录 | 本项目固定版本的外卖评论，原始类别不均衡 |
| IMDB | 英文 | 5 万条 | 二分类影评，标准基线 |
| Yelp Review | 英文 | 大 | 需采样 |

## 自行爬取时的要求

1. 遵守目标站点 `robots.txt`，控制请求频率（见 `.env.example` 的 `CRAWL_DELAY_SECONDS`）；
2. 在 `data/raw/来源说明.md` 中记录：站点、URL、采集时间、采集条数、遵守的协议；
3. 给出标注规范与抽样一致性说明（如双人标注 200 条计算 Kappa）。

## 复现正式标注训练数据

```bash
# 固定公开语料版本 → 下载 + 统一 + 清洗审计 + 分层划分（产物：data/processed、data/splits、
# data/raw/training_corpus/、outputs/stats/training_data_preparation.json）
python -m scripts.prepare_training_data            # 已有产物时会拒绝覆盖，需加 --overwrite
```

脚本固定 ChineseNlpCorpus 提交版本及两份原始 CSV 的 SHA256，保存所有原始行和来源，
再统一交给预处理进行标签冲突审计、去重与分层划分，不提前丢弃标签冲突。
来源记录保存在 `data/raw/training_corpus/`；制备报告为 `outputs/stats/training_data_preparation.json`。
默认拒绝覆盖；可指定新 `--output-root`，或用 `--overwrite` 重建。

本次有效样本 19711 条（负面 10408 / 正面 9303），排除 42 条；
train / val / test 为 13797 / 1971 / 3943，词表 9753 项（含 PAD/UNK）。
这些是已有公开标注，领域为酒店与外卖；没有视频编号，采用按标签分层划分。
通过规模检查不代表 B 站评论预测效果，仍需独立人工标注的 B 站测试集评估。

## 处理后交给情感分析模型

上游爬虫输出不带情感标签时，直接清洗并输出待预测CSV（要求该 CSV 已在本地，例如重新采集得到）：

```bash
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean
```

下一步读取 data/processed/comments_cleaned.csv，用已接入的真实模型判断情感（`src/inference/service.py`
的统一预测器；界面「数据项目」上传后自动走同一预测器，加载失败才降级规则占位）。原文在 text_raw 中，
供模型配套预处理器使用；清洗文本和分词分别为 text、tokens。此流程不需要人工标签，也不会生成训练集或自动标注。
输入的 rpid、bvid、ctime_utc、collected_at_utc 会增加对应的标准编号/时间字段，原始列及回复层级保留。

以下规模要求适用于另行准备的有标签训练语料：

建议准备至少12000条清洗后有效正负标注评论，以便按约7:1:2划分后满足训练不少于8000、测试不少于2000。
除了 text、label，哔哩哔哩数据还应提供 comment_id、video_id、source_ref、collected_at；编号按文本保存。
默认有完整 video_id 时采用视频分组，没有时按标签分层并在报告中说明。

```bash
python -m src.preprocessing --input data/raw/comments_labeled.csv --mode train
```

data/sample/bilibili_demo.csv 是27条人工编写演示记录，24条有效，另含中性、空白和重复样例；不是从哔哩哔哩抓取的数据。
可用 --allow-small-dataset 跑通流程，不能将其用于报告真实模型成绩。

预处理在 data/processed/ 保存清洗/排除数据、词表、标签映射及资源快照；data/splits/ 同时保存 CSV 和
模型可读的 NPZ（词编号、补齐掩码、有效长度及0/1标签）。只使用训练集拟合词表，验证与测试中的新词进入UNK。
完整契约见 [预处理模块说明](../src/preprocessing/README.md)。

> 大型下载文件和处理产物不再入库（2026-11-03 起，见上文「评论数据文件不入库」）；需要时按上述任一命令
> 重新生成即可。处理入口可接收已经取得的无标签评论，或已标注训练文件；B 站采集入口见 `src/crawler/`。
