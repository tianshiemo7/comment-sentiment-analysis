# src/inference —— ⑧ 推理服务模块（FastAPI）

> 对应需求：可运行入口（CLI 或 Web 界面）、输入一条评论 → 情感极性与置信度、
> 批量预测 .csv/.xlsx 并导出。路线图阶段五；验收项 `docs/验收检查清单.md` 六、系统演示。

## 接口契约（锁定，不得改名）

### 既有 5 个（保持不变）

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float},"method":str}` |
| POST | `/api/predict/batch` | multipart 文件（字段名 `file`；支持 `.csv` / `.txt` / `.tsv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | CSV 文件（`Content-Type: text/csv`，`Content-Disposition: attachment`） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` |

契约由 Lead 锁定：原 4 个业务路由**不得改名**，`total` / `results` 字段保持不变
（`/docs`、`/openapi.json` 为 FastAPI 自带文档路由）。
批量接口现在会把结果落盘并返回非 null 的 `export_url`（契约扩展已由 Lead 锁定），
下载路由只允许读取 `outputs/predictions/` 下的 CSV，并显式防目录穿越。

`POST /api/predict` 响应里的 **`method` 是新增字段**（纯增量，`text` / `label` /
`confidence` / `probs` 四个既有字段的名字与语义不变）：由
`service.resolve_method(model, model_name)` 计算——真实检查点加载成功时为模型名
（`textcnn` / `bilstm_attention`），检查点缺失 / torch 缺失降级时才是 `rule_placeholder`。
前端把预测结果保存进「单独评论」时直接转发该字段，不会把模型结果错标成「规则占位」。

### 新增：`/api/videos` 系列（B 站视频评论分析，契约见 `docs/API契约.md`）

实现全部在 `src/videos/` 包里，本文件只挂路由（HTTP 语义）+ lifespan：

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| POST | `/api/videos` | `{"bvid": "BV1xx411c7mD"}` | `{"bvid":str,"status":"queued","progress":0,"created_at":str}`；非法 BV → 400、重复在处理中 → 409 |
| GET | `/api/videos` | — | `{"total":int,"videos":[{bvid,title,owner,cover_url,status,progress,message,phases,comment_count,created_at,updated_at}]}`（`created_at` 倒序） |
| GET | `/api/videos/{bvid}/detail` | — | `{video,analysis,sentiment,trend,keywords,model,top_comments,error,placeholder}`；未知 bvid → 404；**未完成也返回 200** |
| POST | `/api/videos/{bvid}/retry` | — | 同提交接口（`status` 重置为 `queued`）；不存在 → 404 |
| DELETE | `/api/videos/{bvid}` | — | `{"deleted":true}`；不存在 → 404 |

状态机：`queued → crawling → processing → completed`（失败为 `failed` + `error`），
`progress` 0~100 整数且单调不减，`phases` 四个子步骤（采集评论 / 文本清洗 / 情感推理 / 统计分析）。
阶段时长**可配置**（`VIDEO_CRAWL_SECONDS` 等环境变量，默认合计 30 秒与前端 mock 一致）；
落盘在 `outputs/videos/`（`index.json` + `<bvid>.json`），启动时把残留的进行中任务重置为
`queued` 并重新入队，lifespan 关停时优雅停止后台线程。

> ℹ️ `/api/videos` 的「情感推理」阶段自 2026-10-30 起走**真实模型**
> （`service.get_predictor()` → `predict_text`，加载失败回退规则占位）；「采集评论」
> 阶段仍是 opt-in：默认不联网、由确定性占位数据生成 detail（`placeholder: true`），
> 设置 `VIDEO_REAL_CRAWL=1` / 任务级 `settings.crawl.real_crawl=true` 才真实爬取评论。
> 详见 [`src/videos/README.md`](../videos/README.md)。

## 分层设计（真实模型已接入，可继续替换）

| 文件 | 职责 | 换成真实模型时要改哪里 |
| --- | --- | --- |
| `app.py` | HTTP 层：路由、参数校验、错误码、CORS、导出下载、lifespan | 基本不用改 |
| `service.py` | 模型加载 + 预测逻辑（`load_model` / `Predictor` / `predict_text` / `predict_batch`） | **已实现**；换模型结构时改这里 |
| `batch.py` | 上传文件解析（csv / xlsx）、文本列识别、结果导出与下载路径安全校验 | 不用改 |
| `schemas.py` | Pydantic 请求 / 响应模型（契约的代码化） | 不用改 |
| `../videos/` | `/api/videos` 系列：状态机、存储、后台流水线、真实模型情感推理 | 见 `src/videos/README.md` |

### 真实检查点推理（`service.py`）

预测入口统一为 `service.predict_text(text, model, model_name)` /
`service.predict_batch(texts, model, model_name)`，模型由
`service.get_predictor()`（= `service.load_model_cached(ACTIVE_MODEL)`）解析：

| 情况 | `load_model` 行为 | `predict_text` | `method` |
| --- | --- | --- | --- |
| `models/checkpoints/<name>/best.pt` 存在且 torch 可用 | 返回 `Predictor`（模型 + 词表 + 预处理） | 真实前向 + softmax | 模型名（`textcnn` / `bilstm_attention`） |
| 检查点不存在 | 返回 `None` | `predict_rule_based`（**规则占位，不是训练模型**） | `rule_placeholder` |
| torch 未安装（`ImportError`） | 返回 `None` + `logging.warning` | 同上 | `rule_placeholder` |
| 检查点存在但损坏 / 格式不支持 / 词表或超参不匹配 | 抛 `RuntimeError`（可读提示） | —（不静默降级，避免把坏权重当占位结果） | — |

检查点格式（与 `src/training/trainer.py` 保存的一致）：
`torch.load(best.pt)` 的 dict 含 `model_state` / `config` / `vocab` / `hyperparams` /
`model_type` / `model_name` / `epoch` / `metrics`。加载时：

1. `model_type` 决定重建 `TextCNN` 还是 `BiLSTMAttention`，`hyperparams` 过滤后构造，
   `build()` + `load_state_dict()` + `eval()`；
2. 词表优先级：检查点内嵌 `vocab` 快照 → `models/checkpoints/<name>/preprocessing/vocab.json`
   → `data/processed/vocab.json`，长度必须与 `hyperparams["vocab_size"]` 一致；
3. `max_len` 取 `config["max_len"]`（缺失回退 200）；
4. 预处理优先用检查点自带的 `preprocessing/` 资源包（`load_preprocessing_bundle`
   校验 manifest 哈希），校验失败 `logging.warning` 后回退
   `TextPreprocessor.from_resources(max_len=...)`（仓库当前两个真实检查点都带
   `preprocessing/` 快照，校验可过；早期/手工目录没有该子目录时会走回退）；
5. 设备由环境变量 `INFERENCE_DEVICE` 控制：默认 `cpu`；`auto` 时有 CUDA 用 CUDA；
   显式 `cuda` 但不可用时告警并回退 `cpu`；
6. 清洗分词后为空的文本返回确定性的 `0.5 / 0.5`（标签 positive、置信度 0.5），
   不抛异常，保证批量接口健壮；
7. 概率为 `softmax` 后 round 到 6 位，且两类互补使和恒为 1。

> ✅ 仓库当前入库的是**真实训练权重**（2026-10-30 复跑，数据版本 `753bc2014c69bdc1`）：
> `textcnn` 6,074,780 B / `bilstm_attention` 6,736,317 B，内嵌词表 9,753 项、
> `max_len=200`；测试集 accuracy 分别为 **0.8960 / 0.9008**（见
> `docs/模型训练与评估-2026-10-30.md`）。训练语料是酒店/外卖公开语料，
> **不是 B 站领域准确率**；B 站人工标注测试集尚未建立。
> 若换成其它权重：直接覆盖 `models/checkpoints/<name>/best.pt` 并连同
> `config.json` / `metadata.json` / `preprocessing/` 一起替换，重启服务即可。

真实模型不可用（无检查点 / 无 torch）时，预测自动降级到
`service.predict_rule_based` —— 一个**规则 / 词典占位预测器，不是训练模型**：
内置小型中文情感词表（正面词 / 负面词 / 程度副词 / 否定词，合计约 140 词），
按标点切小句统计，程度副词加权、否定词翻转（如「不好」「不满意」「不推荐」），
把情感分压到 `(0, 1)` 区间。它只保证「明显好评 → positive、明显差评 → negative」
方向符合直觉，同一文本结果完全确定，且概率不会是 0 / 1。

导出与下载：
`POST /api/predict/batch` 把结果写到
`outputs/predictions/batch_prediction_<YYYYMMDD_HHMMSS>_<shortid>.csv`
（UTF-8 with BOM，Excel 直接打开不乱码；列 `index,text,label,confidence`），
返回 `export_url = /api/predict/batch/download/<file_id>`。
下载路由的安全边界在 `batch.resolve_export_path`：`file_id` 必须是纯文件名的
`.csv`，含 `/`、`\`、`..` 或非 CSV 一律 400；`resolve()` 后还会复核父目录仍是
`outputs/predictions/`；文件不存在返回 404。用户输入**绝不直接拼接成路径**。

## 启动与自测

```bash
# 启动（仓库根目录）
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload

