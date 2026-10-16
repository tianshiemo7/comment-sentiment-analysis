# 网站用户评论情感分析系统

> Website User Comment Sentiment Analysis System

[![CI](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/tianshiemo7/comment-sentiment-analysis/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-%E2%89%A5%203.10-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/Node.js-%E2%89%A5%2018-339933?logo=nodedotjs&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

给定网站用户评论文本（电商评论、影评、App 评论等），自动判断其情感极性（正面 / 负面），
并提供一个可交互的 Web 界面进行单条预测与批量预测。

本仓库为**开发中的课程设计项目**：数据处理管线、前后端演示接口与页面已经实现；
爬虫、词向量、深度模型、训练、评估和可视化仍待完成。
数据处理支持无标签爬虫CSV到待预测CSV的交接，也支持已标注数据到训练集、词表和模型输入数组的制备。

---

## 一、项目结构

```
comment-sentiment-analysis/
├── data/                     数据集（训练集 / 验证集 / 测试集）
│   ├── raw/                  原始公开数据集、爬虫原始产出
│   ├── processed/            清洗分词后的中间数据
│   ├── splits/               划分后的 train / val / test
│   └── sample/               字段格式样例（照此格式准备数据）
├── src/                      Python 主干代码
│   ├── crawler/              ① 爬虫模块
│   ├── preprocessing/        ② 数据清洗 + jieba 分词 + 词表构建
│   ├── embeddings/           ③ 词向量（Word2Vec / GloVe）
│   ├── models/               ④ 模型（CNN / BiLSTM-Attention，BERT 微调预留）
│   ├── training/             ⑤ 训练与验证循环
│   ├── evaluation/           ⑥ 指标评估（Accuracy / P / R / F1 / 混淆矩阵）
│   ├── visualization/        ⑦ 结果可视化（曲线 / 热力图 / 词云 / Attention）
│   └── inference/            ⑧ FastAPI 推理服务（:8000，供前端调用）
├── notebooks/                实验记录与探索性分析（EDA）
├── frontend/                 Node.js 前端展示（Vue 3 + Vite，:3000）
├── models/                   训练好的模型权重存放目录
│   ├── checkpoints/          训练检查点（.pt / .pth）
│   ├── word2vec/             词向量文件
│   └── bert/                 BERT 微调权重
├── outputs/                  产物输出
│   ├── figures/              图表（曲线、混淆矩阵、词云、Attention）
│   ├── logs/                 训练日志
│   ├── predictions/          批量预测导出结果
│   └── stats/                数据统计（词表规模、平均长度、标签分布）
├── docs/                     文档与课程报告
├── scripts/                  一键脚本（取数 / 划分 / 训练 / 启动服务）
├── tests/                    测试
└── requirements.txt          Python 依赖
```

## 二、需求对照表

| 课程要求 | 对应位置 | 状态 |
| --- | --- | --- |
| 数据集：训练 ≥ 8000、测试 ≥ 2000、正负均衡 | `data/` `data/sample/` | 骨架就绪，待放数据 |
| 数据来源：公开数据集或爬虫（遵守 robots，注明来源与采集时间） | `src/crawler/` | 骨架就绪 |
| 划分 7 : 1 : 2 | `src/preprocessing/split.py` | 已实现 分层或按视频分组 |
| 中文 jieba 分词、英文分词小写化；去停用词/URL/HTML/特殊符号 | `src/preprocessing/` | 已实现 保护否定词与表情信息 |
| 统一 max_len（如 200）截断补齐 | `src/preprocessing/processor.py` | 已实现 同时输出有效长度及掩码 |
| 词表构建 min_freq ≥ 3，统计词表规模/平均长度/标签分布 | `src/preprocessing/` → `outputs/stats/` | 已实现 词表仅使用训练集 |
| 词向量 Word2Vec / GloVe | `src/embeddings/` | 骨架就绪 |
| 两种深度模型搭建、训练与对比 | `src/models/` `src/training/` | 骨架就绪 |
| 指标 Accuracy / Precision / Recall / F1 / 混淆矩阵 | `src/evaluation/` | 骨架就绪 |
| 损失 / 准确率曲线、混淆矩阵热力图、正负词云、Attention 可视化 | `src/visualization/` → `outputs/figures/` | 骨架就绪 |
| 可运行入口：CLI 或 Web 界面 | `src/inference/` + `frontend/` | 骨架就绪 |
| 输入一条评论 → 情感极性与置信度 | `POST /api/predict` | 骨架就绪 |
| 批量预测 .csv/.xlsx 并导出 | `POST /api/predict/batch` | 骨架就绪 |
| requirements.txt 与 README 说明环境/数据路径/命令 | `requirements.txt` `README.md` | ✅ |
| 课程设计报告（≥ 5000 字，8 章结构） | `docs/课程设计报告大纲.md` | 大纲就绪 |

## 三、环境要求

| 组件 | 版本 | 用途 |
| --- | --- | --- |
| Python | ≥ 3.10（开发环境 3.13 已通过） | 爬虫、预处理、训练、推理服务 |
| Node.js | ≥ 18（开发环境 24 已通过） | 前端开发与构建 |
| npm | 随 Node 附带 | 前端依赖管理 |

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

# 以下仍待实现
bash scripts/get_data.sh      # 采集数据
bash scripts/train.sh         # 词向量 + 深度模型训练 + 评估 + 出图
```

无标签模式输出 `data/processed/comments_cleaned.csv`，保留原文、清洗文本、分词及采集元数据，不生成情感标签、训练词表或训练划分。
默认 auto 模式会识别无标签输入；混合标签文件需明确选择 clean 或 train。
下一步预测需接入训练好的模型，并使用模型配套资源处理 text_raw。

有标签训练模式输出包括 `data/splits/{train,val,test}.csv` 与同名 `.npz`、训练词表、预处理资源快照、标签映射、
输入和产物哈希、排除记录及统计。模型输入数组为 `input_ids`、`attention_mask`、`lengths` 和 `labels`，
标签统一为 `negative=0、positive=1`。后续预测应加载这套资源，不能重新建立词表。
详细字段、参数、验收策略与下游调用示例见 [数据处理说明](src/preprocessing/README.md)。

## 五、当前实现状态（骨架阶段）

服务与界面已可运行，但**预测结果为占位实现，不是训练出来的模型**：

| 部分 | 状态 |
| --- | --- |
| FastAPI 推理服务（5 个接口） | ✅ 可运行，契约已锁定 |
| 批量上传解析与结果导出下载 | ✅ 可用（csv / xlsx，导出 UTF-8 BOM CSV） |
| 前端四个页面 | ✅ 可运行（`npm run dev`），单条/批量/模型对比会真实调用后端 |
| 情感预测逻辑 | ⚠️ **规则占位**：`src/inference/service.py:predict_rule_based`，内置 140 词情感词表（含程度副词与否定词翻转）。它能对明显正面/负面的评论给出方向正确的结果，用于打通链路，**不是深度学习模型** |
| 数据读写 / 清洗分词 / 去重冲突审计 / 划分 / 词表 / 编码 / 统计 | ✅ 已实现，含训练与推理共用资源及防泄漏校验 |
| 爬虫 / 词向量 / 两个深度模型 / 训练 / 评估 / 可视化 | ⛔ 仍为骨架与 TODO，见 `docs/开发路线图.md` |

替换为真实模型的入口只有一个：实现 `src/inference/service.py` 的 `load_model` 与 `predict_text`，
模型加载成功（返回非 `None`）后即走真实推理分支，接口契约不变。

## 六、前后端接口契约

契约已锁定，前后端按此实现，任何一侧不得单方面改名。

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float}}` |
| POST | `/api/predict/batch` | multipart 文件（字段名 `file`；支持 `.csv` / `.txt` / `.tsv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | `text/csv` 附件（批量结果导出，UTF-8 BOM，Excel 可直接打开） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` |

说明：

- `label` 取值 `positive` / `negative`，`probs` / `confidence` 均为 **0~1** 的小数；
- 批量接口把结果写入 `outputs/predictions/batch_prediction_<时间戳>_<id>.csv`，`export_url` 即上表第 4 行的下载地址；
- 上传文件首行为表头，文本列自动识别顺序：`text` → `comment` → `content` → `评论` → 第一列；
- 其他后缀（如 `.xls`、无后缀）返回 400；前端文件选择框当前只开放 `.csv` / `.xlsx`；
- 非法文件名返回 400、不存在的文件返回 404（下载路径已做目录穿越防护）。

## 七、数据格式约定

单条评论文本字段统一命名为 `text`，情感标签字段为 `label`（取值 `positive` / `negative`）。

`data/sample/train_sample.csv`：

```csv
text,label
这个商品质量很好，物流也快，非常满意。,positive
```

`data/sample/test_sample.csv`：

```csv
id,text
1,东西一般般，没有想象中那么好。
```

## 八、目录职责

每个源码包内附 `README.md`，说明该模块的职责、输入输出、待实现内容与对应需求条目。
详见 [`docs/开发路线图.md`](docs/开发路线图.md)。

## 九、开发团队与分工

见 [`docs/课程设计报告大纲.md`](docs/课程设计报告大纲.md) 第 8 章，待填写。

## 十、许可证

[MIT](LICENSE)
