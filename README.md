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
| B 站评论爬虫 `src/crawler/bilibili.py` | ✅ 已实现（WBI 签名、断点续爬、去重）· ⚠️ robots 校验与来源登记**尚未接入** |
| 数据处理管线 `src/preprocessing/` | ✅ 已实现（清洗 / 分词 / 划分 / 词表 / 编码 / 统计） |
| 前后端页面与接口 `frontend/` + `src/inference/` + `src/videos/` + `src/artifacts/` | ✅ 已实现 |
| 数据项目（右上传爬虫数据 → clean 管线 → 三张表 + 逐条评论情感） | ✅ 已实现（`src/artifacts/`）· ⚠️ 情感为规则占位 |
| 单独评论（单条预测结果存入左栏②段，无图表） | ✅ 已实现 |
| `/api/videos` 链路中的爬取与情感推理 | ⚠️ **占位数据**（未接真实爬虫与模型，见 `src/videos/placeholder.py`） |
| 词向量 / 深度模型 / 训练 / 评估 / 可视化 | ✅ 已实现（Word2Vec、TextCNN、BiLSTM-Attention、训练/早停/日志、指标与四项出图；需装 torch，见第六节） |

数据处理支持无标签爬虫 CSV 到待预测 CSV 的交接，也支持已标注数据到训练集、词表和模型输入数组的制备。
B 站视频分析链路（输入 BV 号 → 状态跟踪 → 图表词云详情）的前后端与接口契约已落地，
接入真实爬虫与模型后接口契约不变（见 `docs/API契约.md`）。

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
| 数据来源：公开数据集或爬虫（遵守 robots，注明来源与采集时间） | `src/crawler/bilibili.py` | 爬虫已实现；robots 校验与来源登记待接入（见 Issue #4） |
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

## 五、当前实现状态（开发中）

服务与界面已可运行，但**预测结果为占位实现，不是训练出来的模型**：

| 部分 | 状态 |
| --- | --- |
| FastAPI 推理服务（单条/批量预测与模型列表） | ✅ 可运行，契约已锁定 |
| 批量上传解析与结果导出下载 | ✅ 可用（csv / txt / tsv / xlsx，导出 UTF-8 BOM CSV） |
| 前端页面（B站分析主界面 + 单条预测 / 批量预测 / 实验可视化 / 模型对比） | ✅ 可运行（`npm run dev`），真实调用后端接口 |
| B 站分析主流程接口 `/api/videos*` | ✅ 已实现（`src/videos/`），任务状态机 + 详情接口；爬取与情感推理为占位数据 |
| 情感预测逻辑 | ⚠️ **规则占位**：`src/inference/service.py:predict_rule_based`，内置 140 词情感词表（含程度副词与否定词翻转）。它能对明显正面/负面的评论给出方向正确的结果，用于打通链路，**不是深度学习模型** |
| 数据读写 / 清洗分词 / 去重冲突审计 / 划分 / 词表 / 编码 / 统计 | ✅ 已实现，含训练与推理共用资源及防泄漏校验 |
| B 站爬虫 `src/crawler/bilibili.py` | ✅ 可采集（WBI 签名 / 断点续爬 / 去重）· ⚠️ robots 校验与来源登记待接入 |
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
> 也已实现并通过小样本端到端验证；剩下的三件事是：真实数据达标、真实模型接入推理、上传接真实爬取。
> 每项都给了「完成标准」以便自查。

### 已完成（2026-10-17）

| # | 目标 | 完成情况 |
| --- | --- | --- |
| 1 | **模型骨架落地**：`src/models` 的 TextCNN / BiLSTM-Attention，以及 `src/training/` 训练循环 | ✅ 两个模型均能前向出 logits（BiLSTM 另返回注意力权重，`<pad>` 权重为 0）；`python -m src.training.cli --model textcnn` 可在小样本上训练并保存 `best.pt` |
| 2 | **训练与评估闭环**：早停 + 检查点 + 日志；Accuracy / Precision / Recall / F1 / 混淆矩阵 / 错误分析 | ✅ `outputs/logs/train_*.json` 逐轮 loss/acc；`outputs/stats/metrics_<模型>.json`、`model_comparison.csv`、`outputs/predictions/error_cases_<模型>.csv` 均产出；安装 torch 且训练出检查点后 `GET /api/models` 会读真实数值 |
| 6 | **可视化出图**：训练曲线、混淆矩阵热力图、正负词云、Attention 热力图 | ✅ 训练 CLI 默认自动写入 `outputs/figures/`（曲线同时保留按模型命名的版本），见 `tests/test_visualization.py` |

### 待完成

| # | 目标 | 为什么优先 | 完成标准（验收方式） | 依赖 |
| --- | --- | --- | --- | --- |
| 3 | **真实数据达标**：用爬虫采够规模，跑 train 模式管线出 7:1:2 划分 | 课程硬指标：训练 ≥ 8000、测试 ≥ 2000、正负均衡；管线已就绪，缺的是数据 | `python -m src.preprocessing --input <已标注CSV>` 一次通过（**不加** `--allow-small-dataset`），`outputs/stats/dataset_stats.json` 里 `course_requirements_met=true` | 需要有标注语料（公开数据集或人工标注） |
| 4 | **真实模型接入推理**：实现 `src/inference/service.py` 的 `load_model` 与 `predict_text` | 一处改动即可让**单条/批量预测、视频任务、数据项目**三处的「规则占位」标识全部变成模型名 | 页面上的「规则占位」标识消失；`POST /api/predict` 返回的 `method` 变成模型名；`/api/models` 显示 `ready=true` | 2、3 |
| 5 | **上传接真实爬取**：让 `src/artifacts` 的「采集评论」阶段调用 `src/crawler/bilibili.py`，替代"手工先爬再传" | 打通「输入 BV 号 → 自动爬取 → 出结果」的完整体验 | 上传/提交后能自动爬到真实评论；`src/videos/placeholder.py` 与 `src/artifacts` 的占位数据可删 | 爬虫可跑（已具备） |

### 同时要清掉的两个合规/质量问题

| 事项 | 说明 | 位置 |
| --- | --- | --- |
| **爬虫 robots 校验与来源登记** | 课程明确要求「遵守 robots 协议、注明数据来源与采集时间」，目前文档宣称有、代码没有 | Issue [#4](https://github.com/tianshiemo7/comment-sentiment-analysis/issues/4) |
| **预处理模块 4 项必修** | 英文否定被清洗摧毁、`remove_html` 吞正文、manifest 不记达标判决、标签空间二/三分类口径冲突 | Issue [#3](https://github.com/tianshiemo7/comment-sentiment-analysis/issues/3) |

### 明确不在短期范围内

- BERT 微调（加分项，等上面 3 项完成后视时间决定）；
- 多 worker 部署与外部队列（当前单进程，课程演示足够）；
- `analyze` 的 `train` 模式、Attention 权重的交互式可视化等增强项。

## 十二、许可证

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