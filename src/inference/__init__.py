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

本包**不在导入期加载 torch**；Web 层（`app.py`）与模型层（`service.py`）解耦，
真实模型只需替换 `service.load_model` / `service.predict_text` 两处。
当前预测是 `service.predict_rule_based` 的**规则 / 词典占位**（非训练模型）；
`/api/videos` 的评论数据同样是**占位流水线**（真实爬虫已在 `src/crawler/` 实现，
可经 `fetch_video_comments` 接入；当前流水线尚未切换），
详情响应带 `placeholder: true` 如实标注。
"""

from .batch import (
    EmptyBatchError,
    UnsafeExportPathError,
    UnsupportedFileTypeError,
    read_texts,
    resolve_export_path,
)
from .service import (
    LABELS,
    available_models,
    get_active_model_name,
    load_model,
    predict_batch,
    predict_rule_based,
    predict_text,
)

__all__ = [
    "LABELS",
    "get_active_model_name",
    "load_model",
    "predict_text",
    "predict_batch",
    "predict_rule_based",
    "available_models",
    "read_texts",
    "resolve_export_path",
    "UnsupportedFileTypeError",
    "EmptyBatchError",
    "UnsafeExportPathError",
]
