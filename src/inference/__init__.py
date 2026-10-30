"""⑧ 推理服务模块（FastAPI，:8000）。

对外契约（由团队锁定，前后端共用，见 `docs/API契约.md`，**不得改名**）：

原有 5 个（保持不变）：

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":str,"confidence":float,"probs":{...},"method":str}` |
| POST | `/api/predict/batch` | multipart `.csv` / `.xlsx` | `{"total":int,"results":[...],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | CSV 文件（`text/csv`，附件下载；防目录穿越） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{...},"ready":bool}]}` |

新增的 B 站视频评论分析系列（实现在 `src.videos`，契约 `docs/API契约.md` 第二节）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/videos` | 提交 BV 号；非法 400、重复在处理中 409 |
| GET | `/api/videos` | 任务列表（`created_at` 倒序） |
| GET | `/api/videos/{bvid}/detail` | 详情；未完成也返回 200，未知 bvid 404 |
| POST | `/api/videos/{bvid}/retry` | 重置为 `queued`；不存在 404 |
| DELETE | `/api/videos/{bvid}` | 删除；不存在 404 |

启动方式::

    python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload

本包**不在导入期加载 torch**；Web 层（`app.py`）与模型层（`service.py`）解耦。
预测入口统一走 `service.predict_text` / `service.predict_batch`：若
``models/checkpoints/<ACTIVE_MODEL>/best.pt`` 存在（且 torch 可用），
`service.get_predictor` / `service.load_model_cached` 返回真实检查点封装
`service.Predictor`，`/api/predict` 的 `method` 为**模型名**；检查点缺失或 torch
未安装时降级到 `service.predict_rule_based`（规则 / 词典**占位**，不是训练模型），
`method` 为 ``rule_placeholder``，服务始终可用。
`/api/videos` 的「情感推理」阶段也复用同一预测器（真实模型优先、加载失败回退规则
占位）；「采集评论」阶段仍是 opt-in（`VIDEO_REAL_CRAWL=1` / 任务设置），默认不联网、
detail 带 `placeholder: true` 表示评论数据来自确定性占位而非真实爬虫。
"""

from .batch import (
    EmptyBatchError,
    UnsafeExportPathError,
    UnsupportedFileTypeError,
    read_texts,
    resolve_export_path,
)
from .service import (
    ENV_ACTIVE_MODEL,
    ENV_INFERENCE_DEVICE,
    LABELS,
    METHOD_RULE_PLACEHOLDER,
    Predictor,
    available_models,
    get_active_model_name,
    get_predictor,
    load_model,
    load_model_cached,
    predict_batch,
    predict_rule_based,
    predict_text,
    resolve_method,
)

__all__ = [
    "ENV_ACTIVE_MODEL",
    "ENV_INFERENCE_DEVICE",
    "LABELS",
    "METHOD_RULE_PLACEHOLDER",
    "Predictor",
    "get_active_model_name",
    "get_predictor",
    "load_model",
    "load_model_cached",
    "predict_text",
    "predict_batch",
    "predict_rule_based",
    "resolve_method",
    "available_models",
    "read_texts",
    "resolve_export_path",
    "UnsupportedFileTypeError",
    "EmptyBatchError",
    "UnsafeExportPathError",
]