# 真实模型推理设备（默认 cpu；auto = 有 CUDA 用 CUDA）
INFERENCE_DEVICE=auto python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000

# 冒烟自测（真实权重下 method 应为 bilstm_attention / textcnn）
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/predict -H "Content-Type: application/json" \
     -d '{"text":"这个商品质量很好，物流也快"}'
curl -X POST http://localhost:8000/api/predict/batch -F "file=@data/sample/test_sample.csv"
curl "http://localhost:8000/api/predict/batch/download/<file_id>" -o export.csv
curl -i "http://localhost:8000/api/predict/batch/download/..%2f..%2fetc%2fpasswd"  # 预期 4xx
curl http://localhost:8000/api/models   # ready=true 表示检查点已就绪

# /api/videos 系列：把默认 30 秒压到 1 秒左右再看状态机
VIDEO_CRAWL_SECONDS=0.4 VIDEO_CLEAN_SECONDS=0.2 VIDEO_SENTIMENT_SECONDS=0.2 \
VIDEO_STATS_SECONDS=0.2 python -m uvicorn src.inference.app:app --port 8123
curl -X POST http://localhost:8123/api/videos -H "Content-Type: application/json" -d '{"bvid":"BV1xx411c7mD"}'
curl -i -X POST http://localhost:8123/api/videos -H "Content-Type: application/json" -d '{"bvid":"BV123"}'  # 预期 400
curl http://localhost:8123/api/videos
curl http://localhost:8123/api/videos/BV1xx411c7mD/detail
curl -X POST http://localhost:8123/api/videos/BV1xx411c7mD/retry
curl -X DELETE http://localhost:8123/api/videos/BV1xx411c7mD

