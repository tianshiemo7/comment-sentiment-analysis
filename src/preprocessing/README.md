# 数据处理与模型输入

本模块接收上游爬虫 CSV，支持两条流程：无标签评论清洗后交给下一步情感预测；有人工标签的数据用于制备训练集。也兼容 TSV 和 XLSX。网络采集、模型训练与情感预测仍由各自模块负责。

## 运行

在仓库根目录安装 requirements.txt 中的依赖。此模块实际需要 jieba、numpy，读取 XLSX 还需 openpyxl，不依赖 torch。

上游爬虫 CSV 没有情感标签时：

```bash
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean
```

主要输出为 `data/processed/comments_cleaned.csv`，另有 `comments_excluded.csv`、
`comments_preprocessor.json`、`comments_manifest.json` 和 `outputs/stats/comments_stats.json`。
保留原文 text_raw、清洗文本 text、分词 tokens、评论/视频编号及采集字段。label 留空，不自动标正负。
此流程不划分训练集，不拟合词表，不生成训练NPZ，不受训练8000/测试2000条门槛限制。

`--mode auto` 为默认：全部无标签时自动使用 clean，全部有标签时使用 train；混合有标签和无标签的文件要求明确指定模式。
只有接入已有训练模型后，下一步才能输出情感预测。预测时优先取 text_raw，并调用该模型配套的预处理器和词表，
而不是用这批待预测评论重新建立词表。清洗配置快照用于审计，不自动替换模型的训练配置。

已标注的正式训练数据：

```bash
python -m src.preprocessing --input data/raw/comments_labeled.csv --mode train
```

人工编写的 B 站语境演示数据：

```bash
python -m src.preprocessing --input data/sample/bilibili_demo.csv --allow-small-dataset
```

也可以执行：

```bash
bash scripts/split_data.sh --input data/raw/comments_labeled.csv
```

Windows 可直接使用 Python 命令。输出文件存在时默认拒绝覆盖，重跑可指定新 --output-root 或显式 --overwrite。--allow-small-dataset 只供调试，统计会明确记录未满足课程检查，不能据此宣称真实训练语料已达标。

## 输入字段与校验

必需评论文本列为 text、text_raw、comment、content、评论、评论内容或内容之一；多列文件无法识别时要求显式 --text-column，不自动把 id 当评论。
clean 模式不需要 label，提问和没有明确态度的评论也会保留，等待下一步分析。
train 模式需要 label，支持 positive/negative，也归一化 1/0 和正面/负面；neutral、uncertain、irrelevant 单独排除，缺失或未知标签不自动预测补齐。

推荐同时提供 comment_id、video_id、source_ref、collected_at。其它列保留。编号应按文本保存，Excel 已经损失的长整数精度无法恢复。input_row 记录原输入行号，text_raw 保留原评论，text 为清洗结果。

已适配当前爬虫格式：rpid → comment_id、bvid → video_id、ctime_utc → posted_at、collected_at_utc → collected_at；
原字段不会被删除。level、root_rpid、parent_rpid、video_title、likes 等字段原样保留，默认不筛掉二级回复。

空白、非文本单元格、清洗后为空、无有效词元、缺失/未知标签、中性/不确定/无关以及重复/冲突样本都有独立排除原因。相同清洗文本出现相反标签时，冲突记录全部排除等待人工复核，不自动投票。重复评论 ID 指向不同文本或标签时同样排除。只做完全重复去重，不声称已完成近重复语义聚类。

## 训练数据处理顺序与防泄漏

1. HTML/脚本、URL、全半角、控制字符和空白清理；表情归一化，问号/叹号转为词元。
2. 使用隔离的 jieba 分词器及项目领域词典；英文小写分词。停用词过滤显式保留否定、转折及程度词，单字“赞”“差”仍有效。
3. 审计标注、去重和冲突处理，再划分数据。
4. 自动检测 video_id：完整存在时按视频整体分组并尽量平衡标签；没有视频字段时按标签分层。只有部分记录提供分组字段会报错，避免默默混合两种口径。--group-by video_id 可强制要求视频字段，--group-by none 显式关闭分组。
5. 只有训练集用于统计词频、构建词表。min_freq 默认且不得低于 3，PAD=0、UNK=1；等频词按字典序排序。max_vocab_size 不含两个特殊编号。
6. 三个集合共享同一词表、资源快照及标签映射，统一右侧补齐或截断，输出掩码与有效长度。
7. 检查集合之间的清洗文本、评论 ID，以及启用分组时的视频 ID 不交叉。

