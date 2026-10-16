"""FastAPI 推理服务应用（骨架阶段：规则占位预测，接口可用）。

启动::

    python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload

暴露的**业务路由**（契约见根 README「前后端接口契约」一节，原 4 个不得改名）：

- ``GET  /health``
- ``POST /api/predict``
- ``POST /api/predict/batch``
- ``GET  /api/predict/batch/download/{file_id}``（导出文件下载，防目录穿越）
- ``GET  /api/models``

预测逻辑与模型加载在 `service.py`，上传文件解析与结果导出在 `batch.py`，
本文件只负责 HTTP 语义（校验、状态码、响应模型）。
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import batch as batch_io
from . import service
from .schemas import (
    BatchItem,
    BatchPredictResponse,
    HealthResponse,
    ModelInfo,
    ModelsResponse,
    PredictRequest,
    PredictResponse,
)

#: 服务标识与版本（``/health`` 返回，改动需同步 docs 与前端展示）
SERVICE_NAME = "comment-sentiment-analysis-api"
SERVICE_TITLE = "网站用户评论情感分析系统 · 推理服务"
VERSION = "0.1.0"

app = FastAPI(
    title=SERVICE_TITLE,
    version=VERSION,
    description=(
        "网站用户评论情感分析系统的推理服务：单条预测、批量预测（csv/xlsx）、模型列表。\n\n"
        "骨架阶段为占位实现（mock），真实模型接入后接口契约保持不变。"
    ),
    docs_url="/docs",
)

# 前端开发服务器（:3000）与后端（:8000）跨域联调需要 CORS；
# 生产部署时应收敛 allow_origins（TODO）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, summary="健康检查", tags=["系统"])
def health() -> HealthResponse:
    """返回服务存活状态、服务标识与版本号（前端启动时探活）。"""
    return HealthResponse(status="ok", service=SERVICE_NAME, version=VERSION)


@app.post("/api/predict", response_model=PredictResponse, summary="单条评论情感预测", tags=["预测"])
def predict(payload: PredictRequest) -> PredictResponse:
    """输入一条评论，返回情感极性、置信度与两类概率。

    - 空文本由 Pydantic 校验（``min_length=1``）直接返回 422；
    - 纯空白文本在此显式返回 422。
    """
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text 不能为纯空白字符串")

    model = service.load_model_cached(service.get_active_model_name())
    result = service.predict_text(text, model=model, model_name=service.get_active_model_name())
    return PredictResponse(**result)


@app.post(
    "/api/predict/batch",
    response_model=BatchPredictResponse,
    summary="批量评论情感预测（csv / xlsx）",
    tags=["预测"],
)
async def predict_batch(file: UploadFile = File(..., description=".csv 或 .xlsx 文件")):
    """上传 csv / xlsx，逐条预测、把结果落盘并返回可下载的 ``export_url``。

    文件约定：首行为表头，文本列自动识别（``text`` → ``comment`` → ``content``
    → ``评论`` → 第一列）；空文本行跳过，``results[].index`` 为有效行序号（从 0 开始）。

    导出：结果写入 ``outputs/predictions/batch_prediction_<时间戳>_<shortid>.csv``
    （UTF-8 with BOM，列 ``index,text,label,confidence``），``export_url`` 形如
    ``/api/predict/batch/download/<file_id>``。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > batch_io.MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（上限 {batch_io.MAX_BYTES // (1024 * 1024)} MB）",
        )

    try:
        texts = batch_io.read_texts(file.filename or "", content)
    except batch_io.UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except batch_io.EmptyBatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImportError as exc:  # openpyxl 缺失
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    model_name = service.get_active_model_name()
    model = service.load_model_cached(model_name)
    results = service.predict_batch(texts, model=model, model_name=model_name)

    # 改进 B：结果落盘并返回下载地址（文件名由 batch 层生成，不含用户输入）
    export_filename = batch_io.new_export_filename()
    try:
        batch_io.export_results(results, filename=export_filename)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"导出结果文件失败：{exc}") from exc

    return BatchPredictResponse(
        total=len(results),
        results=[BatchItem(**item) for item in results],
        export_url=batch_io.export_url_for(export_filename),
    )


@app.get(
    "/api/predict/batch/download/{file_id}",
    summary="下载批量预测导出文件（CSV）",
    tags=["预测"],
    response_class=FileResponse,
)
def download_batch_export(file_id: str) -> FileResponse:
    """下载 ``outputs/predictions/`` 下的批量预测结果 CSV。

    安全约束（``file_id`` 完全不可控拼接）：

    - ``file_id`` 必须是纯文件名，含 ``/``、``\\``、``..`` 等路径成分一律 400；
    - ``resolve()`` 后父目录必须仍为 ``outputs/predictions/``，否则 400；
    - 非 ``.csv``、文件不存在返回 404。
    """
    try:
        path = batch_io.resolve_export_path(file_id)
    except batch_io.UnsafeExportPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"导出文件不存在：{file_id}")

    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/api/models", response_model=ModelsResponse, summary="可用模型列表与指标", tags=["模型"])
def list_models() -> ModelsResponse:
    """返回支持的模型及其测试集指标、检查点是否就绪。

    指标来源：``outputs/stats/metrics_<model>.json``（`src.evaluation` 产出）；
    未训练时各项为 ``0.0`` 且 ``ready`` 为 ``False``。
    """
    return ModelsResponse(models=[ModelInfo(**item) for item in service.available_models()])


if __name__ == "__main__":  # pragma: no cover - 便于 `python -m src.inference.app` 直接启动
    import uvicorn

    uvicorn.run(
        "src.inference.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