# 单测（真实模型相关用例在无 torch / 无检查点时自动跳过）
python -m pytest tests/ -q
```

## 待实现清单（TODO）

- [x] `service.py:load_model` —— 读取 `models/checkpoints/<model>/best.pt`（含
      `preprocessing/` 资源包兼容）+ 词表，构建模型、`load_state_dict` 并 `eval()`
- [x] `service.py:predict_text` / `Predictor.predict_batch` —— 真实前向：分词 → 编码 →
  `softmax` → 极性与置信度（BiLSTM 的 `(logits, attn)` 只取 logits）
- [x] `service.py:predict_rule_based` —— **保留为降级分支**：检查点缺失 / torch 缺失时
  服务仍可用（`method=rule_placeholder`），不再作为唯一预测来源
- [x] `service.py:load_model_cached` —— 进程内 `lru_cache`；检查点更新后重启服务或
  `cache_clear()`（测试切 `ACTIVE_MODEL` 时必须清缓存）
- [ ] `service.py` —— 检查点更新后按 mtime 自动失效缓存（长期运行的热更新）
- [ ] `src/artifacts/pipeline.py` —— 情感阶段已接 `service.predict_text`；如需逐条上传注意力
  权重/可视化，再扩展 `sentiment.csv` 列（当前契约不要求）
- [ ] `batch.py:export_results` —— 导出文件目前不清理（长期运行需加保留策略 / 定期删除）
- [ ] `app.py` —— 生产部署时收敛 CORS `allow_origins`（当前为联调方便放开了全部来源）
- [ ] `../videos/pipeline.py` —— 「采集评论」阶段仍是 opt-in（`VIDEO_REAL_CRAWL=1` /
  任务设置），可改为默认走 `src/crawler/spider.py` 的真实采集（需联网与 robots 合规）
- [ ] `../videos/placeholder.py` —— 真实数据接入后删除，并把 detail 的 `placeholder` 置为 `false`
- [ ] `../videos/service.py` —— 多 uvicorn worker 部署时需换成外部队列（当前只保证单进程）

## 关联

- 路线图：`docs/开发路线图.md` 阶段五第 1 步
- 上游：`src/preprocessing`、`src/models`、`src/evaluation`（`/api/models` 读指标 JSON）
- 下游：`frontend/`（Vue 3 通过 Vite 代理访问 `:8000`）
- 一键脚本：`scripts/serve.sh`
