"""`/api/videos` 系列的 Pydantic 请求 / 响应模型（`docs/API契约.md` 的代码化）。

**字段名严格照契约实现，不得改名**；`response_model` 会按这里的字段裁剪响应，
因此 store 里多存的内部字段（如 `run_id` / `seq`）不会泄漏到接口响应里。

契约补充说明（本实现做出的、明确记录在案的选择）：

- 列表项与提交响应**只含契约里列出的字段**，不多不少；
- 详情里额外带一个 **顶层** ``placeholder`` 字段：标注 detail 里的**评论数据**是否来自
  占位（未开启真实爬取）。真实爬取为 opt-in（任务设置 `real_crawl=true` / `VIDEO_REAL_CRAWL=1`），
  采集到真实评论（或接入真实爬取后）为 ``false``。它是**新增字段**而非改名，前端可忽略；
  该字段只表示评论数据来源，**不表示情感来源**（情感统一走真实模型 / 规则降级）；
- 详情里额外带一个 **顶层** ``pipeline`` 对象（additive，spec 3.4）：账号模式、
  自动连续开关、下一步动作与本次任务的采集参数快照（不含 Cookie），未完成时也返回；
- 状态枚举新增两个**等待态** ``crawled`` / ``cleaned``（spec 3.1），旧客户端可忽略；
- 详情未完成时（`queued`/`crawling`/`processing`/`crawled`/`cleaned`/`failed`）：
  `sentiment` 为 ``null``，`trend` / `top_comments` 为空数组（`[]`），`keywords`
  保持对象结构但两个数组为空，`analysis.length_stats` 为 ``null``；`model` 是
  「当前生效模型」的静态信息，与视频无关，因此始终返回对象。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

#: BV 号校验规则（前后端一致，契约锁定）
BVID_PATTERN = r"^BV[0-9A-Za-z]{10}$"
BVID_RE = re.compile(BVID_PATTERN)

#: 任务状态枚举（契约锁定；`crawled` / `cleaned` 为 2026-10-17 新增的等待态）
VIDEO_STATUSES: tuple[str, ...] = (
    "queued",
    "crawling",
    "processing",
    "crawled",
    "cleaned",
    "completed",
    "failed",
)

#: 「进行中」状态（重复提交这些状态的任务返回 409；重启时会被重新入队）
RUNNING_STATUSES: tuple[str, ...] = ("queued", "crawling", "processing")

#: 「等待操作」状态（分步流水线在阶段边界暂停；不会被自动轮询 / 重启重跑）
WAITING_STATUSES: tuple[str, ...] = ("crawled", "cleaned")

#: 子步骤状态枚举（契约锁定）
PHASE_STATUSES: tuple[str, ...] = ("pending", "active", "done", "failed")

#: 子步骤名称（契约锁定，顺序即流水线顺序）
PHASE_NAMES: tuple[str, ...] = ("采集评论", "文本清洗", "情感推理", "统计分析")

VideoStatus = Literal[
    "queued",
    "crawling",
    "processing",
    "crawled",
    "cleaned",
    "completed",
    "failed",
]
PhaseStatus = Literal["pending", "active", "done", "failed"]
CommentLabel = Literal["positive", "negative"]
AccountMode = Literal["anonymous", "account"]
SortMode = Literal["time", "hot"]


# --------------------------------------------------------------------------- #
# 请求 / 提交类响应
# --------------------------------------------------------------------------- #
class VideoSubmitRequest(BaseModel):
    """``POST /api/videos`` 请求体。

    注意：**不做 Pydantic `pattern` 校验**，否则非法 BV 号会得到 422；
    契约要求格式非法返回 **400**，因此正则校验放在 service 层（`InvalidBvidError`）。

    ``settings`` 可选（spec 3.2）：省略 = 旧行为（一次跑完，不暂停）；
    传入（即使为空对象）= 启用分步流水线，与已保存设置部分合并后作为本任务快照。
    Cookie 永远取服务端设置，**不随任务传**；这里保持宽松的 ``dict`` 类型以免
    非法设置被 Pydantic 直接变成 422，校验在 service 层完成并映射为 400。
    """

    bvid: str = Field(
        ...,
        description="B 站 BV 号，形如 BV1xx411c7mD（正则 ^BV[0-9A-Za-z]{10}$）",
        examples=["BV1xx411c7mD"],
    )
    settings: dict[str, Any] | None = Field(
        None,
        description="可选的任务级设置（账号模式 / 采集参数 / 自动流程），省略即旧行为",
        examples=[
            {
                "account": {"mode": "anonymous"},
                "crawl": {"real_crawl": True, "max_pages": 2},
                "pipeline": {"auto_clean": False, "auto_analyze": False},
            }
        ],
    )


class VideoSubmitResponse(BaseModel):
    """``POST /api/videos`` 与 ``POST /api/videos/{bvid}/retry`` 的响应（结构相同）。"""

    bvid: str = Field(..., description="BV 号")
    status: VideoStatus = Field(..., description="任务状态；提交/重试成功时为 queued")
    progress: int = Field(..., ge=0, le=100, description="进度 0~100")
    created_at: str = Field(..., description="创建时间（ISO8601，带时区）")


class PhaseItem(BaseModel):
    """`phases[]` 子步骤。"""

    name: str = Field(..., description="子步骤名称")
    status: PhaseStatus = Field(..., description="pending | active | done | failed")


class VideoListItem(BaseModel):
    """``GET /api/videos`` 列表中的单个条目（字段与契约完全一致）。"""

    bvid: str
    title: str | None = Field(None, description="尚未爬到时为 null")
    owner: str | None = Field(None, description="尚未爬到时为 null")
    cover_url: str | None = Field(None, description="尚未爬到时为 null")
    status: VideoStatus
    progress: int = Field(..., ge=0, le=100)
    message: str
    phases: list[PhaseItem]
    comment_count: int = Field(..., ge=0, description="已入库评论数（进行中也可逐步增长）")
    created_at: str
    updated_at: str


class VideoListResponse(BaseModel):
    """``GET /api/videos`` 响应（按 ``created_at`` 倒序，最新在前）。"""

    total: int = Field(..., ge=0)
    videos: list[VideoListItem]


class VideoDeleteResponse(BaseModel):
    """``DELETE /api/videos/{bvid}`` 响应。"""

    deleted: bool = Field(..., description="固定为 true（任务不存在时返回 404）")


# --------------------------------------------------------------------------- #
# 详情类响应
# --------------------------------------------------------------------------- #
class VideoMeta(BaseModel):
    """`detail.video`。"""

    bvid: str
    title: str | None
    owner: str | None
    cover_url: str | None
    pubdate: str | None = Field(None, description="未完成时为 null")
    crawled_at: str | None = Field(None, description="未完成时为 null")
    comment_total: int = Field(0, ge=0)


class LengthStats(BaseModel):
    """`detail.analysis.length_stats`（仅完成时非 null）。"""

    avg_chars: float = Field(..., ge=0)
    max_chars: int = Field(..., ge=0)


class AnalysisInfo(BaseModel):
    """`detail.analysis`。"""

    status: VideoStatus
    progress: int = Field(..., ge=0, le=100)
    message: str
    length_stats: LengthStats | None = None


class SentimentStats(BaseModel):
    """`detail.sentiment`（未完成时为 null；`*_ratio` 之和约等于 1）。"""

    total: int = Field(..., ge=0)
    positive: int = Field(..., ge=0)
    negative: int = Field(..., ge=0)
    neutral: int = Field(..., ge=0)
    positive_ratio: float = Field(..., ge=0.0, le=1.0)
    negative_ratio: float = Field(..., ge=0.0, le=1.0)
    neutral_ratio: float = Field(..., ge=0.0, le=1.0)


class TrendPoint(BaseModel):
    """`detail.trend[]`：按天（``YYYY-MM-DD``）或按小时（``YYYY-MM-DD HH:00``）分桶。"""

    bucket: str
    positive: int = Field(..., ge=0)
    negative: int = Field(..., ge=0)
    neutral: int = Field(..., ge=0)


class KeywordItem(BaseModel):
    """`detail.keywords.*[]`：词云直接用 ``count`` 或 ``weight`` 渲染，后端不出图。"""

    word: str
    count: int = Field(..., ge=0)
    weight: float = Field(..., ge=0.0, le=1.0)


class KeywordsBlock(BaseModel):
    """`detail.keywords`（未完成时两个数组均为空）。"""

    positive: list[KeywordItem]
    negative: list[KeywordItem]


class ModelBlock(BaseModel):
    """`detail.model`：当前生效模型的测试集指标。"""

    name: str
    accuracy: float = Field(..., ge=0.0, le=1.0)
    precision: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    f1: float = Field(..., ge=0.0, le=1.0)


class TopComment(BaseModel):
    """`detail.top_comments.*[]`：每种情感取点赞最高的若干条（建议 ≤ 10）。"""

    text: str
    like: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    label: CommentLabel


class TopCommentsBlock(BaseModel):
    """`detail.top_comments`（未完成时两个数组均为空）。"""

    positive: list[TopComment]
    negative: list[TopComment]


class PipelineCrawlInfo(BaseModel):
    """``detail.pipeline.crawl``：本次任务使用的采集参数快照（不含 Cookie）。"""

    real_crawl: bool = True
    max_pages: int = Field(1, ge=0, le=50)
    max_sub_pages: int = Field(1, ge=0, le=20)
    page_size: int = Field(20, ge=1, le=50)
    include_replies: bool = False
    sort_mode: SortMode = "time"
    max_items: int = Field(200, ge=0)
    delay_seconds: float = Field(2.5, ge=0.0)
    timeout_seconds: float = Field(15.0, gt=0.0)
    require_login: bool = False
    init_buvid: bool = False


class PipelineInfo(BaseModel):
    """``detail.pipeline``（顶层新增对象，additive；未完成时也返回）。

    Attributes:
        account_mode: 本任务使用的账号模式（``anonymous`` / ``account``）。
        auto_clean: 采集完成后是否自动清洗。
        auto_analyze: 清洗完成后是否自动情感分析。
        next_action: 等待态下的下一步动作：``clean`` / ``analyze``；其余状态为 ``null``。
        crawl: 本任务的采集参数快照（不含 Cookie）。
    """

    account_mode: AccountMode = "anonymous"
    auto_clean: bool = False
    auto_analyze: bool = False
    next_action: Literal["clean", "analyze"] | None = None
    crawl: PipelineCrawlInfo = Field(default_factory=PipelineCrawlInfo)


class VideoDetailResponse(BaseModel):
    """``GET /api/videos/{bvid}/detail`` 响应。

    ``analysis.status`` 为 ``failed`` 时 ``error`` 给出失败原因，其余数据字段可为空；
    未完成时**不报错**，仍返回 HTTP 200。
    """

    video: VideoMeta
    analysis: AnalysisInfo
    sentiment: SentimentStats | None = None
    trend: list[TrendPoint] = Field(default_factory=list)
    keywords: KeywordsBlock
    model: ModelBlock
    top_comments: TopCommentsBlock
    error: str | None = None
    placeholder: bool = Field(
        True,
        description=(
            "评论数据来源标注：true = 当前 detail 使用占位评论数据（未开启真实爬取）；"
            "false = 真实爬取到的评论。新增字段，前端可忽略；"
            "只表示评论数据来源，不表示情感来源（情感走统一真实模型，加载失败降级规则占位）。"
        ),
    )
    pipeline: PipelineInfo = Field(
        default_factory=PipelineInfo,
        description="新增字段（additive）：账号模式 / 自动连续开关 / 下一步动作 / 采集参数快照",
    )
