"""`/api/videos` 系列的 Pydantic 请求 / 响应模型（`docs/API契约.md` 的代码化）。

**字段名严格照契约实现，不得改名**；`response_model` 会按这里的字段裁剪响应，
因此 store 里多存的内部字段（如 `run_id` / `seq`）不会泄漏到接口响应里。

契约补充说明（本实现做出的、明确记录在案的选择）：

- 列表项与提交响应**只含契约里列出的字段**，不多不少；
- 详情里额外带一个 **顶层** ``placeholder: true`` 字段：诚实标注当前 detail 由
  占位逻辑生成（真实爬虫已在 `src/crawler/` 实现，但 `/api/videos` 尚未接入）。它是
  **新增字段**而非改名，前端可忽略；真实爬虫接入后置为 ``false``；
- 详情未完成时（`queued`/`crawling`/`processing`/`failed`）：`sentiment` 为 ``null``，
  `trend` / `top_comments` 为空数组（`[]`），`keywords` 保持对象结构但两个数组为空，
  `analysis.length_stats` 为 ``null``；`model` 是「当前生效模型」的静态信息，
  与视频无关，因此始终返回对象。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

#: BV 号校验规则（前后端一致，契约锁定）
BVID_PATTERN = r"^BV[0-9A-Za-z]{10}$"
BVID_RE = re.compile(BVID_PATTERN)

#: 任务状态枚举（契约锁定）
VIDEO_STATUSES: tuple[str, ...] = ("queued", "crawling", "processing", "completed", "failed")

#: 「进行中」状态（重复提交这些状态的任务返回 409）
RUNNING_STATUSES: tuple[str, ...] = ("queued", "crawling", "processing")

#: 子步骤状态枚举（契约锁定）
PHASE_STATUSES: tuple[str, ...] = ("pending", "active", "done", "failed")

#: 子步骤名称（契约锁定，顺序即流水线顺序）
PHASE_NAMES: tuple[str, ...] = ("采集评论", "文本清洗", "情感推理", "统计分析")

VideoStatus = Literal["queued", "crawling", "processing", "completed", "failed"]
PhaseStatus = Literal["pending", "active", "done", "failed"]
CommentLabel = Literal["positive", "negative"]


# --------------------------------------------------------------------------- #
# 请求 / 提交类响应
# --------------------------------------------------------------------------- #
class VideoSubmitRequest(BaseModel):
    """``POST /api/videos`` 请求体。

    注意：**不做 Pydantic `pattern` 校验**，否则非法 BV 号会得到 422；
    契约要求格式非法返回 **400**，因此正则校验放在 service 层（`InvalidBvidError`）。
    """

    bvid: str = Field(
        ...,
        description="B 站 BV 号，形如 BV1xx411c7mD（正则 ^BV[0-9A-Za-z]{10}$）",
        examples=["BV1xx411c7mD"],
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
            "诚实标注：detail 由占位逻辑生成（真实爬虫尚未接入）。"
            "新增字段，前端可忽略；真实数据接入后为 false。"
        ),
    )
