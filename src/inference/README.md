# src/inference —— ⑧ 推理服务模块（FastAPI）

> 对应需求：可运行入口（CLI 或 Web 界面）、输入一条评论 → 情感极性与置信度、
> 批量预测 .csv/.xlsx 并导出。路线图阶段五；验收项 `docs/验收检查清单.md` 六、系统演示。

## 接口契约（锁定，不得改名）

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float}}` |
| POST | `/api/predict/batch` | multipart 文件（字段名 `file`；支持 `.csv` / `.txt` / `.tsv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | CSV 文件（`Content-Type: text/csv`，`Content-Disposition: attachment`） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` |

契约由 Lead 锁定：原 4 个业务路由**不得改名**，`total` / `results` 字段保持不变
（`/docs`、`/openapi.json` 为 FastAPI 自带文档路由）。
批量接口现在会把结果落盘并返回非 null 的 `export_url`（契约扩展已由 Lead 锁定），
下载路由只允许读取 `outputs/predictions/` 下的 CSV，并显式防目录穿越。

## 分层设计（便于把 mock 换成真实模型）

| 文件 | 职责 | 换成真实模型时要改哪里 |
| --- | --- | --- |
| `app.py` | HTTP 层：路由、参数校验、错误码、CORS、导出下载 | 基本不用改 |
| `service.py` | 模型加载 + 预测逻辑（`load_model` / `predict_text` / `predict_batch`） | **主要改这里** |
| `batch.py` | 上传文件解析（csv / xlsx）、文本列识别、结果导出与下载路径安全校验 | 不用改 |
| `schemas.py` | Pydantic 请求 / 响应模型（契约的代码化） | 不用改 |

当前状态：`service.load_model` 返回 `None` 表示**占位模式**，`predict_text` 走
`service.predict_rule_based` —— 一个**规则 / 词典占位预测器，不是训练模型**：
内置小型中文情感词表（正面词 / 负面词 / 程度副词 / 否定词，合计约 140 词），
按标点切小句统计，程度副词加权、否定词翻转（如「不好」「不满意」「不推荐」），
把情感分压到 `(0, 1)` 区间。它只保证「明显好评 → positive、明显差评 → negative」
方向符合直觉，同一文本结果完全确定，且概率不会是 0 / 1。

真实模型接入后 `load_model` 返回非 `None` 对象，`predict_text` 走真实前向分支，
`predict_rule_based` 不再被调用（可删除或保留为降级分支）；未实现前真实分支抛
带 TODO 说明的 `NotImplementedError`。

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

# 冒烟自测
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/predict -H "Content-Type: application/json" \
     -d '{"text":"这个商品质量很好，物流也快"}'
curl -X POST http://localhost:8000/api/predict/batch -F "file=@data/sample/test_sample.csv"
curl "http://localhost:8000/api/predict/batch/download/<file_id>" -o export.csv
curl -i "http://localhost:8000/api/predict/batch/download/..%2f..%2fetc%2fpasswd"  # 预期 4xx
curl http://localhost:8000/api/models

# 单测（无需 torch）
python -m pytest tests/ -q
```

## 待实现清单（TODO）

- [ ] `service.py:load_model` —— 读取 `models/checkpoints/<model>/best.pt` + 词表，构建模型并 `eval()`
- [ ] `service.py:predict_text` —— 真实前向：分词 → 编码 → `softmax` → 极性与置信度
- [ ] `service.py:predict_rule_based` —— 真实模型接入后整体删除（当前是诚实标注的规则占位）
- [ ] `service.py:load_model_cached` —— 检查点更新后需重启服务（或加文件 mtime 失效逻辑）
- [ ] `batch.py:export_results` —— 导出文件目前不清理（长期运行需加保留策略 / 定期删除）
- [ ] `app.py` —— 生产部署时收敛 CORS `allow_origins`（当前为联调方便放开了全部来源）

## 关联

- 路线图：`docs/开发路线图.md` 阶段五第 1 步
- 上游：`src/preprocessing`、`src/models`、`src/evaluation`（`/api/models` 读指标 JSON）
- 下游：`frontend/`（Vue 3 通过 Vite 代理访问 `:8000`）
- 一键脚本：`scripts/serve.sh`
