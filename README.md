# 网站用户评论情感分析系统

> Website User Comment Sentiment Analysis System

[![CI](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-%E2%89%A5%203.10-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/Node.js-%E2%89%A5%2018-339933?logo=nodedotjs&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

给定网站用户评论文本，自动判断其情感极性（正面 / 负面），并提供可交互的 Web 界面。

**主流程（B 站视频评论分析）**：在界面顶部输入一个 **BV 号**提交分析任务，
左侧栏查看所有任务的实时状态（排队中 / 爬取中 / 处理中 / 已完成 / 失败 + 进度条），
点击任意一项，右侧展示该视频的详细分析结果：情感占比饼图、情感趋势折线、
正负关键词词云、关键词排行、代表性评论与模型指标。

**辅助功能**：单条评论预测、批量文件预测（`.csv` / `.xlsx` 导入导出），用于课程要求的算法演示。

本仓库为**开发中的课程设计项目**，各部分状态如下（避免歧义，逐项说明）：

| 部分 | 状态 |
| --- | --- |
| B 站评论爬虫 `src/crawler/` | ✅ 已实现：WBI 签名 / 风控退避 / 文本标准化 / robots / 来源登记 / 断点续爬 / **大规模批量采集（清单/零请求续采/不限页数/流式合并）** / 公开数据集下载 / 标注抽样与合并 / 效率基准 · 本机实测见 [十二、爬虫模块改进与实测效率](#十二爬虫模块改进与实测效率) |
| 数据处理管线 `src/preprocessing/` | ✅ 已实现（清洗 / 分词 / 划分 / 词表 / 编码 / 统计） |
| 真实数据规模（课程硬指标） | ✅ 公开语料 + 类别均衡后 **18,644 条**，`course_requirements_met=true`（train 13,029 / test 3,723，正负各 ~9.3k）；数据文件不入库，命令见第四节与第十二节 |
| 前后端页面与接口 `frontend/` + `src/inference/` + `src/videos/` + `src/artifacts/` | ✅ 已实现 |
| 数据项目（右上传爬虫数据 → clean 管线 → 三张表 + 逐条评论情感） | ✅ 已实现（`src/artifacts/`）· ⚠️ 情感为规则占位 |
| 单独评论（单条预测结果存入左栏②段，无图表） | ✅ 已实现 |
| `/api/videos` 链路中的爬取与情感推理 | ✅ 默认占位；`VIDEO_REAL_CRAWL=1` 时**真实爬取评论**（情感仍为规则占位），采集失败落 `failed`；见第十二节与 `src/videos/README.md` |
| 词向量 / 深度模型 / 训练 / 评估 / 可视化 | ✅ 已实现（Word2Vec、TextCNN、BiLSTM-Attention、训练/早停/日志、指标与四项出图；需装 torch，见第六节） |

数据处理支持无标签爬虫 CSV 到待预测 CSV 的交接，也支持已标注数据到训练集、词表和模型输入数组的制备。
B 站视频分析链路（输入 BV 号 → 状态跟踪 → 图表词云详情）的前后端与接口契约已落地；
真实爬取已做成 opt-in（`VIDEO_REAL_CRAWL=1`），真实模型尚未接入，接口契约不变（见 `docs/API契约.md`）。

---

## 一、项目结构

```
comment-sentiment-analysis/
├── data/                     数据集与上传数据
│   ├── raw/                  原始公开数据集、爬虫原始产出
│   ├── processed/            清洗分词后的中间数据
│   ├── splits/               划分后的 train / val / test
│   ├── uploads/              界面上传的「数据项目」落盘（含 solo_comments.json）
│   └── sample/               字段格式样例（照此格式准备数据）
├── resources/                分词与清洗资源（stopwords / userdict / emoji_map）
├── src/                      Python 主干代码
│   ├── crawler/              ① 爬虫模块（B 站评论采集）
│   ├── preprocessing/        ② 数据清洗 + jieba 分词 + 切分 + 词表构建
│   ├── embeddings/           ③ 词向量（Word2Vec / GloVe）
│   ├── models/               ④ 模型（CNN / BiLSTM-Attention，BERT 微调预留）
│   ├── training/             ⑤ 训练与验证循环
│   ├── evaluation/           ⑥ 指标评估（Accuracy / P / R / F1 / 混淆矩阵）
│   ├── visualization/        ⑦ 结果可视化（曲线 / 热力图 / 词云 / Attention）
│   ├── videos/               B 站视频分析任务（状态机 + 详情，供前端①③段调用）
│   ├── artifacts/            数据项目与单独评论（上传 → clean 管线 → 三张表 + 占位情感）
│   └── inference/            ⑧ FastAPI 推理服务（:8000，供前端调用）
├── notebooks/                实验记录与探索性分析（EDA）
├── frontend/                 Node.js 前端展示（Vue 3 + Vite，:3000）
│   ├── src/views/            B站分析(首页) / 单条预测 / 批量预测 / 可视化 / 模型对比
│   ├── src/components/       左栏三段、BV 输入框、项目/评论详情与 ECharts 图表组件
│   ├── src/composables/      轮询与详情状态（usePolling / useArtifacts 等）
│   ├── src/api/              接口封装（artifacts.js 主流程、videos.js、client.js）
│   ├── src/components/Icon.vue  统一图标组件（内联 SVG，零依赖）
│   ├── src/assets/icons/     内置的 Remix Icon 精选子集（48 个，见 NOTICE.txt）
│   └── mock/                 后端未实现前的接口模拟层（Vite 插件，一行卸载）
├── models/                   训练好的模型权重存放目录
│   ├── checkpoints/          训练检查点（.pt / .pth）
│   ├── word2vec/             词向量文件
│   └── bert/                 BERT 微调权重
├── outputs/                  产物输出
│   ├── figures/              图表（曲线、混淆矩阵、词云、Attention）
│   ├── logs/                 训练日志
│   ├── predictions/          批量预测导出结果
│   ├── stats/                数据统计（词表规模、平均长度、标签分布）
│   └── videos/               B 站任务落盘（index.json + 每个视频一份详情）
├── docs/                     文档与课程报告
├── scripts/                  一键脚本（取数 / 划分 / 训练 / 启动服务 / 生成图标子集 / 前端图标验收）
├── tests/                    测试
└── requirements.txt          Python 依赖
```

## 二、需求对照表

| 课程要求 | 对应位置 | 状态 |
| --- | --- | --- |
| 数据集：训练 ≥ 8000、测试 ≥ 2000、正负均衡 | `data/` `data/sample/` | 骨架就绪，待放数据 |
| 数据来源：公开数据集或爬虫（遵守 robots，注明来源与采集时间） | `src/crawler/` | ✅ 爬虫已实现 WBI 签名 / 限速 / robots 校验 / 来源登记（`data/raw/来源说明.md`），实测见第十二节 |
| 划分 7 : 1 : 2 | `src/preprocessing/split.py` | 已实现 分层或按视频分组 |
| 中文 jieba 分词、英文分词小写化；去停用词/URL/HTML/特殊符号 | `src/preprocessing/` | 已实现 保护否定词与表情信息 |
| 统一 max_len（如 200）截断补齐 | `src/preprocessing/processor.py` | 已实现 同时输出有效长度及掩码 |
| 词表构建 min_freq ≥ 3，统计词表规模/平均长度/标签分布 | `src/preprocessing/` → `outputs/stats/` | 已实现 词表仅使用训练集 |
| 词向量 Word2Vec / GloVe | `src/embeddings/` | ✅ Word2Vec 已实现（GloVe 可加载外部预训练向量，未内置下载） |
| 两种深度模型搭建、训练与对比 | `src/models/` `src/training/` | ✅ 已实现（TextCNN / BiLSTM-Attention + 统一训练循环 + 模型对比表） |
| 指标 Accuracy / Precision / Recall / F1 / 混淆矩阵 | `src/evaluation/` | ✅ 已实现（含宏平均与错误分析） |
| 损失 / 准确率曲线、混淆矩阵热力图、正负词云、Attention 可视化 | `src/visualization/` → `outputs/figures/` | ✅ 已实现（训练 CLI 自动出图） |
| 可运行入口：CLI 或 Web 界面 | `src/inference/` + `frontend/` | 已实现 |
| **B 站视频评论分析主流程**（输入 BV 号 → 状态跟踪 → 图表词云详情） | `frontend/` + `src/videos/` + `docs/API契约.md` | 前后端与契约均已实现（爬取/推理为占位） |
| **爬虫数据上传 → 处理结果项目**（初始数据 / 筛选后结果 / 排除记录 + 逐条评论情感） | `src/artifacts/` + 前端左栏①段 | 已实现（情感为规则占位） |
| **单独评论集中管理**（单条预测结果存入左栏②段） | `src/artifacts/` + 前端②段 | 已实现 |
| 输入一条评论 → 情感极性与置信度 | `POST /api/predict` | 已实现 |
| 批量预测 .csv/.xlsx 并导出 | `POST /api/predict/batch` | 已实现 |
| requirements.txt 与 README 说明环境/数据路径/命令 | `requirements.txt` `README.md` | ✅ |
| 课程设计报告（≥ 5000 字，8 章结构） | `docs/课程设计报告大纲.md` | 大纲就绪 |

## 三、环境要求

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | ≥ 3.10（开发环境 3.13 已通过） | 爬虫、预处理、训练、推理服务 |
| Node.js | ≥ 18（开发环境 24 已通过） | 前端开发与构建 |
| npm | 随 Node 附带 | 前端依赖管理 |
| PyTorch | ≥ 2.2（CPU 即可） | 训练 TextCNN / BiLSTM-Attention；未安装时跳过训练与模型测试 |

## 四、快速开始

### 1. 后端推理服务（端口 8000）

```bash
# 在仓库根目录
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 启动服务
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload
# 或使用脚本
bash scripts/serve.sh
```

接口文档（FastAPI 自动生成）：http://localhost:8000/docs

### 2. 前端展示（端口 3000）

```bash
cd frontend
npm install
npm run dev        # 开发模式，访问 http://localhost:3000
npm run build      # 生产构建，产物在 frontend/dist/
```

前端通过 Vite 代理把 `/api`、`/health` 转发到 `http://localhost:8000`，
因此**两个服务需同时运行**。

### 3. 数据处理与后续训练

```bash
# 接收上游无标签爬虫CSV，清洗后交给下一步情感预测
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean

# 人工编写的演示数据，只用于检验管线，不能作为真实实验语料
python -m src.preprocessing --input data/sample/bilibili_demo.csv --allow-small-dataset

# 正式处理：默认检查训练不少于8000、测试不少于2000及分布条件
python -m src.preprocessing --input data/raw/comments_labeled.csv

# 使用新输出目录或显式 --overwrite 可以重跑；Shell入口同样可用
bash scripts/split_data.sh --input data/raw/comments_labeled.csv

# 训练 + 测试评估 + 出图（需要 PyTorch；data/splits 不存在时会先跑上面的预处理）
bash scripts/train.sh

# 只训练一个模型 / 指定超参 / 顺带训练词向量并用预训练 embedding 初始化
MODELS=textcnn TRAIN_ARGS="--epochs 20 --batch-size 64" bash scripts/train.sh
WITH_WORD2VEC=1 bash scripts/train.sh

# 小样本冒烟（样例数据必须加 --allow-small-dataset；训练结果只用于验证链路）
python -m src.preprocessing --input data/sample/bilibili_demo.csv --allow-small-dataset
bash scripts/train.sh
```

无标签模式输出 `data/processed/comments_cleaned.csv`，保留原文、清洗文本、分词及采集元数据，不生成情感标签、训练词表或训练划分。
默认 auto 模式会识别无标签输入；混合标签文件需明确选择 clean 或 train。
下一步预测需接入训练好的模型，并使用模型配套资源处理 text_raw。

有标签训练模式输出包括 `data/splits/{train,val,test}.csv` 与同名 `.npz`、训练词表、预处理资源快照、标签映射、
输入和产物哈希、排除记录及统计。模型输入数组为 `input_ids`、`attention_mask`、`lengths` 和 `labels`，
标签统一为 `negative=0、positive=1`。后续预测应加载这套资源，不能重新建立词表。
详细字段、参数、验收策略与下游调用示例见 [数据处理说明](src/preprocessing/README.md)。

### 4. 数据采集（爬虫）

```bash
# B 站评论：采集 2 页一级评论 + 楼中楼；默认 2.5 秒/请求、遵守 robots、自动登记来源
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2 --include-replies

# 机器可读报告（请求数 / 重试 / 延迟分位 / 风控恢复次数）
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 1 --json

# 或使用统一脚本（参数见脚本头部注释）
bash scripts/get_data.sh BV1GJ411x7h7

# 通用 HTML 页面（robots + 限速 + CSS 选择器）
python -m src.crawler html --url https://example.com/post --source 示例站点 \
    --selector ".comment-content" --max-items 100

# 采集后：来源登记在 data/raw/来源说明.md 与 sources.json；
# 再交给预处理清洗
python -m src.preprocessing --input data/raw/BV1GJ411x7h7_comments.csv --mode clean
```

爬虫单元测试与效率基准（离线 mock，不访问真实站点）：

```bash
python -m pytest tests/test_crawler.py -q
python scripts/bench_crawler.py offline --pages 5 --per-page 20 --include-replies
```

### 5. 公开数据集、标注与 `/api/videos` 真实爬取

```bash
# 查看/下载公开带标签中文情感语料（下载后自动登记来源；校验失败不会留下半成品）
bash scripts/get_data.sh --list-datasets
bash scripts/get_data.sh --dataset chnsenticorp
python -m src.crawler.datasets --import-csv data/raw/datasets/ChnSentiCorp_htl_all.csv \
    --output data/raw/chnsenticorp_unified.csv

# 多份语料合并 + 类别均衡（按较小类别下采样；实测 18,644 条 / 正负各 9,322）
python -m src.crawler.datasets --merge-balance \
    data/raw/waimai_10k_unified.csv data/raw/chnsenticorp_unified.csv \
    --output data/raw/comments_labeled_balanced.csv --seed 42

# 跑课程验收的训练预处理（实测 train 13,029 / val 1,861 / test 3,723，
# course_requirements_met=true；数据文件不入库，按上面命令可复现）
python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv

# 无标签评论 → 标注候选（按 video_id 分层、固定 seed、输出 manifest）
python -m src.crawler.labeling export --input data/raw/BV1GJ411x7h7_comments.csv \
    --output data/labeling/sample.csv --sample-size 500 --seed 42
# 人工标注后合并并校验（标签取值 / 重复 id / 冲突 / 缺失）
python -m src.crawler.labeling merge --sample data/labeling/sample.csv \
    --labels data/labeling/labels.csv --output data/raw/comments_labeled.csv

# /api/videos 真实爬取（opt-in；默认关闭，开启后「采集评论」阶段会真的联网）
VIDEO_REAL_CRAWL=1 VIDEO_CRAWL_MAX_PAGES=1 VIDEO_CRAWL_DELAY=2.5 \
    python -m uvicorn src.inference.app:app --port 8000
```

> 真实采集失败时任务会落 `failed` 并带可读原因，不会卡在 `crawling`；
> 真实评论进入 detail 后 `placeholder=false`，但情感极性仍是规则占位（`method=rule_placeholder`）。
> 完整参数见 `src/videos/README.md` 与 `src/crawler/README.md`。

## 五、当前实现状态（开发中）

服务与界面已可运行，但**预测结果为占位实现，不是训练出来的模型**：

| 部分 | 状态 |
| --- | --- |
| FastAPI 推理服务（单条/批量预测与模型列表） | ✅ 可运行，契约已锁定 |
| 批量上传解析与结果导出下载 | ✅ 可用（csv / txt / tsv / xlsx，导出 UTF-8 BOM CSV） |
| 前端页面（B站分析主界面 + 单条预测 / 批量预测 / 实验可视化 / 模型对比） | ✅ 可运行（`npm run dev`），真实调用后端接口 |
| B 站分析主流程接口 `/api/videos*` | ✅ 已实现（`src/videos/`），任务状态机 + 详情接口；默认占位，`VIDEO_REAL_CRAWL=1` 时真实爬取评论（情感仍规则占位） |
| 情感预测逻辑 | ⚠️ **规则占位**：`src/inference/service.py:predict_rule_based`，内置 140 词情感词表（含程度副词与否定词翻转）。它能对明显正面/负面的评论给出方向正确的结果，用于打通链路，**不是深度学习模型** |
| 数据读写 / 清洗分词 / 去重冲突审计 / 划分 / 词表 / 编码 / 统计 | ✅ 已实现，含训练与推理共用资源及防泄漏校验 |
| B 站爬虫 `src/crawler/` | ✅ 可采集：WBI 签名 / 限速抖动 / 风控退避 / 文本标准化 / robots 校验 / 来源登记 / 断点续爬；`python -m src.crawler bilibili --help`，实测见第十二节 |
| 词向量 / 两个深度模型 / 训练 / 评估 / 可视化 | ✅ 已实现：Word2Vec（`src/embeddings/`）+ TextCNN / BiLSTM-Attention（`src/models/`）+ 早停/检查点/日志（`src/training/`）+ 指标/混淆矩阵/错误分析（`src/evaluation/`）+ 曲线/热力图/词云（`src/visualization/`）。**需要 PyTorch**；用真实数据训练后 `metrics_<模型>.json` 会让 `/api/models` 显示真实指标 |

训练链路已实现（`bash scripts/train.sh`）：预处理产物 → DataLoader → TextCNN / BiLSTM-Attention
→ 早停 + best 检查点 → 测试集指标 / 对比表 / 错误样例 → 曲线 / 混淆矩阵 / 词云。
产物位置：`models/checkpoints/<模型>/best.pt`、`outputs/logs/train_*.json`、
`outputs/stats/metrics_<模型>.json`、`outputs/figures/*.png`。

注意：**训练需要安装 PyTorch**（`requirements.txt` 中按注释单独安装）；不装 torch 时
`pytest` 会自动跳过模型/训练相关用例，其余服务与数据处理测试照常运行。
训练出的检查点目前**还未接入推理服务**：替换真实模型的入口仍是实现 `src/inference/service.py`
的 `load_model` 与 `predict_text`，加载成功后接口契约不变、`method` 自动变成模型名。
**近期还要做什么、做到什么程度算完成**，见下方「短期实现目标」一节。

## 六、前后端接口契约

契约已锁定，前后端按此实现，任何一侧不得单方面改名。

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float},"method":str}` |
| POST | `/api/predict/batch` | multipart 文件（字段名 `file`；支持 `.csv` / `.txt` / `.tsv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | `text/csv` 附件（批量结果导出，UTF-8 BOM，Excel 可直接打开） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` |
| GET | `/api/videos` | — | 视频任务列表（左侧栏「视频任务」段） |
| POST | `/api/videos` | `{"bvid": str}` | 创建分析任务 |
| GET | `/api/videos/{bvid}/detail` | — | 视频分析详情（图表 / 词云 / 评论） |
| POST | `/api/videos/{bvid}/retry`、`DELETE /api/videos/{bvid}` | — | 重试 / 删除任务 |
| GET | `/api/artifacts` | — | 左栏三段列表：`数据项目` / `单独评论` / `视频任务` |
| POST | `/api/artifacts/upload` | multipart `files`（可多个）+ 可选 `name` | 上传爬虫数据 → 落盘为**数据项目**，自动处理 |
| GET | `/api/artifacts/{id}`、`/stages`、`/table?stage=&offset=&limit=` | — | 项目状态 / 表清单 / **分页读表** |
| GET | `/api/artifacts/{id}/comments?offset=&limit=&label=` | — | **逐条评论情感（可展开）** |
| GET | `/api/artifacts/{id}/analysis` | — | 聚合情感分析（`method` 标明占位或模型名） |
| POST | `/api/artifacts/{id}/open` | `{"stage": "clean"}` | **在服务器本机**用系统程序打开该 CSV |
| GET | `/api/artifacts/{id}/path?stage=clean` | — | 只读取该 CSV 的路径（**无副作用，不打开程序**，供「复制文件地址」用） |
| POST | `/api/artifacts/{id}/analyze`、`DELETE /api/artifacts/{id}` | — | 重新处理 / 删除项目 |
| POST | `/api/solo-comments` | `{"text","label","confidence","probs","method"}` | 保存单条评论到左栏「单独评论」段 |
| GET | `/api/solo-comments?offset=&limit=`、`DELETE /api/solo-comments/{id}` | — | 列表 / 删除（`?ids=` 批量删除，幂等） |

说明：

- `label` 取值 `positive` / `negative`，`probs` / `confidence` 均为 **0~1** 的小数；
- 批量接口把结果写入 `outputs/predictions/batch_prediction_<时间戳>_<id>.csv`，`export_url` 即上表第 4 行的下载地址；
- 上传文件首行为表头，文本列自动识别顺序：`text` → `comment` → `content` → `评论` → 第一列；
- 其他后缀（如 `.xls`、无后缀）返回 400；前端文件选择框当前只开放 `.csv` / `.xlsx`；
- 非法文件名返回 400、不存在的文件返回 404（下载路径已做目录穿越防护）。
- **数据项目**：上传爬虫 CSV 后落盘到 `data/uploads/<项目id>/`，自动按 `clean` 模式跑一遍
  `src/preprocessing` 管线，产出 `initial.csv`（初始数据）、`clean.csv`（筛选后结果）、
  `excluded.csv`（排除记录）三张表与逐条情感；标题未指定时显示「未自定义数据」。
- **单独评论**：粒度与视频任务相当但**不渲染图表**（只有原文 / 极性 / 置信度 / 概率条），
  由「单条预测」页的「保存到左栏」开关写入。
- **情感结果当前是占位**：`method` 字段为 `rule_placeholder`（140 词规则预测器），
  界面据此显示「规则占位」标识，接入真实模型后自动变为模型名。
- 完整字段定义与安全约束见 [`docs/API契约.md`](docs/API契约.md) 第四点五节。

## 七、数据格式约定

单条评论文本字段统一命名为 `text`，情感标签字段为 `label`（取值 `positive` / `negative`）。

`data/sample/train_sample.csv`（最小格式，用于训练管线）：

```csv
text,label
这个商品质量很好，物流也快，非常满意。,positive
```

`data/sample/test_sample.csv`（无标签，用于批量预测）：

```csv
id,text
1,东西一般般，没有想象中那么好。
```

`data/sample/bilibili_demo.csv`（**人工编写的演示数据**，用于打通界面①段，不可用于正式实验）：

```csv
comment_id,video_id,text,label,source
demo001,demo_video01,这个教程讲解清晰看完就学会了,positive,人工编写演示样本
```

**爬虫产出格式**（`src/crawler/bilibili.py` 实际写出的列，也是上传到界面的输入格式）：

```csv
bvid,aid,video_title,rpid,level,root_rpid,parent_rpid,text,likes,reply_count,ctime_utc,collected_at_utc
BV1UB4y1W7Xu,112233,示例标题,123456,1,,,这个教程讲解清晰,12,3,2026-09-01T12:00:00+00:00,2026-10-16T10:00:00+00:00
```

> 上传到「数据项目」时，预处理会自动做列名映射：`bvid → video_id`、`rpid → comment_id`、
> `ctime_utc → posted_at`、`collected_at_utc → collected_at`，因此按视频分组划分能直接生效。
> 详见 `data/uploads/README.md` 与 `src/artifacts/README.md`。
>
> 爬虫落盘前会做**统一标准化**：HTML 实体还原、全角字母数字转半角（保留中文标点）、
> 删除零宽/控制字符、空白归一、纯标点与空文本过滤；emoji 与 `[笑哭]` 等 B 站表情保留。
> 每次采集的「站点 / URL / 采集时间 / 条数 / 合规说明」自动写入
> [`data/raw/来源说明.md`](data/raw/来源说明.md)，同时保留机器可读的 `data/raw/sources.json`。

## 八、目录职责

每个源码包内附 `README.md`，说明该模块的职责、输入输出、待实现内容与对应需求条目。
详见 [`docs/开发路线图.md`](docs/开发路线图.md)。

## 九、开发团队与分工

见 [`docs/课程设计报告大纲.md`](docs/课程设计报告大纲.md) 第 8 章，待填写。

## 十、B 站主流程界面（前端设计）

界面分三块：顶部 BV 输入框、左侧可收起任务栏、右侧详情区。

```
┌──────────────────────────────────────────────────────────────┐
│  [BV号输入框________________]  [开始分析]      ← 顶部小框      │
├─────────────────────┬────────────────────────────────────────┤
│ 左侧栏（可收起）      │  右侧详情区                            │
│ ┌─────────────────┐ │  ┌──────────────────────────────────┐ │
│ │BV1xx…  ●爬取中   │ │  │ 视频信息：标题/UP主/评论数/时间   │ │
│ │▓▓▓▓░░░░ 43%     │ │  ├──────────────────────────────────┤ │
│ ├─────────────────┤ │  │ 情感占比饼图 · 情感趋势折线       │ │
│ │BV1yy…  ●处理中   │ │  │ 正/负词云 · 关键词排行            │ │
│ ├─────────────────┤ │  │ 代表性评论 · 模型指标             │ │
│ │BV1zz…  ●已完成   │ │  └──────────────────────────────────┘ │
│ │BV1ww…  ×失败     │ │  未完成 → 只显示「处理中」+ 进度+子步骤│
│ └─────────────────┘ │                                        │
└─────────────────────┴────────────────────────────────────────┘
```

要点：

- **左侧栏分三段**（各自可收起、各自记忆状态）：
  1. **数据项目** —— 右上角「上传爬取数据」上传的爬虫 CSV，落盘后自动跑 `clean` 管线，
     条目显示 `初始行数 → 筛选后行数`；未指定名称时显示「未自定义数据」；
  2. **单独评论** —— 集中存放「单条预测」页保存的单条评论（粒度和视频相当，但**不渲染图表**）；
  3. **视频任务** —— 输入 BV 号创建的分析任务（状态徽章 / 进度条 / 评论数）。
- **数据项目详情**：三张表可切换查看（初始数据 / 筛选后结果 / 排除记录），
  支持分页；并可**展开逐条评论的情感**（原文 + 极性 + 置信度）；
  操作按钮统一放在**最底部**：打开 CSV（服务器本机用系统程序打开）、复制文件地址、重新处理。
- **视频任务详情**：未完成时**只渲染「处理中」状态块**（进度 + `phases` 子步骤），不渲染图表；
  完成后渲染饼图、折线、词云、关键词排行、代表性评论、模型指标，并可展开逐条评论。
- **状态推进**：`queued → crawling → processing → completed`（或 `failed`），
  左侧栏每 3 秒轮询一次；全部任务到达终态后停止轮询，页面隐藏时暂停。
- **词云与图表由前端渲染**，后端只返回词频、分布、趋势数据（不生成图片）。
- **情感结果当前为占位**：`method="rule_placeholder"`，界面显示「规则占位」标识。
- **接口契约**：完整字段定义见 [`docs/API契约.md`](docs/API契约.md)（前后端唯一契约来源）。
- **美术资源**：界面图标取自 Remix Icon 的 48 个线稿图标，以 path 数据内置在
  `frontend/src/assets/icons/`，由 `components/Icon.vue` 渲染 —— **不新增任何 npm 依赖**、
  不用图标字体。品牌标记与 favicon 为项目原创（图标许可禁止用作品牌标识），许可与出处见
  [`frontend/src/assets/icons/NOTICE.txt`](frontend/src/assets/icons/NOTICE.txt)。
- **后端未实现前的联调**：`frontend/mock/` 提供 Vite 插件模拟全部接口（含真实的进度推进），
  真实后端就绪后删除 `vite.config.js` 中的该插件即可（或 `VITE_USE_MOCK=0` 关闭），业务代码无需改动。

## 十一、短期实现目标

> 现状（2026-10-17 更新）：**演示链路已完整可跑**，训练侧（词向量 / 深度模型 / 训练 / 评估 / 可视化）
> 也已实现并通过小样本端到端验证；真实数据获取/标注与 `/api/videos` 真实爬取已在第二轮补齐。
> 剩下的核心两件事是：真实数据规模达标、真实模型接入推理。

### 已完成（2026-10-17）

| # | 目标 | 完成情况 |
| --- | --- | --- |
| 1 | **模型骨架落地**：`src/models` 的 TextCNN / BiLSTM-Attention，以及 `src/training/` 训练循环 | ✅ 两个模型均能前向出 logits（BiLSTM 另返回注意力权重，`<pad>` 权重为 0）；`python -m src.training.cli --model textcnn` 可在小样本上训练并保存 `best.pt` |
| 2 | **训练与评估闭环**：早停 + 检查点 + 日志；Accuracy / Precision / Recall / F1 / 混淆矩阵 / 错误分析 | ✅ `outputs/logs/train_*.json` 逐轮 loss/acc；`outputs/stats/metrics_<模型>.json`、`model_comparison.csv`、`outputs/predictions/error_cases_<模型>.csv` 均产出；安装 torch 且训练出检查点后 `GET /api/models` 会读真实数值 |
| 6 | **可视化出图**：训练曲线、混淆矩阵热力图、正负词云、Attention 热力图 | ✅ 训练 CLI 默认自动写入 `outputs/figures/`（曲线同时保留按模型命名的版本），见 `tests/test_visualization.py` |
| 7 | **爬虫审查与改进**：审查 PR #2 提交的 988 行爬虫，补齐反爬稳健性 / 标准化 / robots / 来源登记 / 可复用接口 / 效率基准 | ✅ 拆为 10 个实现模块 + 统一 CLI；爬虫测试 79 条（含大规模采集）；本机真实联网与离线 mock 效率见 [第十二节](#十二爬虫模块改进与实测效率)；审查记录见 [`docs/爬虫代码审查与改进-2026-10-17.md`](docs/爬虫代码审查与改进-2026-10-17.md) |
| 8 | **第二轮全面改进**：公开数据集获取 / 标注工作流 / `/api/videos` 真实爬取 / 长跑稳健性 / 可重复基准 | ✅ 3 个并行子代理交付；全量测试全过；详见 [`docs/第二轮全面改进-2026-10-17.md`](docs/第二轮全面改进-2026-10-17.md) |
| 9 | **真实数据规模达标**：公开语料下载 → 统一格式 → 合并均衡 → train 预处理 | ✅ 实测 `waimai_10k` + `ChnSentiCorp` 合并均衡 **18,644 条**（正负各 9,322），预处理 `course_requirements_met=true`：train 13,029 / val 1,861 / test 3,723；命令见第四节，数据文件不入库 |

### 待完成

| # | 目标 | 为什么优先 | 完成标准（验收方式） | 依赖 |
| --- | --- | --- | --- | --- |
| 4 | **真实模型接入推理**：实现 `src/inference/service.py` 的 `load_model` 与 `predict_text` | 一处改动即可让**单条/批量预测、视频任务、数据项目**三处的「规则占位」标识全部变成模型名 | 页面上的「规则占位」标识消失；`POST /api/predict` 返回的 `method` 变成模型名；`/api/models` 显示 `ready=true` | 2、9 |
| 5 | **上传接真实爬取**：`/api/videos` 已在 `VIDEO_REAL_CRAWL=1` 时调用真实爬虫；`src/artifacts` 的「采集评论」阶段仍待接入 | 打通「输入 BV 号 → 自动爬取 → 出结果」与「上传数据项目」两条链路 | 页面视频任务可显示真实评论；上传项目不依赖手工先爬 | 爬虫与视频链路已具备，artifacts 待接 |

### 同时要清掉的合规/质量问题

| 事项 | 说明 | 位置 |
| --- | --- | --- |
| ~~**爬虫 robots 校验与来源登记**~~ | ✅ 2026-10-17 已解决：`src/crawler/robots.py` + `sources.py`，采集时自动校验并写入 `data/raw/来源说明.md`；API 主机 `Disallow: /` 的处理策略见第十二节 | Issue [#4](https://github.com/tianshiemo7/comment-sentiment-analysis/issues/4)（可关闭） |
| **预处理模块 4 项必修** | 英文否定被清洗摧毁、`remove_html` 吞正文、manifest 不记达标判决、标签空间二/三分类口径冲突 | Issue [#3](https://github.com/tianshiemo7/comment-sentiment-analysis/issues/3) |

### 明确不在短期范围内

- BERT 微调（加分项，等真实模型接入后视时间决定）；
- 多 worker 部署与外部队列（当前单进程，课程演示足够）；
- `analyze` 的 `train` 模式、Attention 权重的交互式可视化等增强项。

## 十二、爬虫模块改进与实测效率

> 审查对象：PR [#2](https://github.com/tianshiemo7/comment-sentiment-analysis/pull/2)
> 「添加B站评论爬虫模块」（提交 `7533862`，988 行单文件脚本）。
> 完整审查记录见 [`docs/爬虫代码审查与改进-2026-10-17.md`](docs/爬虫代码审查与改进-2026-10-17.md)；
> 模块说明见 [`src/crawler/README.md`](src/crawler/README.md)。

### 1. 审查结论

提交版本是一个**能跑通的可用原型**：WBI 签名、分页游标、楼中楼、rpid 去重、断点续爬、
BV 号解析均实现正确，作为第一次爬虫提交质量合格。但它更接近一次性脚本，
在五个方面存在缺口：

| 维度 | 提交版本的问题 | 后果 |
| --- | --- | --- |
| 合规 | 无 robots 校验、无来源登记 | 与课程验收要求及 README 宣称不一致（Issue #4） |
| 反爬稳健性 | 401/403/412/429 直接停止；WBI 密钥永不过期；固定 sleep 无抖动；忽略 `Retry-After` | 一次风控就中断，无法自动恢复 |
| 数据质量 | 评论原文直接落盘，仅 `strip()` | HTML 实体 / 零宽字符 / 纯标点进入训练集 |
| 可复用性 | 988 行单文件、`print` 为主、无导入 API | 其它模块（`src/videos` / `src/artifacts`）无法直接接入 |
| 可观测性 | 无请求数 / 重试 / 延迟 / 字节数指标，无测试 | 无法回答「采集效率如何、失败在哪」 |

### 2. 改进内容

| 方向 | 具体改动 |
| --- | --- |
| **反爬稳健性** | ① `RateLimiter`：最小间隔 + 30% 抖动 + 风控后自适应降速/恢复；② `transport.py`：连接池复用、超时、5xx/429 指数退避、尊重 `Retry-After`；③ HTTP 412/403 与业务码 -352/-509/-799 → 冷却 + 刷新 WBI + 有限重试；④ WBI 密钥 TTL 缓存 + 强制刷新；⑤ 可选 buvid3/4（默认关闭，原因见第 6 节）；⑥ 重试耗尽才 `CrawlStopped`，已落盘数据与断点保留 |
| **正规化** | `normalize.py`：HTML 实体还原 → 逐字符 NFKC（全角字母数字转半角、**保留中文标点**）→ 统一换行 → 删除零宽/控制字符 → 空白归一；空文本 / 纯标点过滤；emoji 与 `[笑哭]` 保留；字段类型统一（rpid 字符串、likes/rcount int、UTC ISO 时间） |
| **接口适配** | 拆分为 `bilibili / spider / transport / rate_limit / robots / normalize / sources / errors`；提供 `BiliClient` / `crawl_video` / `crawl_bilibili` / `fetch_video_comments` 可导入 API；`crawl_bilibili` 返回带效率指标的 `CrawlReport.as_dict()`；CSV 列名与旧版完全兼容，`src/preprocessing` 列名映射零改动 |
| **合规闭环** | `robots.py` 按主机缓存校验（404 允许、异常 fail-closed）；视频页 robots 允许；`api.bilibili.com/robots.txt` 当前为 `Disallow: /`，默认 `warn`（日志告警 + 来源说明记录 + 匿名低速），`BILI_API_ROBOTS_POLICY=enforce` 可强制拒绝；每次采集自动写 `data/raw/来源说明.md` + `sources.json` |
| **可观测性 / 测试** | 请求数、重试、延迟 avg/p50/p95/max、响应字节、评论吞吐、风控恢复次数；第一轮 60 条离线单测，第二轮 132 条，大规模补强后 **141 条**（爬虫 79 / 数据与标注 47 / 视频真实爬取 15）；`scripts/bench_crawler.py` 提供离线 mock（默认重复 3 次 + RSS）与真实联网两种基准 |

### 3. 实测环境与方法

- **时间**：2026-10-17 04:34 UTC（北京时间 12:34）
- **机器**：Windows 11（10.0.26200），Python 3.13.9，18 核 CPU，单进程串行
- **网络**：本机家宽直连 B 站（未使用代理）
- **命令**：
  - 真实联网：`python scripts/bench_crawler.py live --bvid BV1GJ411x7h7 --max-pages 2 --max-items 60 --delay 2.5`
  - 离线 mock：`python scripts/bench_crawler.py offline --pages 5 --per-page 20 --include-replies --risk-mode 412`
- **口径**：`业务请求数` 不含 robots.txt（单独计数）；`总耗时` 为进程内 wall clock；延迟为单次 HTTP 往返；
  `评论吞吐` 包含保守限速等待，**反映合规采集速度而非客户端性能上限**。
- 原始 JSON 写入 `outputs/benchmarks/`（gitignored，避免把一次性测量当产物提交），本节保留数值与命令以便复现；
- 第二轮起离线基准默认 `--repeat 3` 取中位数并记录 RSS，联网基准仍为单次（避免对站点重复施压）。

### 4. 真实联网轻量实测

| 指标 | 数值 |
| --- | --- |
| 目标 | `BV1GJ411x7h7`（站点评论总数 237,738，匿名可见子集） |
| 采集配置 | 2 页一级评论，不采楼中楼，最多 60 条，2.5 秒/请求 |
| 总耗时 | **9.306 s** |
| 业务请求数 / robots 拉取 | 4 / 2 |
| 采集评论数 | **39**（2 页） |
| 评论吞吐 | **4.19 条/s** |
| 请求延迟 avg / p50 / p95 / max | **159.65 / 140.92 / 325.30 / 325.30 ms** |
| 重试 / 风控恢复 | 0 / 0 |
| 限速等待合计 | 7.60 s（占总耗时约 82%） |
| 响应字节数 | 215,791 B |
| 进程 RSS（前 → 后） | 29.59 → 42.06 MB |

**解读**：耗时主要来自**主动限速**（7.60s），不是客户端瓶颈；单请求客户端开销约 160ms，
其中包含 DNS/TLS 复用后的往返与接口处理。旧版对同一视频首页采集 19 条、3 次请求
（无 robots、无指标、风控即停）；新版首页同样 19 条，额外完成 2 次 robots 判定、
0 重试，并输出完整指标与来源登记 —— **有效吞吐与旧版相当，稳健性与可解释性提升**。

### 5. 离线 mock 基准（排除网络与限速，3 次重复取中位数）

本地 mock 服务完整模拟 nav / view / WBI 校验 / 分页 / 楼中楼，并注入 1 次 HTTP 412 风控：

| 指标 | 数值 |
| --- | --- |
| 总耗时（3 次中位数） | **0.480 s**（min 0.382 / max 0.484） |
| 业务请求数 | 29（5 页 × 20 条 + 楼中楼 + 风控恢复请求） |
| 采集评论数 | **135** |
| 评论吞吐 | **281.42 条/s**（min 278.67 / max 353.56） |
| 请求吞吐 | 60.45 次/s（min 59.86 / max 75.95） |
| 单请求平均耗时 | 16.542 ms（min 13.166 / max 16.705） |
| 请求延迟 avg / p50 / p95 / max | 14.48 / 14.11 / 28.48 / 28.95 ms |
| WBI 校验失败 | 0 |
| 风控触发 / 恢复 | **1 / 1（自动恢复，任务继续）** |
| 文本标准化 | **104,061 条/s** |
| CSV 写入 | **242,910 行/s** |
| 限速模拟（500 次请求） | 间隔 2.500~3.248 s，均值 2.868 s，358 种不同间隔（抖动生效） |
| 进程 RSS（前 → 后） | 43.49 → 43.60 MB |

**解读**：客户端层开销约 15ms/请求，标准化与 CSV 写入均不是瓶颈；注入 412 后
任务自动恢复并完成全部 135 条，验证了「风控 → 刷新 → 重试 → 继续」链路。
离线基准 `delay=0` 仅用于测客户端开销，**实际采集 CLI 强制 `--delay ≥ 2`**。

### 6. 实测发现（反爬相关）

1. **buvid3 反而收紧匿名评论配额**：同一接口、同一 UA，不带 buvid3 时首页返回
   **19 条且可继续翻页**；带上 buvid3/buvid4 后只返回 **3 条且 `is_end=true`**。
   因此新版默认**不**自动初始化 buvid，仅保留 `BILI_INIT_BUVID=1` / `--init-buvid`
   显式开关（需要设备指纹的接口可自行开启）。
2. **robots.txt 的 Cookie 污染**：用同一个 `requests.Session` 拉取
   `www.bilibili.com/robots.txt` 会收到 `buvid3` cookie，随后评论配额被限制为 3 条。
   新版为 robots 检查使用**独立会话**，避免合规检查改变业务会话状态。
3. **API 主机 robots 为 `Disallow: /`**：`api.bilibili.com/robots.txt` 当前禁止通用抓取。
   本项目把它视为「视频页数据通道」，默认 `warn` 模式并保持匿名、低速、小规模；
   如要求严格禁止，设置 `BILI_API_ROBOTS_POLICY=enforce` 后采集会直接停止。
4. **匿名可见评论有限且不稳定**：该视频站点评论 237,738 条；匿名 2 页约 39 条，但继续翻页
   实测可到 **200+ 条/11 页仍未结束**（不同视频、时段和风控策略下波动很大）。
   完整规模采集仍建议配置登录态，并用 `--require-login` 在未登录时直接失败。

### 7. 复现命令与验证

```bash
# 单元测试（离线，不访问真实站点）
python -m pytest tests/test_crawler.py -q                              # 79 passed
python -m pytest tests/test_crawler_datasets.py tests/test_crawler_labeling.py -q  # 47 passed
python -m pytest tests/test_videos_real_crawl.py -q                    # 15 passed

# 离线效率基准（本地 mock，含 WBI 校验 / 风控恢复；默认重复 3 次取中位数）
python scripts/bench_crawler.py offline --pages 5 --per-page 20 --include-replies --repeat 3

# 真实联网轻量测试（请保持保守频率）
python scripts/bench_crawler.py live --bvid BV1GJ411x7h7 --max-pages 2

# 采集 + 来源登记 + 下游清洗
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 1
python -m src.preprocessing --input data/raw/BV1GJ411x7h7_comments.csv --mode clean
```

下游交接实测：clean 模式 **accepted 38 / excluded 1**，列名映射（`bvid→video_id`、
`rpid→comment_id`）无需改动；`tests/test_preprocessing.py:test_bilibili_crawler_schema_*`
继续通过。

### 8. 第二轮全面改进（2026-10-17 下午，3 个子代理并行）

第一轮解决的是「爬虫本身是否合规、稳健、可复用」；第二轮针对第一轮评估出的三个真实短板
（数据规模/标注、`/api/videos` 未接真实爬取、长跑边界条件）并行改进：

| 子任务 | 交付 | 验收 |
| --- | --- | --- |
| **A. 数据获取 + 标注闭环** | `src/crawler/datasets.py`（4 个公开中文语料注册表；流式下载 + 可选 SHA256 + `.part` 原子改名 + 来源登记；`import_labeled_csv` 统一 `text,label`；`merge_balance` 合并均衡）；`src/crawler/labeling.py`（分层确定性抽样 + manifest；标注合并校验标签/重复/冲突/缺失） | 47 条离线测试（本地 http.server 模拟下载，checksum 成功/失败、404、标签映射、抽样确定性、合并校验、类别均衡） |
| **B. 爬虫长跑稳健性** | BV 输入去重；楼中楼分页不再写死 20（`page.size/count` 自适应）；`sub_done` 断点（恢复时跳过已处理根评论）；`--max-seconds` 时间预算与 `timed_out` | 70 条爬虫测试（原 60 + 新 10） |
| **C. `/api/videos` 接真实爬取** | `src/videos/crawler_adapter.py`；`PipelineConfig.real_crawl` 与 6 个 `VIDEO_CRAWL_*` 环境变量；采集失败落 `failed` 不卡 `crawling`；真实评论进入 detail（`placeholder=false`，情感仍 `rule_placeholder`） | 15 条离线测试（默认占位不变、真实分支、失败分支、HTTP 契约） |

另外 Lead 集成时补强：
- **安全校验**：数据集 URL 仅允许 `http(s)`，`filename` 拒绝目录穿越（防 `file://` 读取本机文件）；
- **真实 detail 质量**：词云关键词过滤 URL / 域名 / `@提及` / `[B站表情]` 噪声；
- **效率基准**：离线默认 `--repeat 3` 取中位数 + 报告进程 RSS（`psutil` 可选，无则跳过）；
- **真实端到端冒烟**（本机联网，未入库）：`VIDEO_REAL_CRAWL=1` 跑 `BV1GJ411x7h7`，
  1 页采集到 **19 条真实评论**，任务 `completed`，`comment_count=19`、`placeholder=false`、
  detail 标题/词云/趋势来自真实数据，耗时约 26 s（其中真实采集约 8 s，其余为流水线阶段时序）。

**验收结果**：全量 **460 passed**（原 319 + 新增 141），耗时约 107 s；
`python -m src.crawler.datasets --help` / `labeling --help` / `bash -n scripts/get_data.sh`
/ `py_compile src/crawler/*.py src/videos/*.py` 均通过；`git diff --check` 干净。
真实 `/api/videos` 冒烟与 `tests/test_videos_real_crawl.py` 证明默认路径零回归。

> 详细分工、设计、命令输出与风险见
> [`docs/第二轮全面改进-2026-10-17.md`](docs/第二轮全面改进-2026-10-17.md)。

### 9. 真实数据闭环验证（2026-10-17）

除了爬虫效率，本轮还用公开语料跑通了「下载 → 统一 → 合并均衡 → 训练预处理」全链路：

| 步骤 | 命令 / 结果 |
| --- | --- |
| 下载 | `bash scripts/get_data.sh --dataset waimai_10k`、`--dataset chnsenticorp`；自动登记来源 |
| 统一 | `python -m src.crawler.datasets --import-csv ...`：外卖 11,987 条（正 4,000 / 负 7,980）、ChnSentiCorp 7,766 条（正 5,322 / 负 2,443） |
| 合并均衡 | `--merge-balance ... --seed 42`：**18,644 条，正负各 9,322**（按较小类别下采样，跨文件文本去重） |
| 训练预处理 | `python -m src.preprocessing --input data/raw/comments_labeled_balanced.csv`：accepted 18,613 / excluded 31（同文本异标签 13、重复文本 18）；`course_requirements_met=true` |

`outputs/stats/dataset_stats.json` 关键结果：

```json
{"splits": {"train": 13029, "val": 1861, "test": 3723},
 "labels": {"negative": 9305, "positive": 9308},
 "vocab_size": 9308,
 "course_requirements_met": true}
```

> 数据文件体积大且被 `.gitignore` 排除，因此不入库；按上面命令可在联网环境复现。
> 来源说明与 SHA256 已写入 `data/raw/来源说明.md` / `sources.json`。
> 该语料是「酒店 + 外卖」跨领域混合，训练出的模型跨域效果仍需评估（报告可讨论）。

### 10. 大规模采集能力（正确性优先，速度次要）

面向「上万条 BV、反复跑到全部完成」的场景，补齐了以下能力（操作手册见
[`docs/大规模采集运行手册.md`](docs/大规模采集运行手册.md)）：

| 能力 | 实现 / 用法 |
| --- | --- |
| 批量清单 | `--bvid-file data/raw/bvids.txt`（一行一个，支持注释/URL/行内备注；自动去重，非法行记错误继续） |
| 已完成零请求跳过 | `--skip-finished`（默认）：断点 `finished=true` 且 CSV 非空时**不请求 robots/meta/评论**，直接恢复标题/条数；`--rescan-finished` 可强制重扫 |
| 不限页数 | `--max-pages 0` / `--max-sub-pages 0` 表示翻到接口自然结束（内部硬上限防死循环） |
| 登录态 | `BILI_SESSDATA` 或完整 `BILI_COOKIE`；`--check-login` 只检查并退出；`--require-login` 未登录直接失败（匿名可见量随接口策略波动，实测 3~200+ 条，完整规模建议登录） |
| 断点续采 | 页级 offset、`sub_done` 楼中楼断点、`timed_out` 时间预算；中断/风控后重跑同一命令即可 |
| 批量来源登记 | 批量结束只写**一条汇总来源**，避免逐视频重写 sources.json/Markdown 的 O(N²) 磁盘开销 |
| 流式合并导出 | `--merge-output data/raw/all_comments.csv`（可只合并不采集；默认不占内存，`--merge-dedupe` 可选按 rpid 去重） |
| 长跑日志 | `--log-file outputs/logs/crawl_batch.log`，每页进度 + 每 100 视频汇总；`--quiet` 只留告警/汇总 |
| 批量报告 | `--json` 输出 `video_count / completed_count / skipped_count / failed_count / total_saved / metrics` |

推荐命令：

```bash
python -m src.crawler.bilibili \
  --bvid-file data/raw/bvids.txt \
  --max-pages 0 --max-sub-pages 0 --include-replies \
  --require-login --delay 2.5 \
  --out-dir data/raw \
  --log-file outputs/logs/crawl_batch.log \
  --merge-output data/raw/all_comments.csv
```

离线规模验证：

- 30 个视频 × 3 页 × 20 条 = **1,800 条**全部落盘，流式合并行数一致；
- 500 个视频 × 3 页 × 20 条 = **30,000 条 / 500 个 CSV**：离线假客户端采集 3.15 s，
  合并 30,000 行（3.43 MB）16.22 s；随后用「任何网络调用即报错」的客户端重跑清单，
  **500 个视频全部零请求跳过（12.98 s）**；
- 真实联网已实测：`--check-login` 匿名返回未登录、`--bvid-file` + 非法行容错、
  已完成断点零请求跳过（`requests=0`）、`--merge-output` 合并 59 行；
- 测试见 `tests/test_crawler.py` 的 `test_large_batch_scale_and_zero_request_resume` 等。

> 说明：真实登录态下的深层分页/楼中楼全量需要使用者提供 Cookie，本次未用真实账号验证；
> 合规限速下大规模采集耗时以「小时/天」计，详见运行手册的容量估算。

### 11. 结论与局限

- **结论**：提交版本的核心链路正确，但在合规、风控恢复、标准化与可复用性上不足；
  第一轮已补齐这些缺口；第二轮又补上了公开数据获取/标注、`/api/videos` 真实爬取与
  长跑边界条件，并用公开语料把训练数据做到了课程硬指标（18,644 条、正负均衡、
  `course_requirements_met=true`），通过全量测试 + 本机真实联网 + 离线 mock 基准验证。
- **局限**：
  - 匿名账号可见评论量随接口策略波动（实测单视频 3~200+ 条，且未必能到全量），
    大规模采集建议配置 `BILI_SESSDATA`/`BILI_COOKIE`，并用 `--require-login` 校验；
  - 真实登录态下的深层分页/楼中楼全量未用真实账号验证（能力与离线规模测试已具备，
    见[运行手册](docs/大规模采集运行手册.md)）；
  - 公开数据集内置 URL/SHA256 未在本次联网环境逐个验证（测试禁止联网），首次下载前建议
    `bash scripts/get_data.sh --dataset <name>` 复核并记录实际校验和；
  - 真实采集只影响评论数据，情感极性仍是 `predict_rule_based` 规则占位，真实模型未接入；
  - `src/artifacts` 的上传链路仍未接真实爬取（`/api/videos` 已 opt-in 接入）；
  - 单进程串行、单机测量；基准已改为 3 次中位数，但仍受网络波动影响。

## 十三、许可证

本项目代码：[MIT](LICENSE)

### 第三方美术资源

界面图标取自 **[Remix Icon](https://remixicon.com)** v4.9.1（Copyright © 2017–2026 Remix Design），
按 **Remix Icon License v1.0**（2026 年 1 月版）使用：

- 本项目在 Web 界面中将图标用作**功能与装饰元素**，并对其颜色与尺寸做了适配
  （`fill="currentColor"`，图形本身未改动）—— 属该许可第 2.1、2.2 条允许的用途；
- 该许可第 3.3 条**禁止**将图标用作 logo / 商标 / 品牌标识，因此本项目的品牌标记（「情」字方格）
  与 favicon 均为**项目原创**，未使用任何 Remix 图形；
- 第 4.1 条要求品牌图标仅用于指代对应品牌：本项目仅使用 `bilibili-line` 一个 Logos 类图标，
  且只出现在代表 B 站内容的「B站分析」入口上，未用作项目标识，也未暗示任何背书或隶属关系；
- 第 5 条要求随源码附带许可，故完整许可全文与出处记录见
  [`frontend/src/assets/icons/NOTICE.txt`](frontend/src/assets/icons/NOTICE.txt)。

图标子集可复现/更新：`node scripts/gen_icons.mjs <remixicon 包目录>`（详见
[`docs/superpowers/specs/2026-10-16-frontend-remixicon-assets-design.md`](docs/superpowers/specs/2026-10-16-frontend-remixicon-assets-design.md)）。