分组划分为固定种子下的标签数量贪心搜索，约 7:1:2；不使用模型分数选划分，不拆视频凑整数。正式处理检查训练至少 8000 条、测试至少 2000 条、各集合包含正负类别。项目质量策略还要求各集合正面占比 45%—55%、条数比例距目标不超过 5 个百分点；这些范围是项目策略，不是课程原文规定的精确阈值。分组过大或数量不足时需调整采样。缺少 video_id 时会明确提示无法据此声明跨视频独立评估。

## 有标签训练模式交给下一阶段的文件

| 文件 | 内容 |
| --- | --- |
| data/processed/cleaned.csv | 清洗分词后的有效标注评论及原始字段 |
| data/processed/excluded.csv | 排除样本、原始行号和 exclude_reason |
| data/processed/vocab.json | 仅训练集拟合的词表与词频 |
| data/processed/preprocessor.json | 预处理版本、有效停用词、领域词典、表情映射和 max_len |
| data/processed/label_map.json | negative=0、positive=1 |
| data/processed/split_manifest.csv | 评论编号、视频、清洗文本哈希及 split |
| data/processed/manifest.json | 输入/产物哈希、数据版本、种子、依赖版本与划分配置 |
| data/splits/train.csv、val.csv、test.csv | 按集合保存的 text、label、tokens 及溯源信息 |
| data/splits/train.npz、val.npz、test.npz | 可直接读取的模型输入数组 |
| outputs/stats/dataset_stats.json | 总体与分集标签、长度、截断率、OOV、排除原因和课程检查 |

NPZ 使用 allow_pickle=False 读取，各数组行序与同名 CSV 一致：

| 数组 | 类型与形状 | 用途 |
| --- | --- | --- |
| input_ids | int64 [N,max_len] | 模型嵌入层输入 |
| attention_mask | int64 [N,max_len] | 真实词元为1，PAD为0 |
| lengths | int64 [N] | 截断后有效长度，可用于LSTM打包 |
| source_lengths | int64 [N] | 截断前分词长度 |
| labels | int64 [N] | 交叉熵目标，negative=0、positive=1 |
| comment_ids | Unicode [N] | 与原数据及错误分析对应 |

## 下游使用

```python
from src.preprocessing import load_encoded_split, load_preprocessing_bundle

train = load_encoded_split("data/splits/train.npz")
# 后续安装 PyTorch 后：
# input_ids = torch.from_numpy(train["input_ids"])
# targets = torch.from_numpy(train["labels"])

processor, vocab, labels = load_preprocessing_bundle("data/processed")
sample = processor.encode("这个教程没有讲清楚关键步骤[doge]", vocab)
# sample: text/tokens/input_ids/attention_mask/length/source_length/truncated
```

load_preprocessing_bundle 校验词表、配置和标签映射哈希。后续发布模型时，应将这套资源一起保存，并使用同一版本处理新评论；不能在预测时重新拟合词表。Word2Vec 应读取 train.csv 的 tokens 列进行训练，不把验证/测试语料作为无监督训练补充。

resources/ 内为人工维护的起始资源，不是实验统计结果。正式实验前可以修改；每次运行都会把有效资源固化到 preprocessor.json。预处理算法语义发生改变时应同步提升 PIPELINE_VERSION。

## 常用参数

--input、--mode auto|clean|train、--output-root、--text-column、--sheet-name、--lang zh|en、--max-len、--min-freq、--max-vocab-size、--seed、--ratios、--group-by、--stopwords、--userdict、--emoji-map、--allow-small-dataset、--overwrite。参数详情见 python -m src.preprocessing --help。

## 验证

```bash
python -m pytest tests/test_preprocessing.py -q
python -m pytest tests/ -q
```

包含输入格式、否定/表情保留、确定性划分、防泄漏、冲突排除、正式规模检查、训练集专用词表、资源重载与数组契约测试。12000条规模测试使用合成样本，只证明代码约束，不代表已经采集真实数据或取得模型成绩。
