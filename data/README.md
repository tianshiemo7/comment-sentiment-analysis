# data/ — 数据集

课程要求：训练集 ≥ 8000 条、测试集 ≥ 2000 条，正负样本基本平衡，按 **训练 : 验证 : 测试 ≈ 7 : 1 : 2** 划分。

## 目录说明

| 目录 | 内容 | 是否入库 |
| --- | --- | --- |
| `raw/` | 原始公开数据集、爬虫直接产出 | 是（2026-10-30 一次性快照，见下节） |
| `processed/` | 清洗、分词、去停用词后的中间数据 | 是（**真实语料**产物，2026-10-30 回填） |
| `splits/` | 最终用于训练的 `train.csv` / `val.csv` / `test.csv` | 是（**真实语料**产物，2026-10-30 回填） |
| `uploads/` | **界面上传**的数据项目与单独评论（`<项目id>/`、`solo_comments.json`） | 否（运行时数据，随使用产生） |
| `sample/` | 字段格式样例，供照抄字段名 | 是 |

## 已入库的数据快照（2026-10-30）

为便于课程验收与换机备份，本机工作副本中被 `.gitignore` 排除的数据以**一次性快照**方式强制入库
（提交 `1eb77a7`，共 521 个文件 / 约 56 MB）。`.gitignore` 规则保持不变，快照之外的新增大文件仍默认不入库；
唯一被刻意排除的是 `outputs/settings.json`（含 B 站 Cookie 明文）。本轮交付又把 `processed/`、`splits/`
回填为**真实训练语料**产物（原冒烟产物已被替换）。

| 文件 | 规模 | 说明 |
| --- | --- | --- |
| `raw/datasets/ChnSentiCorp_htl_all.csv` | 7,766 条 | 公开语料原始下载件（`label,review`，1 正面 / 0 负面） |
| `raw/datasets/waimai_10k.csv` | 11,987 条 | 外卖评论公开语料原始下载件（1 正面 / 0 负面） |
| `raw/chnsenticorp_unified.csv` | 7,765 条 | 统一为 `text,label`（positive 5,322 / negative 2,443） |
| `raw/waimai_10k_unified.csv` | 11,980 条 | 统一为 `text,label`（positive 4,000 / negative 7,980） |
| `raw/comments_labeled_balanced.csv` | **18,644 条** | 两份公开语料合并 + 类别均衡（正负各 9,322），课程硬指标训练语料 |
| `raw/BV1GJ411x7h7_comments.csv` | 59 条 | 爬虫首次实测样本（一级评论，首次 39 + 补采 20，无标签） |
| `raw/BV1tseyzCEoF_comments.csv` | 470 条 | 课程验收待训练语料（一级 266 + 楼中楼 204，无标签） |
| `raw/BV1UB4y1W7Xu_comments.csv` | 154 条 | 组内提供的视频评论（一级 99 + 二级 55，无标签；SHA256 `075d7a9c…3697`） |
| `raw/sentiment_crawl/` | **10,800 条** | 跨分区大规模采集：18 分区 / 216 视频，train 9,000 / test 1,800 |
| `processed/`、`splits/` | **19,711 条** | 真实公开语料产物：训练集 13,797 / 验证集 1,971 / 测试集 3,943，词表 9,753（含 PAD/UNK），数据版本 `753bc2014c69bdc1`、pipeline 1.1.0；CSV + NPZ 齐全，共约 19 MB + 18 MB |

真实规模的清洗与划分产物即上表 `processed/`、`splits/`（19,711 → 13,797 / 1,971 / 3,943）；
`outputs/realdata/` 另存的是**旧数据版本**（18,644 条 → 13,029 / 1,861 / 3,723，词表 9,308）的历史闭环结果，
仅供参考，不能与当前数据版本混用，详见 [`../outputs/README.md`](../outputs/README.md)。原始文件登记与 SHA256 见
`raw/来源说明.md`、`raw/sources.json` 与 [`raw/README.md`](raw/README.md)。

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
python -m scripts.prepare_training_data
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

上游爬虫输出不带情感标签时，直接清洗并输出待预测CSV：

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
python -m src.preprocessing --input data/raw/comments_labeled.csv
```

data/sample/bilibili_demo.csv 是27条人工编写演示记录，24条有效，另含中性、空白和重复样例；不是从哔哩哔哩抓取的数据。
可用 --allow-small-dataset 跑通流程，不能将其用于报告真实模型成绩。

预处理在 data/processed/ 保存清洗/排除数据、词表、标签映射及资源快照；data/splits/ 同时保存 CSV 和
模型可读的 NPZ（词编号、补齐掩码、有效长度及0/1标签）。只使用训练集拟合词表，验证与测试中的新词进入UNK。
完整契约见 [预处理模块说明](../src/preprocessing/README.md)。

> 大型下载文件和处理产物默认不入库，可通过上述命令重建；2026-10-30 已按「一次性快照」入库一份（见上文，
> 提交 `1eb77a7`），`.gitignore` 规则未改，后续新增大文件仍默认不入库。本轮交付已把 `processed/`、`splits/`
> 回填为真实语料产物（19,711 条 → 13,797 / 1,971 / 3,943，词表 9,753，数据版本 `753bc2014c69bdc1`）。
> 处理入口可接收已经取得的无标签评论，或已标注训练文件；B 站采集入口见 `src/crawler/`。
