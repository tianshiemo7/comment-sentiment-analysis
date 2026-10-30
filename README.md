# 网站用户评论情感分析系统

> Website User Comment Sentiment Analysis System

[![CI](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-%E2%89%A5%203.10-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/Node.js-%E2%89%A5%2018-339933?logo=nodedotjs&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

给定网站用户评论文本，自动判断情感极性（正面 / 负面），并提供可交互的 Web 界面。

**主流程（B 站视频评论分析）**：输入 BV 号 → 任务排队 → 采集评论 → 清洗 → 情感推理 → 统计分析 → 前端展示情感占比、情感趋势、正负词云、关键词排行、代表性评论与模型指标。评论采集默认占位数据，可按任务设置或 `VIDEO_REAL_CRAWL=1` 真实联网采集（遵守 robots 与限速）。

**辅助功能**：单条评论预测、批量文件预测（`.csv` / `.xlsx` 导入导出）、上传爬虫数据生成「数据项目」、单独评论收集与展示。

> 情感预测自 **2026-10-30** 起统一使用已训练的真实模型（TextCNN / BiLSTM-Attention），
> 检查点缺失、torch 未安装或权重损坏时才降级为规则占位预测；接口契约不变。
> 完整接口字段见 [`docs/API契约.md`](docs/API契约.md)，前端说明见 [`frontend/README.md`](frontend/README.md)。

## 一、功能状态

| 模块 | 状态 |
| --- | --- |
| 爬虫 `src/crawler/` | ✅ B 站评论采集：WBI 签名 / 限速抖动 / 风控退避 / robots 校验 / 来源登记 / 断点续采 / 大规模清单采集 / 公开数据集下载 / 标注抽样与合并 |
| 预处理 `src/preprocessing/` | ✅ 清洗 / jieba 分词 / 去重与标签冲突审计 / 7:1:2 划分 / 词表 / 编码 / 统计 |
| 训练数据（课程硬指标） | ✅ 公开标注语料审计后 **19,711 条**（train 13,797 / val 1,971 / test 3,943，正面约 47.2%，`course_requirements_met=true`） |
| 词向量 / 模型 / 训练 / 评估 / 可视化 `src/embeddings|models|training|evaluation|visualization/` | ✅ Word2Vec + TextCNN + BiLSTM-Attention；早停 / 检查点 / 指标 / 混淆矩阵 / 错误分析 / 曲线 / 词云 |
| 推理服务 `src/inference/` | ✅ FastAPI：单条 / 批量预测、模型列表、批量导出下载；四个预测入口统一使用真实模型 |
| B 站视频任务 `src/videos/` | ✅ 任务状态机 + 分步流水线（待清洗 / 待分析）+ 真实爬取（opt-in）+ 真实模型情感推理 |
| 数据项目与单独评论 `src/artifacts/` | ✅ 上传爬虫 CSV → clean 管线 → 三张表 + 逐条情感（真实模型）；单独评论集中管理 |
| 前端 `frontend/` | ✅ Vue 3 + Vite + ECharts：B 站分析主界面 / 单条预测 / 批量预测 / 实验可视化 / 模型对比；默认直连真实后端 |
| 真实权重与指标（2026-10-30 CPU 复核） | ✅ TextCNN 测试 Acc **89.60%** / F1 88.76%，BiLSTM-Attention **90.08%** / F1 89.20%（酒店/外卖公开语料，**非 B 站领域指标**） |
| 测试与验收 | ✅ `pytest` **578 passed**；端到端冒烟后端 **9/9** + 前端 **6/6 PASS**；GitHub CI 通过 |

## 二、技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python ≥ 3.10、FastAPI + Uvicorn、Pydantic |
| 模型 | PyTorch ≥ 2.2（CPU 可跑）、gensim（Word2Vec）、jieba（分词）、NumPy |
| 前端 | Node.js ≥ 18、Vue 3、Vite、ECharts + echarts-wordcloud |
| 测试 | pytest、httpx / FastAPI TestClient、GitHub Actions（Python + 前端构建） |
| 可选 | Playwright（仅「人工登录窗口」需要；Windows 已装 Edge 可免装 Chromium） |

## 三、项目结构

```
comment-sentiment-analysis/
├── data/                     数据集与上传数据
│   ├── raw/                  原始公开语料、爬虫原始产出、来源登记
│   ├── processed/            清洗分词后的中间数据（词表 / 预处理快照 / manifest）
│   ├── splits/               train / val / test（CSV + NPZ）
│   ├── uploads/              界面上传的「数据项目」运行期落盘
│   └── sample/               字段格式样例
├── resources/                分词与清洗资源（stopwords / userdict / emoji_map）
├── src/
│   ├── crawler/              爬虫（B 站 / 通用 HTML / 公开数据集 / 标注）
│   ├── preprocessing/        清洗 + 分词 + 划分 + 词表 + 编码 + 统计
│   ├── embeddings/           Word2Vec 训练与词表对齐的 embedding 矩阵
│   ├── models/               TextCNN / BiLSTM-Attention
│   ├── training/             统一训练循环（早停 / 检查点 / 日志）
│   ├── evaluation/           指标 / 混淆矩阵 / 错误分析
│   ├── visualization/        曲线 / 热力图 / 词云
│   ├── inference/            FastAPI 推理服务（:8000）
│   ├── videos/               B 站视频任务（状态机 / 分步流水线 / 设置 / 人工登录）
│   └── artifacts/            数据项目与单独评论
├── frontend/                 Vue 3 前端（:3000，src/ 业务代码 + mock/ 演示层）
├── models/                   词向量与检查点（仓库已按快照方式入库真实权重）
├── outputs/                  图表 / 日志 / 指标 / 错误样例 / 批量导出 / 任务落盘
├── docs/                     接口契约、路线图、验收清单、实验记录、运行手册
├── scripts/                  一键脚本（取数 / 划分 / 训练 / 启动 / 端到端验收 / 图标生成）
├── tests/                    测试
└── requirements.txt          Python 依赖
```

> 为便于课程验收与换机备份，`data/`、`models/`、`outputs/` 下的数据与训练产物已按「一次性快照」提交
> （`.gitignore` 规则未改，之后新增的大文件默认仍不入库）；含 Cookie 的 `outputs/settings.json` **绝不入库**。

## 四、快速开始

### 1. 环境要求

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# 训练与真实模型推理需要 PyTorch（CPU 即可），按 requirements.txt 注释单独安装：
#   pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 2. 启动后端（:8000）

```bash
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload
# 或： bash scripts/serve.sh
```

- 接口文档：<http://localhost:8000/docs>
- 默认模型 `bilstm_attention`；`ACTIVE_MODEL=textcnn` 可切换，`INFERENCE_DEVICE=auto|cuda` 可用 GPU。
- 检查点存在时真实推理，加载失败自动降级规则占位（响应 `method` 为 `rule_placeholder`）。

### 3. 启动前端（:3000）

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
npm run build      # 生产构建，产物 frontend/dist/
```

前端通过 Vite 代理把 `/api`、`/health` 转发到 `http://localhost:8000`，因此两个服务需同时运行。
**默认不加载 mock**，所有请求走真实后端；无后端只想演示界面时用 `VITE_USE_MOCK=1 npm run dev`
（仅 dev 生效，`npm run preview` 与生产构建始终走代理）。

首次使用可点左上角齿轮「设置」配置账号模式与采集默认值；人工登录窗口需可选依赖 Playwright
（未安装时用手动粘贴 Cookie 即可）。Cookie 只保存在服务端 `outputs/settings.json`（已 gitignore），
接口只返回脱敏字段。详见 [`docs/API契约.md`](docs/API契约.md) 与
[`docs/大规模采集运行手册.md`](docs/大规模采集运行手册.md)。

### 4. 数据与训练复现（真实权重口径）

```bash
# ① 固定版本公开语料 → data/processed + data/splits（数据版本 753bc2014c69bdc1）
python -m scripts.prepare_training_data

# ② 词向量（只用训练集拟合）
python -m src.embeddings --train-file data/splits/train.csv --vocab data/processed/vocab.json \
  --out models/word2vec --embed-dim 128 --window 5 --min-count 3 --epochs 10 --workers 1 --seed 42

# ③ 两个模型（CPU 复核口径；GPU 环境把 --device 换成 cuda）
python -m src.training.cli --model textcnn --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
python -m src.training.cli --model bilstm_attention --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
```

也可以用一键脚本：`bash scripts/train.sh`（缺 `data/splits` 时会先跑预处理；`WITH_WORD2VEC=1`
先用词向量初始化；`MODELS=textcnn` 只训一个模型）。训练产物：`models/checkpoints/<模型>/best.pt`、
`outputs/logs/train_*.json`、`outputs/stats/metrics_<模型>.json`、`outputs/figures/*.png`。

无标签爬虫 CSV → 待预测评论（不生成标签/词表）：

```bash
python -m src.preprocessing --input data/raw/BV1UB4y1W7Xu_comments.csv --mode clean
```

数据字段、输出结构与验收策略见 [`data/README.md`](data/README.md) 与
[`src/preprocessing/README.md`](src/preprocessing/README.md)。
注意：`--allow-small-dataset` 的演示数据会覆盖当前正式数据，只用于验证链路。

### 5. 采集评论

```bash
# B 站：默认 2.5 秒/请求、遵守 robots、自动登记来源
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2 --include-replies
# 或统一脚本 / 通用 HTML 采集
bash scripts/get_data.sh BV1GJ411x7h7
python -m src.crawler html --url https://example.com/post --source 示例站点 \
    --selector ".comment-content" --max-items 100
```

大规模清单采集、断点续采、登录态与 Web 分步流程见
[`docs/大规模采集运行手册.md`](docs/大规模采集运行手册.md)；爬虫实现见 [`src/crawler/README.md`](src/crawler/README.md)。
`/api/videos` 的真实爬取是 opt-in：`VIDEO_REAL_CRAWL=1` 或任务级设置 `real_crawl=true` 才联网。

### 6. 端到端验收

```bash
python scripts/e2e_smoke.py              # 后端 9 项契约检查（隔离临时目录）
python scripts/e2e_smoke.py --frontend   # 追加前端代理 + 浏览器渲染验证
```

报告落盘 `outputs/e2e/last_run.json`；脚本只断言契约与结构，不伪造准确率。

## 五、接口一览

| 分组 | 接口 |
| --- | --- |
| 系统 | `GET /health`、`GET /api/models` |
| 单条 / 批量预测 | `POST /api/predict`、`POST /api/predict/batch`、`GET /api/predict/batch/download/{file_id}` |
| 视频任务 | `POST /api/videos`、`GET /api/videos`、`GET /api/videos/{bvid}/detail`、`POST /api/videos/{bvid}/continue`、`POST /api/videos/{bvid}/retry`、`DELETE /api/videos/{bvid}` |
| 设置与账号 | `GET/PUT /api/settings`、`POST /api/crawler/session/check`、`POST /api/account/login/start`、`GET /api/account/login/status`、`POST /api/account/login/cancel` |
| 数据项目 | `POST /api/artifacts/upload`、`GET /api/artifacts`、`GET /api/artifacts/{id}`（`/stages`、`/table`、`/comments`、`/analysis`、`/path`）、`POST /api/artifacts/{id}/open`、`POST /api/artifacts/{id}/analyze`、`DELETE /api/artifacts/{id}` |
| 单独评论 | `POST/GET /api/solo-comments`、`DELETE /api/solo-comments/{id}`、`DELETE /api/solo-comments?ids=` |

字段、示例、错误码与安全约束以 [`docs/API契约.md`](docs/API契约.md) 为唯一来源。要点：

- `label` 取 `positive` / `negative`；`probs` / `confidence` 为 0~1 小数；
- `method` 为情感来源：真实模型名（`textcnn` / `bilstm_attention`）或降级时的 `rule_placeholder`；
- 批量结果落盘 `outputs/predictions/batch_prediction_<时间戳>_<id>.csv`（UTF-8 BOM，Excel 可直接打开）；
- 上传文件首行为表头，文本列自动识别：`text` → `comment` → `content` → `评论` → 第一列；
- 下载接口只允许读取 `outputs/predictions/` 下的 CSV，并已做目录穿越防护。

## 六、数据格式约定

统一字段：文本 `text`、标签 `label`（仅 `positive` / `negative`，固定 `negative=0`、`positive=1`）。

```csv
text,label
这个商品质量很好，物流也快，非常满意。,positive
```

爬虫产出列（也是上传到界面的输入格式）：

```csv
bvid,aid,video_title,rpid,level,root_rpid,parent_rpid,text,likes,reply_count,ctime_utc,collected_at_utc
BV1UB4y1W7Xu,112233,示例标题,123456,1,,,这个教程讲解清晰,12,3,2026-09-01T12:00:00+00:00,2026-10-16T10:00:00+00:00
```

上传「数据项目」时自动做列名映射（`bvid→video_id`、`rpid→comment_id`、`ctime_utc→posted_at`、
`collected_at_utc→collected_at`）。爬虫落盘前会统一标准化：HTML 实体还原、全角字母数字转半角
（保留中文标点）、删除零宽/控制字符、空白归一、纯标点与空文本过滤；emoji 与 B 站表情保留。
每次采集的站点 / URL / 时间 / 条数 / 合规说明自动写入 `data/raw/来源说明.md` 与 `sources.json`。

## 七、数据与实验结果

| 项目 | 数值 |
| --- | --- |
| 数据版本 | `753bc2014c69bdc1`（pipeline 1.1.0） |
| 总样本 | 19,711 条（负面 10,408 / 正面 9,303） |
| 划分 | train 13,797 / val 1,971 / test 3,943（7:1:2，标签分层） |
| 词表 | 9,753 项（含 PAD / UNK，仅用训练集拟合，min_freq=3） |
| 数据来源 | ChnSentiCorp 酒店 + 外卖评论（公开语料，有标签）；B 站评论暂无人工标注 |

| 模型 | 最佳轮次 | 验证 Acc | 测试 Acc | Precision | Recall | F1 | Macro F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TextCNN | 2 | 0.8808 | **0.8960** | 0.9060 | 0.8700 | 0.8876 | 0.8954 |
| BiLSTM-Attention | 3 | 0.8798 | **0.9008** | 0.9181 | 0.8673 | 0.8920 | 0.9002 |

- 混淆矩阵（行真实 / 列预测，`[negative, positive]`）：TextCNN TN 1914 / FP 168 / FN 242 / TP 1619；
  BiLSTM TN 1938 / FP 144 / FN 247 / TP 1614。
- 训练 / 评估细节、权重大小与 SHA256、GPU 历史实验对照见
  [`docs/模型训练与评估-2026-10-30.md`](docs/模型训练与评估-2026-10-30.md)；
  数据处理与审计过程见 [`docs/数据处理验证-2026-10-30.md`](docs/数据处理验证-2026-10-30.md)。
- ⚠️ 上述指标来自**酒店 / 外卖公开语料**，没有 B 站人工标注测试集，**不能宣称 B 站评论准确率**。

## 八、测试与质量

```bash
python -m pytest tests/ -q    # 578 passed（含真实模型推理 31 项 + 视频真实模型 9 项）
```

- 无 torch 环境会自动跳过模型/训练相关用例，其余服务与数据处理测试照常运行；
- 端到端：`scripts/e2e_smoke.py --frontend` → 后端 9/9、前端 6/6 PASS（真实 uvicorn + 真实权重 +
  Vite 代理与无头浏览器渲染）；
- 全新 clone 自 GitHub 后可直接加载真实权重、跑通测试与端到端脚本；
- CI：GitHub Actions 执行 Python 全量测试与前端构建（见顶部 badge）。

## 九、界面

界面分四块：顶部「设置入口 + 账号徽标 + BV 输入框」、左侧可收起任务栏（数据项目 / 单独评论 /
视频任务三段）、右侧详情区，以及「系统设置」「爬虫参数」两个弹窗。

- 视频任务详情：完成后展示情感占比饼图、情感趋势、正负词云、关键词排行、代表性评论与模型指标；
  未完成只展示进度与子步骤，不渲染空图表；
- 分步流水线：采集完成停在「待清洗」、清洗完成停在「待分析」，点按钮或开启自动连续继续；
- 数据项目详情：三张表（初始 / 筛选后 / 排除记录）分页查看 + 可展开逐条情感 + 底部操作按钮；
- 图表与词云由前端渲染，后端只返回词频、分布与趋势数据。

历史界面截图：[系统设置弹窗](docs/images/2026-10-17-settings/settings-dialog.png) ·
[爬虫参数弹窗](docs/images/2026-10-17-settings/crawl-params-dialog.png) ·
[待分析等待态](docs/images/2026-10-17-settings/staged-cleaned.png) ·
[完成后的详情](docs/images/2026-10-17-settings/completed-detail.png)。
前端页面、组件与 mock 层说明见 [`frontend/README.md`](frontend/README.md)。

## 十、已知限制与合规

- **领域限制**：模型训练语料为酒店 / 外卖公开语料，B 站评论的极性结果不能当作该领域准确率；
  B 站人工标注测试集尚未建立。
- **真实爬取是 opt-in**：默认不联网、用确定性占位评论数据（`detail.placeholder=true` 只表示评论数据来源，
  不表示情感来源）；真实采集需 `VIDEO_REAL_CRAWL=1` 或任务设置，失败会落 `failed` 并带原因。
- **合规**：遵守 robots 与限速（默认 2.5 秒/请求，硬性下限 2 秒），不绕过风控、不并发抓取；
  仅用于课程学习与研究。
- **凭据安全**：B 站 Cookie 只保存在服务端 `outputs/settings.json`（已被 gitignore），接口只回传脱敏字段，
  不随任务提交；人工登录窗口不读取、不记录账号密码。
- **部署**：当前单进程串行（课程演示足够），多 worker / 外部队列不在范围内。
- 其它历史审查与增强项见 [`docs/验收检查清单.md`](docs/验收检查清单.md)。

## 十一、文档索引

| 文档 | 内容 |
| --- | --- |
| [`docs/API契约.md`](docs/API契约.md) | 前后端唯一接口契约（字段 / 示例 / 错误码 / 设置与分步流水线） |
| [`docs/开发路线图.md`](docs/开发路线图.md) | 分阶段实现路线与当前状态 |
| [`docs/验收检查清单.md`](docs/验收检查清单.md) | 课程验收逐项自查 |
| [`docs/模型训练与评估-2026-10-30.md`](docs/模型训练与评估-2026-10-30.md) | 数据版本、训练命令、指标、混淆矩阵、权重哈希、GPU 历史实验 |
| [`docs/数据处理验证-2026-10-30.md`](docs/数据处理验证-2026-10-30.md) | 预处理修复、公开语料复现、无标签评论交接 |
| [`docs/大规模采集运行手册.md`](docs/大规模采集运行手册.md) | 爬虫清单采集 / 登录态 / 断点续采 / Web 分步流程 |
| [`docs/爬虫代码审查与改进-2026-10-17.md`](docs/爬虫代码审查与改进-2026-10-17.md) | 爬虫审查结论、改进点与实测效率 |
| [`docs/第二轮全面改进-2026-10-17.md`](docs/第二轮全面改进-2026-10-17.md) | 第二轮改进与真实数据闭环记录 |
| [`docs/数据流通审计与训练链路-2026-10-17.md`](docs/数据流通审计与训练链路-2026-10-17.md) | 数据流与训练链路审计 |
| [`docs/课程设计报告大纲.md`](docs/课程设计报告大纲.md) | 课程报告结构与分工 |

各源码包都有自己的 `README.md`（`src/*/README.md`、`data/README.md`、`models/README.md`、
`outputs/README.md`、`frontend/README.md`），说明该部分职责、输入输出与复现方式。

## 十二、剩余工作

1. **课程设计报告正文**：按 [`docs/课程设计报告大纲.md`](docs/课程设计报告大纲.md) 撰写 ≥ 5000 字、8 章。
2. **B 站人工标注测试集**：补充独立人工标注并评估领域迁移，才能报告 B 站领域指标。
3. **artifacts 上传链路接真实爬取**：`src/artifacts` 的「采集评论」阶段目前仍依赖用户先上传 CSV，
   `/api/videos` 已支持真实爬取，可作为后续增强。

## 十三、开发团队与分工

见 [`docs/课程设计报告大纲.md`](docs/课程设计报告大纲.md) 第 8 章（待填写）。

## 十四、许可证

本项目代码：[MIT](LICENSE)。

界面图标取自 [Remix Icon](https://remixicon.com) v4.9.1（Remix Icon License v1.0），
以 path 数据内置在 `frontend/src/assets/icons/`，**不新增 npm 依赖**、不用图标字体；
图标仅作功能/装饰元素，favicon 为项目原创，左上角齿轮是功能图标、不作品牌标识。
完整许可全文、出处与品牌图标使用说明见
[`frontend/src/assets/icons/NOTICE.txt`](frontend/src/assets/icons/NOTICE.txt)；
图标子集可由 `node scripts/gen_icons.mjs <remixicon 包目录>` 复现。
