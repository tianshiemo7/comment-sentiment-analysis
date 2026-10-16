"""FastAPI 请求 / 响应模型（接口契约的代码化）。

字段名与根 README「前后端接口契约」一节的契约严格一致，任何一侧改动都会导致前端联调失败。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: 标签取值（顺序与 `src/models/base.py` 的 logits 顺序一致）
LABEL_PATTERN = "^(positive|negative)$"


class HealthResponse(BaseModel):
    """``GET /health`` 响应。"""

    status: str = Field(..., description="服务状态，正常为 ok", examples=["ok"])
    service: str = Field(..., description="服务标识", examples=["comment-sentiment-analysis-api"])
    version: str = Field(..., description="服务版本号", examples=["0.1.0"])


class PredictRequest(BaseModel):
    """``POST /api/predict`` 请求体。"""

    text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="待预测的单条评论",
        examples=["这个商品质量很好，物流也快，非常满意。"],
    )


class PredictResponse(BaseModel):
    """``POST /api/predict`` 响应。"""

    text: str = Field(..., description="原样返回的评论文本")
    label: str = Field(..., pattern=LABEL_PATTERN, description="情感极性")
    confidence: float = Field(..., ge=0.0, le=1.0, description="预测置信度（最大类概率）")
    probs: dict[str, float] = Field(
        ...,
        description='两类概率，形如 {"positive": 0.93, "negative": 0.07}，和为 1',
    )


class BatchItem(BaseModel):
    """批量预测中的单条结果。"""

    index: int = Field(..., ge=0, description="数据行序号（从 0 开始）")
    text: str = Field(..., description="该行评论文本")
    label: str = Field(..., pattern=LABEL_PATTERN, description="情感极性")
    confidence: float = Field(..., ge=0.0, le=1.0, description="预测置信度")


class BatchPredictResponse(BaseModel):
    """``POST /api/predict/batch`` 响应。

    ``export_url`` 为导出文件下载地址；结果已落盘到 ``outputs/predictions/``，
    因此成功时**不再返回 null**（仅在未来明确禁用导出时才可能为 null）。
    下载走 ``GET /api/predict/batch/download/{file_id}``（防目录穿越）。
    """

    total: int = Field(..., ge=0, description="成功预测的条数")
    results: list[BatchItem] = Field(..., description="逐条预测结果")
    export_url: str | None = Field(
        None,
        description="导出文件下载地址（形如 /api/predict/batch/download/<file_id>），无则为 null",
    )


class ModelMetrics(BaseModel):
    """模型评估指标（未训练时各项为 0.0）。"""

    accuracy: float = Field(0.0, ge=0.0, le=1.0)
    precision: float = Field(0.0, ge=0.0, le=1.0)
    recall: float = Field(0.0, ge=0.0, le=1.0)
    f1: float = Field(0.0, ge=0.0, le=1.0)


class ModelInfo(BaseModel):
    """``GET /api/models`` 中的单个模型条目。"""

    name: str = Field(..., description="模型名称，如 textcnn / bilstm_attention")
    type: str = Field(..., description="模型类型（与 name 对应，供前端分组展示）")
    metrics: ModelMetrics = Field(..., description="测试集指标")
    ready: bool = Field(..., description="检查点是否就绪（False 表示仍是占位实现）")


class ModelsResponse(BaseModel):
    """``GET /api/models`` 响应。"""

    models: list[ModelInfo] = Field(..., description="可用模型列表")
