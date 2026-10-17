"""FastAPI 推理服务应用（骨架阶段：规则占位预测，接口可用）。

启动::

    python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload

暴露的**业务路由**（契约见 `docs/API契约.md`，全部不得改名）：

原有 5 个（**保持不变**）：

- ``GET  /health``
- ``POST /api/predict``
- ``POST /api/predict/batch``
- ``GET  /api/predict/batch/download/{file_id}``（导出文件下载，防目录穿越）
- ``GET  /api/models``

本次新增的 B 站视频评论分析系列（实现在 `src/videos/`）：

- ``POST   /api/videos``（提交 BV 号；非法 400 / 重复在处理中 409 / 可选 settings）
- ``GET    /api/videos``（列表，按 ``created_at`` 倒序）
- ``GET    /api/videos/{bvid}/detail``（详情；未完成也 200，不报错）
- ``POST   /api/videos/{bvid}/retry``（重置为 queued；不存在 404）
- ``POST   /api/videos/{bvid}/continue``（等待态继续：crawled→清洗 / cleaned→分析；409/404）
- ``DELETE /api/videos/{bvid}``（删除；不存在 404）

设置与账号（实现在 `src/videos/settings.py` / `src/videos/browser_login.py`，spec 第 2 节）：

- ``GET    /api/settings``（读取设置；Cookie 只返回 has_cookie / masked_cookie）
- ``PUT    /api/settings``（部分更新；越界 / 枚举非法 / 账号缺凭据 → 400）
- ``POST   /api/crawler/session/check``（检查登录态；未配置凭据 200 匿名 / 异常 502）
- ``POST   /api/account/login/start``（在**后端机器桌面**弹出人工登录窗口；缺依赖 503）
- ``GET    /api/account/login/status``（轮询登录窗口状态）
- ``POST   /api/account/login/cancel``（取消登录并关闭窗口）

本次新增的「数据项目」与「单独评论」两段（实现在 `src/artifacts/`，契约 4.5 节）：

- ``GET    /api/artifacts``（左栏三段：projects / solo / videos）
- ``POST   /api/artifacts/upload``（multipart 上传，校验后缀与大小，自动入队处理）
- ``GET    /api/artifacts/{project_id}``（项目对象；不存在 404）
- ``POST   /api/artifacts/{project_id}/analyze``（重新处理）
- ``GET    /api/artifacts/{project_id}/stages``（三段阶段清单）
- ``GET    /api/artifacts/{project_id}/table``（某阶段表格分页）
- ``GET    /api/artifacts/{project_id}/comments``（逐条占位情感，可按 label 过滤）
- ``GET    /api/artifacts/{project_id}/analysis``（聚合占比 / 词频 / 长度分布）
- ``GET    /api/artifacts/{project_id}/path``（**只读**返回该阶段 CSV 路径，无副作用）
- ``POST   /api/artifacts/{project_id}/open``（在服务器本机打开 CSV）
- ``DELETE /api/artifacts/{project_id}``（删除项目目录）
- ``POST   /api/solo-comments`` / ``GET /api/solo-comments`` /
  ``DELETE /api/solo-comments/{id}`` / ``DELETE /api/solo-comments?ids=a,b``

**占位声明**：数据项目的情感结果来自 `src.inference.service.predict_rule_based`
（规则 / 词典占位预测器，**不是训练模型**），接口 ``method`` 字段固定为
``rule_placeholder``。安全约定见 `docs/API契约.md` 4.5.3：
``table`` / ``comments`` / ``analysis`` / ``open`` 只接受服务端生成的 id 与固定 stage 枚举，
**对外绝不接受任何路径**。

预测逻辑与模型加载在 `service.py`，上传文件解析与结果导出在 `batch.py`，
视频分析任务的状态机 / 存储 / 后台流水线在 `src/videos/`，
数据项目与单独评论在 `src/artifacts/`，
本文件只负责 HTTP 语义（校验、状态码、响应模型）与 lifespan。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..artifacts import service as artifacts_service
from ..artifacts.errors import (
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    ArtifactValidationError,
    OpenUnsupportedError,
)
from ..artifacts.schemas import (
    DEFAULT_COMMENTS_LIMIT,
    DEFAULT_SOLO_LIMIT,
    DEFAULT_TABLE_LIMIT,
    MAX_COMMENTS_LIMIT,
    MAX_SOLO_LIMIT,
    MAX_TABLE_LIMIT,
    AnalysisResponse,
    AnalyzeRequest,
    ArtifactDeleteResponse,
    ArtifactListResponse,
    ArtifactPathResponse,
    CommentsResponse,
    OpenRequest,
    OpenResponse,
    ProjectItem,
    SoloCommentItem,
    SoloCreateRequest,
    SoloDeleteResponse,
    SoloListResponse,
    StageItem,
    StageListResponse,
    TableResponse,
)
from ..videos import browser_login
from ..videos import service as videos_service
from ..videos import settings as video_settings
from ..videos.browser_login import AccountLoginStatus, LoginDependencyError
from ..videos.errors import (
    InvalidBvidError,
    VideoBusyError,
    VideoNotContinuableError,
    VideoNotFoundError,
)
from ..videos.schemas import (
    VideoDeleteResponse,
    VideoDetailResponse,
    VideoListResponse,
    VideoSubmitRequest,
    VideoSubmitResponse,
)
from ..videos.settings import (
    SettingsResponse,
    SettingsValidationError,
    SessionCheckError,
    SessionCheckRequest,
    SessionCheckResponse,
)
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    """服务生命周期：启动视频分析与数据项目流水线，关停时优雅停止后台线程。

    - 启动：读回 `outputs/videos/index.json`，把上次残留的 `queued` / `crawling` /
      `processing` 任务重置为 `queued` 并重新入队；同时扫描 `data/uploads/`，
      把残留的 `queued` / `processing` 项目重置为 `queued` 并重新入队；
    - 关停：置位各自的 `stop_event`，流水线在阶段内立刻退出并把任务 / 项目恢复成
      `queued`，下次启动继续跑（标准库线程，无 Celery / Redis 依赖）。
    """
    videos_service.get_service().start()
    artifacts_service.get_service().start()
    try:
        yield
    finally:
        artifacts_service.get_service().shutdown()
        videos_service.get_service().shutdown()
        # 关停时关闭可能仍开着的人工登录窗口（daemon 线程，不阻塞退出）
        browser_login.shutdown()


app = FastAPI(
    title=SERVICE_TITLE,
    version=VERSION,
    description=(
        "网站用户评论情感分析系统的推理服务：单条预测、批量预测（csv/xlsx）、模型列表，\n\n"
        "以及 B 站视频评论分析任务（``/api/videos`` 系列：提交 / 列表 / 详情 / 重试 / 删除），\n\n"
        "以及数据项目与单独评论（``/api/artifacts`` 与 ``/api/solo-comments`` 系列：\n"
        "上传 → clean 管线 → 三张表 → 占位情感 → 聚合分析 → 本机打开 CSV）。\n\n"
        "骨架阶段为占位实现（mock），真实模型与真实爬虫接入后接口契约保持不变；\n"
        "数据项目的情感结果 ``method`` 字段固定为 ``rule_placeholder``（不是训练模型）。"
    ),
    docs_url="/docs",
    lifespan=lifespan,
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
    """输入一条评论，返回情感极性、置信度、两类概率与预测来源 `method`。

    - 空文本由 Pydantic 校验（``min_length=1``）直接返回 422；
    - 纯空白文本在此显式返回 422；
    - ``method`` 是**新增字段**：当前为 ``rule_placeholder``（规则 / 词典占位预测器，
      **不是训练模型**），真实模型接入后自动变成模型名；前端据此在「单独评论」
      里如实标注来源，而不是硬编码占位。
    """
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text 不能为纯空白字符串")

    model_name = service.get_active_model_name()
    model = service.load_model_cached(model_name)
    result = service.predict_text(text, model=model, model_name=model_name)
    return PredictResponse(**result, method=service.resolve_method(model, model_name))


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


# --------------------------------------------------------------------------- #
# B 站视频评论分析任务（`/api/videos` 系列，契约见 docs/API契约.md）
#
# 业务规则（校验、去重、404）与状态机在 `src/videos/service.py` / `pipeline.py`，
# 本处只做 HTTP 语义：把业务异常映射成 400 / 409 / 404，并按 response_model 裁剪字段。
# --------------------------------------------------------------------------- #
@app.post(
    "/api/videos",
    response_model=VideoSubmitResponse,
    summary="提交 B 站视频分析任务",
    tags=["视频分析"],
)
def create_video_task(payload: VideoSubmitRequest) -> VideoSubmitResponse:
    """提交 BV 号创建分析任务，成功返回 ``{"bvid","status":"queued","progress":0,"created_at"}``。

    - BV 号不匹配 ``^BV[0-9A-Za-z]{10}$`` → **400**；
    - 同一 BV 号已在 ``queued`` / ``crawling`` / ``processing`` → **409**；
    - 已完成 / 已失败的任务可重新提交（会被重跑回 ``queued``）；
    - 可选 ``settings``：省略 = 旧行为（一次跑完四阶段）；传入 = 分步流水线
      （``crawled`` / ``cleaned`` 等待态），设置非法 → **400**。
    """
    try:
        task = videos_service.get_service().submit(payload.bvid, payload.settings)
    except InvalidBvidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VideoBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return VideoSubmitResponse(**task)


@app.get(
    "/api/videos",
    response_model=VideoListResponse,
    summary="视频分析任务列表（前端左侧栏轮询）",
    tags=["视频分析"],
)
def list_video_tasks() -> VideoListResponse:
    """返回 ``{"total", "videos": [...]}``，按 ``created_at`` 倒序（最新在前）。

    任务进行中 ``title`` / ``owner`` / ``cover_url`` 允许为 ``null``（前端显示 BV 号占位）。
    """
    return VideoListResponse(**videos_service.get_service().list_videos())


@app.get(
    "/api/videos/{bvid}/detail",
    response_model=VideoDetailResponse,
    summary="视频分析详情（未完成也返回 200）",
    tags=["视频分析"],
)
def get_video_detail(bvid: str) -> VideoDetailResponse:
    """返回 ``video`` / ``analysis`` / ``sentiment`` / ``trend`` / ``keywords`` / ``model`` /
    ``top_comments`` / ``error`` 全部字段。

    **未完成时不报错，仍返回 HTTP 200**：``analysis.status`` 为当前状态并带 ``progress`` /
    ``message``，``sentiment`` 为 ``null``，``trend`` / ``keywords`` / ``top_comments`` 为空。
    未知 ``bvid`` → **404**。
    """
    try:
        detail = videos_service.get_service().detail(bvid)
    except VideoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VideoDetailResponse(**detail)


@app.post(
    "/api/videos/{bvid}/retry",
    response_model=VideoSubmitResponse,
    summary="重试视频分析任务",
    tags=["视频分析"],
)
def retry_video_task(bvid: str) -> VideoSubmitResponse:
    """把任务重置为 ``queued`` 并重新入队，返回与提交接口相同结构的对象；不存在 → **404**。"""
    try:
        task = videos_service.get_service().retry(bvid)
    except VideoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VideoSubmitResponse(**task)


@app.post(
    "/api/videos/{bvid}/continue",
    response_model=VideoSubmitResponse,
    summary="继续分步流水线（crawled → 清洗，cleaned → 分析）",
    tags=["视频分析"],
)
def continue_video_task(bvid: str) -> VideoSubmitResponse:
    """从等待态继续执行（spec 3.3）。

    - 仅当任务处于 ``crawled`` / ``cleaned`` 时允许；其他状态 → **409**，
      ``detail`` 形如「任务当前状态 queued，无需继续」；
    - 未知 ``bvid`` → **404**；
    - 成功：重置为 ``queued``、``progress=0``、phases 保留已完成阶段，重新入队，
      响应同 ``POST /api/videos``。
    """
    try:
        task = videos_service.get_service().continue_task(bvid)
    except VideoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VideoNotContinuableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return VideoSubmitResponse(**task)


@app.delete(
    "/api/videos/{bvid}",
    response_model=VideoDeleteResponse,
    summary="删除视频分析任务",
    tags=["视频分析"],
)
def delete_video_task(bvid: str) -> VideoDeleteResponse:
    """删除任务及其 detail 文件，成功返回 ``{"deleted": true}``；不存在 → **404**。"""
    try:
        videos_service.get_service().delete(bvid)
    except VideoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return VideoDeleteResponse(deleted=True)


# --------------------------------------------------------------------------- #
# 系统设置与账号（`/api/settings`、`/api/crawler/session/check`、`/api/account/login/*`）
#
# 业务规则在 `src/videos/settings.py` 与 `src/videos/browser_login.py`，
# 本处只做 HTTP 语义：
# - Cookie 明文永不回传：GET/PUT 只返回 has_cookie / masked_cookie；
# - PUT 校验失败映射为 400（不是 422），detail 为中文可读提示；
# - 登录态检查失败映射为 502；
# - 人工登录窗口依赖缺失映射为 503（带安装指引）。
# --------------------------------------------------------------------------- #
@app.get(
    "/api/settings",
    response_model=SettingsResponse,
    summary="读取系统设置（Cookie 脱敏）",
    tags=["设置"],
)
def get_settings() -> SettingsResponse:
    """返回账号 / 采集参数 / 自动流程三组设置与最近保存时间。

    Cookie 明文只存在服务端 ``outputs/settings.json``，本接口只返回
    ``has_cookie`` / ``masked_cookie``（形如 ``SESSD…9f2a``）；文件不存在或损坏时
    按默认值返回。
    """
    return SettingsResponse(**video_settings.load_public())


@app.put(
    "/api/settings",
    response_model=SettingsResponse,
    summary="部分更新系统设置（Cookie 脱敏返回）",
    tags=["设置"],
)
def put_settings(payload: dict | None = Body(None)) -> SettingsResponse:
    """部分更新设置（缺省字段保持不变，只做浅层嵌套合并）。

    - ``account.cookie`` 非空 → 保存；``account.cookie: ""`` 或
      ``account.mode == "anonymous"`` → 清除凭据；
    - ``account.mode == "account"`` 但没有凭据 → **400**（中文提示去填 Cookie）；
    - 数值越界 / 枚举非法 / 类型不对 → **400**；
    - 成功返回与 ``GET /api/settings`` 相同结构（脱敏）。
    """
    try:
        updated = video_settings.update_settings(payload if payload is not None else {})
    except SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SettingsResponse(**updated)


@app.post(
    "/api/crawler/session/check",
    response_model=SessionCheckResponse,
    summary="检查 B 站登录态（可用临时 Cookie）",
    tags=["设置"],
)
def check_crawler_session(payload: SessionCheckRequest | None = Body(None)) -> SessionCheckResponse:
    """检查登录态；未配置凭据返回匿名态 200，网络 / 接口异常返回 **502**。

    请求体可省略；``{"cookie": "SESSDATA=..."}`` 可用尚未保存的临时凭据检查。
    登录成功后把 ``uname`` / ``mid`` 缓存进设置文件（只读字段）。
    """
    try:
        result = video_settings.check_session(
            payload.cookie if payload is not None else None
        )
    except SessionCheckError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SessionCheckResponse(**result)


@app.post(
    "/api/account/login/start",
    response_model=AccountLoginStatus,
    summary="打开人工登录窗口（B 站扫码 / 账密登录）",
    tags=["设置"],
)
def start_account_login() -> AccountLoginStatus:
    """在**后端所在机器**的桌面弹出真实浏览器窗口，由人完成登录后自动保存 Cookie。

    - 已打开窗口在等待时重复调用直接返回当前状态（幂等）；
    - Playwright 未安装 → **503**，``detail`` 带安装命令
      （``pip install playwright`` / ``python -m playwright install chromium``）；
    - 状态机：``opening → waiting → success | cancelled | timeout | failed``，
      前端用 ``GET /api/account/login/status`` 每 2 秒轮询。
    """
    try:
        status = browser_login.get_login_manager().start()
    except LoginDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AccountLoginStatus(**status)


@app.get(
    "/api/account/login/status",
    response_model=AccountLoginStatus,
    summary="查询人工登录窗口状态（前端 2 秒轮询）",
    tags=["设置"],
)
def get_account_login_status() -> AccountLoginStatus:
    """返回登录窗口状态对象（**不含 Cookie 明文**，只有 ``has_cookie`` 等）。"""
    return AccountLoginStatus(**browser_login.get_login_manager().snapshot())


@app.post(
    "/api/account/login/cancel",
    response_model=AccountLoginStatus,
    summary="取消人工登录并关闭窗口",
    tags=["设置"],
)
def cancel_account_login() -> AccountLoginStatus:
    """取消本次登录并关闭窗口；非等待态时幂等返回当前状态。"""
    return AccountLoginStatus(**browser_login.get_login_manager().cancel())


# --------------------------------------------------------------------------- #
# 数据项目（`/api/artifacts` 系列，契约 docs/API契约.md 4.5.1）
#
# 业务规则（上传校验、状态机、阶段 / 表格 / 评论 / 分析 / 打开 / 删除）都在
# `src/artifacts/service.py`，本处只做 HTTP 语义：把业务异常映射成 400 / 404 / 413，
# 并按 response_model 裁剪字段。
#
# 安全（契约 4.5.3）：`table` / `comments` / `analysis` / `open` 只接受服务端生成的
# `project_id` 与固定 `stage` 枚举，**对外绝不接受任何路径**。
# --------------------------------------------------------------------------- #
@app.get(
    "/api/artifacts",
    response_model=ArtifactListResponse,
    summary="左栏三段（数据项目 / 单独评论 / 视频任务）",
    tags=["数据项目"],
)
def list_artifacts() -> ArtifactListResponse:
    """返回 ``{"groups":[{key,label,items}×3]}``。

    ``projects`` / ``solo`` 来自本服务，``videos`` 复用既有 ``GET /api/videos`` 的列表项；
    视频服务未启动时该段为空数组（不报错）。
    """
    return ArtifactListResponse(**artifacts_service.get_service().list_groups())


@app.post(
    "/api/artifacts/upload",
    response_model=ProjectItem,
    summary="上传爬取数据（自动入队处理）",
    tags=["数据项目"],
)
async def upload_artifact(
    files: list[UploadFile] = File(..., description=".csv / .txt / .tsv / .xlsx，可多个"),
    name: str | None = Form(None, description="项目显示名；为空时用「未自定义数据」"),
) -> ProjectItem:
    """上传一个或多个数据文件，解析后落盘为统一 UTF-8 BOM CSV 并自动入队处理。

    - 后缀只允许 ``.csv`` / ``.txt`` / ``.tsv`` / ``.xlsx``，单文件 ≤ 20 MB、合计 ≤ 50 MB；
    - 文件名只取 basename（**不用用户文件名做路径**）；
    - 成功返回项目对象（通常 ``status="queued"``，随后由后台线程推进）；
    - 格式不支持 / 文件为空 / 无法解析 → **400**；超过大小上限 → **413**。
    """
    entries = [(item.filename or "", await item.read()) for item in files]
    try:
        project = await run_in_threadpool(
            artifacts_service.get_service().create_project, entries, name
        )
    except ArtifactTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectItem(**project)


@app.get(
    "/api/artifacts/{project_id}",
    response_model=ProjectItem,
    summary="项目状态（前端轮询）",
    tags=["数据项目"],
)
def get_artifact(project_id: str) -> ProjectItem:
    """返回项目对象；不存在（或 id 形态非法）→ **404**。"""
    try:
        return ProjectItem(**artifacts_service.get_service().get_project(project_id))
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/artifacts/{project_id}/analyze",
    response_model=ProjectItem,
    summary="重新处理项目",
    tags=["数据项目"],
)
def analyze_artifact(project_id: str, payload: AnalyzeRequest | None = Body(None)) -> ProjectItem:
    """重新跑一遍处理管线（清掉上一轮产物并把状态重置为 ``queued``）。

    请求体可省略；``{"mode": "auto"}`` 为默认（自动判定，无标签 → ``clean``）。
    当前只支持 ``auto`` / ``clean`` 两种取值，其余取值 → **400**；项目不存在 → **404**。
    """
    try:
        project = artifacts_service.get_service().analyze(
            project_id, payload.mode if payload is not None else "auto"
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProjectItem(**project)


@app.get(
    "/api/artifacts/{project_id}/stages",
    response_model=StageListResponse,
    summary="阶段清单（初始 / 筛选后 / 排除）",
    tags=["数据项目"],
)
def get_artifact_stages(project_id: str) -> StageListResponse:
    """返回 ``{"stages":[{key,label,rows,columns}×3]}``；未处理完的阶段行列数为 0。"""
    try:
        stages = artifacts_service.get_service().list_stages(project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StageListResponse(stages=[StageItem(**item) for item in stages])


@app.get(
    "/api/artifacts/{project_id}/table",
    response_model=TableResponse,
    summary="某阶段表格分页",
    tags=["数据项目"],
)
def get_artifact_table(
    project_id: str,
    stage: str = Query("clean", description="initial | clean | excluded"),
    offset: int = Query(0, ge=0, description="起始行（越界返回空 rows）"),
    limit: int = Query(DEFAULT_TABLE_LIMIT, ge=1, description=f"每页行数，上限 {MAX_TABLE_LIMIT}"),
) -> TableResponse:
    """返回某个阶段的表格分页。

    ``stage`` 只接受固定枚举，``limit`` 上限 500；stage 非法或 limit 越界 → **400**，
    项目不存在 → **404**。项目尚未处理完时返回空表（``total_rows`` 为 0），不报错。
    """
    try:
        table = artifacts_service.get_service().table(
            project_id, stage=stage, offset=offset, limit=limit
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TableResponse(**table)


@app.get(
    "/api/artifacts/{project_id}/comments",
    response_model=CommentsResponse,
    summary="逐条评论占位情感（可展开）",
    tags=["数据项目"],
)
def get_artifact_comments(
    project_id: str,
    offset: int = Query(0, ge=0, description="起始条（越界返回空 items）"),
    limit: int = Query(DEFAULT_COMMENTS_LIMIT, ge=1, description=f"每页条数，上限 {MAX_COMMENTS_LIMIT}"),
    label: str = Query("", description="positive | negative | neutral；不传为全部"),
) -> CommentsResponse:
    """返回逐条评论的占位情感结果，``items[].text`` 取原文（``text_raw``）。

    ``label`` 非法或 ``limit`` 越界 → **400**；项目不存在 → **404**。
    """
    try:
        comments = artifacts_service.get_service().comments(
            project_id, offset=offset, limit=limit, label=label
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CommentsResponse(**comments)


@app.get(
    "/api/artifacts/{project_id}/analysis",
    response_model=AnalysisResponse,
    summary="聚合情感分析（占比 / 词频 / 长度分布）",
    tags=["数据项目"],
)
def get_artifact_analysis(project_id: str) -> AnalysisResponse:
    """返回 ``{method,total,sentiment{...ratio},keywords,length,sample}``。

    ``method`` 如实反映来源，当前固定为 ``rule_placeholder``（规则占位，不是训练模型）；
    三个 ``*_ratio`` 之和约等于 1（``total > 0`` 时）。项目不存在 → **404**。
    """
    try:
        analysis = artifacts_service.get_service().analysis(project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AnalysisResponse(**analysis)


@app.post(
    "/api/artifacts/{project_id}/open",
    response_model=OpenResponse,
    summary="在服务器本机打开某阶段 CSV",
    tags=["数据项目"],
)
def open_artifact(project_id: str, payload: OpenRequest | None = Body(None)) -> OpenResponse:
    """用系统默认程序打开该项目的 CSV（Windows ``os.startfile`` / macOS ``open`` / Linux ``xdg-open``）。

    请求体可省略（默认打开 ``clean`` 阶段），``stage`` 只接受固定枚举。
    安全约束：扩展名必须是 ``.csv``，``resolve()`` 后父目录必须在 ``data/uploads/``
    或 ``data/processed/`` 之内，否则 **400**；该项目阶段文件不存在 → **400**；
    项目不存在 → **404**。
    """
    stage = payload.stage if payload is not None else "clean"
    try:
        opened = artifacts_service.get_service().open_csv(project_id, stage)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ArtifactValidationError, OpenUnsupportedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OpenResponse(**opened)


@app.get(
    "/api/artifacts/{project_id}/path",
    response_model=ArtifactPathResponse,
    summary="只读取某阶段 CSV 路径（无副作用）",
    tags=["数据项目"],
)
def get_artifact_path(
    project_id: str,
    stage: str = Query("clean", description="initial | clean | excluded"),
) -> ArtifactPathResponse:
    """返回 ``{"stage", "path", "exists"}`` —— **只读，无副作用**。

    **绝不调用 opener、不打开任何程序**：界面的「复制文件地址」用它，
    避免为了拿路径而真的弹一次 Excel（要真的打开文件请用 ``POST .../open``）。

    与 ``/open`` 共用同一套安全校验（固定 stage 枚举、只接受服务端生成的 id、
    ``resolve()`` 后必须位于 ``data/uploads/`` 或 ``data/processed/`` 内），
    区别是文件不存在时**不报错**，而是返回 ``exists: false``。
    非法 ``stage`` → **400**；项目不存在 → **404**。
    """
    try:
        payload = artifacts_service.get_service().resolve_stage_path(project_id, stage)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ArtifactPathResponse(**payload)


@app.delete(
    "/api/artifacts/{project_id}",
    response_model=ArtifactDeleteResponse,
    summary="删除项目（含目录与全部产物）",
    tags=["数据项目"],
)
def delete_artifact(project_id: str) -> ArtifactDeleteResponse:
    """删除项目目录与产物，成功返回 ``{"deleted": true}``；不存在 → **404**。"""
    try:
        artifacts_service.get_service().delete_project(project_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArtifactDeleteResponse(deleted=True)


# --------------------------------------------------------------------------- #
# 单独评论（`/api/solo-comments` 系列，契约 docs/API契约.md 4.5.2）
#
# 只新增「把预测结果存起来」的路径，既有 /api/predict 系列语义完全不变。
# --------------------------------------------------------------------------- #
@app.post(
    "/api/solo-comments",
    response_model=SoloCommentItem,
    summary="保存一条单独评论（左栏「保存到左栏」开关）",
    tags=["单独评论"],
)
def create_solo_comment(payload: SoloCreateRequest) -> SoloCommentItem:
    """保存由前端「单条预测」页提交的一条结果。

    ``label`` 必须是 ``positive`` / ``negative`` / ``neutral``，``confidence`` ∈ [0, 1]，
    ``text`` 非空（纯空白 → **400**）；``probs`` 与 ``POST /api/predict`` 的返回一致。
    """
    try:
        item = artifacts_service.get_service().create_solo(payload.model_dump())
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SoloCommentItem(**item)


@app.get(
    "/api/solo-comments",
    response_model=SoloListResponse,
    summary="单独评论列表（按 created_at 倒序）",
    tags=["单独评论"],
)
def list_solo_comments(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_SOLO_LIMIT, ge=1, description=f"每页条数，上限 {MAX_SOLO_LIMIT}"),
) -> SoloListResponse:
    """返回 ``{"total","offset","limit","items"}``，最新在前；``limit`` 越界 → **400**。"""
    try:
        payload = artifacts_service.get_service().list_solo(offset=offset, limit=limit)
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SoloListResponse(**payload)


@app.delete(
    "/api/solo-comments/{comment_id}",
    response_model=SoloDeleteResponse,
    summary="删除一条单独评论",
    tags=["单独评论"],
)
def delete_solo_comment(comment_id: str) -> SoloDeleteResponse:
    """成功返回 ``{"deleted": true, "deleted_count": 1}``；不存在 → **404**。"""
    try:
        payload = artifacts_service.get_service().delete_solo(comment_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SoloDeleteResponse(**payload)


@app.delete(
    "/api/solo-comments",
    response_model=SoloDeleteResponse,
    summary="批量删除单独评论（ids=a,b）",
    tags=["单独评论"],
)
def delete_solo_comments(
    ids: str | None = Query(None, description="逗号分隔的评论 id，形如 ids=c_...,c_..."),
) -> SoloDeleteResponse:
    """成功返回 ``{"deleted": true, "deleted_count": n}``（**幂等**）。

    ``ids`` 缺省 / 为空 / 传入的 id 不存在 → 200 且 ``deleted_count`` 为真实删除条数（可为 0），
    不会因为「没删到」报错（前端「清空全部」在列表为空时也会安全通过）；
    只有 id 形态非法（不匹配 ``^c_…``）才 → **400**。
    """
    try:
        payload = artifacts_service.get_service().delete_solo_many(ids)
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SoloDeleteResponse(**payload)


if __name__ == "__main__":  # pragma: no cover - 便于 `python -m src.inference.app` 直接启动
    import uvicorn

    uvicorn.run(
        "src.inference.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
