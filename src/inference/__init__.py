"""⑧ 推理服务模块（FastAPI，:8000）。

对外契约（由团队锁定，前后端共用，见根 README「前后端接口契约」一节，**不得改名**）：

| 方法 | 路径 | 请求 | 响应 |
| --- | --- | --- | --- |
| GET | `/health` | — | `{"status":"ok","service":str,"version":str}` |
| POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":str,"confidence":float,"probs":{...}}` |
| POST | `/api/predict/batch` | multipart `.csv` / `.xlsx` | `{"total":int,"results":[...],"export_url":str}` |
| GET | `/api/predict/batch/download/{file_id}` | — | CSV 文件（`text/csv`，附件下载；防目录穿越） |
| GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{...},"ready":bool}]}` |

启动方式::

    python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload

本包**不在导入期加载 torch**；Web 层（`app.py`）与模型层（`service.py`）解耦，
真实模型只需替换 `service.load_model` / `service.predict_text` 两处。
当前预测是 `service.predict_rule_based` 的**规则 / 词典占位**（非训练模型）。
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
