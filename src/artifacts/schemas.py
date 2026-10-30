"""`/api/artifacts` 与 `/api/solo-comments` 的 Pydantic 模型（`docs/API契约.md` 第四点五节的代码化）。

**字段名严格照契约实现，不得改名**：`response_model` 会按这里的字段裁剪响应，
因此项目记录里多存的内部字段（`run_id` / `seq` / `pipeline` 等）不会泄漏到接口响应里。

本文件同时锁定几处契约枚举，路由层与 service 层共用，避免出现第二份定义：

- 项目状态：``queued`` | ``processing`` | ``completed`` | ``failed``；
- 阶段：``initial`` | ``clean`` | ``excluded``（固定枚举，**对外绝不接受路径**）；
- 情感标签：``positive`` | ``negative`` | ``neutral``；
- 降级方法名：``rule_placeholder``（真实模型不可用时的规则占位降级；模型可用时
  为模型名 ``textcnn`` / ``bilstm_attention``，见 `src/inference/service.py`）。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from ..inference.service import METHOD_RULE_PLACEHOLDER

# --------------------------------------------------------------------------- #
# 枚举与常量（契约锁定）
# --------------------------------------------------------------------------- #
#: 项目 ID 规则：`p_` + 日期 + 时分 + 6 位十六进制（服务端生成，客户端不可构造路径）
PROJECT_ID_PATTERN = r"^p_\d{8}_\d{4}_[0-9a-f]{6}$"

#: 单独评论 ID 规则：`c_` + 日期 + 时分 + 6 位十六进制
SOLO_ID_PATTERN = r"^c_\d{8}_\d{4}_[0-9a-f]{6}$"

PROJECT_ID_RE = re.compile(PROJECT_ID_PATTERN)
SOLO_ID_RE = re.compile(SOLO_ID_PATTERN)

#: 项目状态枚举（契约锁定）
PROJECT_STATUSES: tuple[str, ...] = ("queued", "processing", "completed", "failed")

#: 「进行中」状态（重新分析 / 删除时用于并发判断）
RUNNING_STATUSES: tuple[str, ...] = ("queued", "processing")

#: 数据阶段枚举（契约锁定，顺序即左栏展示顺序）
STAGE_KEYS: tuple[str, ...] = ("initial", "clean", "excluded")

#: 阶段展示名（契约锁定：初始数据 / 筛选后结果 / 排除记录）
STAGE_LABELS: dict[str, str] = {
    "initial": "初始数据",
    "clean": "筛选后结果",
    "excluded": "排除记录",
}

#: 情感标签枚举（契约锁定）
SENTIMENT_LABELS: tuple[str, ...] = ("positive", "negative", "neutral")

#: 降级方法名（在本模块转出）：真实模型不可用时才出现的规则 / 词典占位来源。
#: 常量本身定义在 `src.inference.service.METHOD_RULE_PLACEHOLDER`（预测来源的唯一真相），
#: 这里只做转出，保证数据项目与 `POST /api/predict` 用的是同一个字符串。

#: 上传允许的后缀（契约第四节五）
ALLOWED_SUFFIXES: tuple[str, ...] = (".csv", ".txt", ".tsv", ".xlsx")

#: 单个文件大小上限 20 MB / 合计上限 50 MB（契约锁定）
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

#: 未自定义名称时的项目显示名（契约锁定）
DEFAULT_PROJECT_NAME = "未自定义数据"

#: 表格分页默认值 / 上限（契约锁定：limit 上限 500，默认 100）
DEFAULT_TABLE_LIMIT = 100
MAX_TABLE_LIMIT = 500

#: 逐条评论分页默认值 / 上限（契约：默认 50）
DEFAULT_COMMENTS_LIMIT = 50
MAX_COMMENTS_LIMIT = 500

#: 单独评论默认分页（契约：默认 50）
DEFAULT_SOLO_LIMIT = 50
MAX_SOLO_LIMIT = 500

#: 表格落盘的统一编码（契约 table 响应里的 `encoding` 字段）
CSV_ENCODING = "utf-8-sig"

#: 左栏三段的 key / label（契约 4.5.1 的 `groups[]`）
GROUP_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("projects", "数据项目"),
    ("solo", "单独评论"),
    ("videos", "视频任务"),
)


# --------------------------------------------------------------------------- #
# 项目对象（契约 4.5.1）
# --------------------------------------------------------------------------- #
class SourceItem(BaseModel):
    """`sources[]`：一份上传原件的元信息。"""

    filename: str = Field(..., description="上传时的原始文件名（只取 basename）")
    rows: int = Field(..., ge=0, description="该文件解析出的数据行数")
    size: int = Field(..., ge=0, description="该文件的字节数")


class StageItem(BaseModel):
    """`stages[]`：一个数据阶段的行列数（未处理完时均为 0）。"""

    key: str = Field(..., description="initial | clean | excluded")
    label: str = Field(..., description="初始数据 | 筛选后结果 | 排除记录")
    rows: int = Field(0, ge=0, description="数据行数（不含表头）")
    columns: int = Field(0, ge=0, description="列数")


class SentimentCounts(BaseModel):
    """项目对象里的 `sentiment`（仅完成时非 null；不含占比）。"""

    total: int = Field(..., ge=0)
    positive: int = Field(..., ge=0)
    negative: int = Field(..., ge=0)
    neutral: int = Field(..., ge=0)


class ProjectItem(BaseModel):
    """项目对象（`GET /api/artifacts` 列表项、`GET /api/artifacts/{id}`、上传与重新分析响应）。"""

    id: str = Field(..., description="服务端生成的项目 id（形如 p_20261016_1530_a1b2c3）")
    name: str = Field(..., description="项目显示名；未自定义时为「未自定义数据」")
    created_at: str = Field(..., description="创建时间（ISO8601，带时区）")
    status: str = Field(..., description="queued | processing | completed | failed")
    progress: int = Field(..., ge=0, le=100, description="进度 0~100")
    message: str = Field(..., description="当前状态的中文说明")
    sources: list[SourceItem] = Field(default_factory=list)
    stages: list[StageItem] = Field(default_factory=list)
    sentiment: SentimentCounts | None = Field(None, description="未完成时为 null")
    method: str = Field(
        METHOD_RULE_PLACEHOLDER,
        description=(
            "情感来源：真实模型名（textcnn / bilstm_attention）；"
            "模型不可用时降级为 rule_placeholder（规则占位）。默认值仅为契约缺省兼容"
        ),
    )
    error: str | None = Field(None, description="失败原因；未失败时为 null")


class ArtifactGroup(BaseModel):
    """左栏一段：`{key, label, items}`。"""

    key: str
    label: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactListResponse(BaseModel):
    """`GET /api/artifacts`：projects / solo / videos 三段。"""

    groups: list[ArtifactGroup]


# --------------------------------------------------------------------------- #
# 上传 / 阶段 / 表格 / 逐条评论 / 聚合分析
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    """`POST /api/artifacts/{id}/analyze` 请求体（可省略）。

    当前只支持自动判定（无标签 → `clean`），因此只接受 `auto` / `clean` 两个取值；
    其余取值返回 400（避免前端误以为支持 `train` 模式）。
    """

    mode: str = Field("auto", description="auto（默认）或 clean")


class StageListResponse(BaseModel):
    """`GET /api/artifacts/{id}/stages`：三段阶段清单。"""

    stages: list[StageItem]


class TableResponse(BaseModel):
    """`GET /api/artifacts/{id}/table`：某个阶段的表格分页。"""

    stage: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    total_rows: int = Field(0, ge=0)
    offset: int = Field(0, ge=0)
    limit: int = Field(0, ge=0)
    encoding: str = Field(CSV_ENCODING, description="固定为 utf-8-sig（落盘统一带 BOM）")


class CommentItem(BaseModel):
    """`comments[].items[]`：逐条评论的情感结果（真实模型优先，加载失败为规则占位降级）。"""

    index: int = Field(..., ge=0, description="在 clean 表里的行序号（从 0 开始）")
    text: str = Field(..., description="原文（取 text_raw，与批量预测接口语义一致）")
    label: str = Field(..., description="positive | negative | neutral")
    confidence: float = Field(..., ge=0.0, le=1.0)
    probs: dict[str, float] = Field(
        default_factory=dict,
        description="两类概率（模型 softmax 输出；降级时为规则预测器输出）",
    )


class CommentsResponse(BaseModel):
    """`GET /api/artifacts/{id}/comments`：逐条评论分页（可按 label 过滤）。"""

    total: int = Field(0, ge=0, description="过滤后的总条数")
    offset: int = Field(0, ge=0)
    limit: int = Field(0, ge=0)
    items: list[CommentItem] = Field(default_factory=list)


class AnalysisSentiment(BaseModel):
    """`analysis.sentiment`：计数 + 占比（三个占比之和约等于 1）。"""

    positive: int = Field(0, ge=0)
    negative: int = Field(0, ge=0)
    neutral: int = Field(0, ge=0)
    positive_ratio: float = Field(0.0, ge=0.0, le=1.0)
    negative_ratio: float = Field(0.0, ge=0.0, le=1.0)
    neutral_ratio: float = Field(0.0, ge=0.0, le=1.0)


class KeywordItem(BaseModel):
    """`analysis.keywords.*[]`：词云直接用 `count` 或 `weight` 渲染，后端不出图。"""

    word: str
    count: int = Field(..., ge=0)
    weight: float = Field(..., ge=0.0, le=1.0)


class KeywordsBlock(BaseModel):
    """`analysis.keywords`。"""

    positive: list[KeywordItem] = Field(default_factory=list)
    negative: list[KeywordItem] = Field(default_factory=list)


class LengthStats(BaseModel):
    """`analysis.length`：原文长度分布（字符数）。"""

    mean: float = Field(0.0, ge=0.0)
    median: float = Field(0.0, ge=0.0)
    p90: float = Field(0.0, ge=0.0)
    max: int = Field(0, ge=0)


class SampleItem(BaseModel):
    """`analysis.sample[]`：抽样展示的逐条结果。"""

    index: int = Field(..., ge=0)
    text: str
    label: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class AnalysisResponse(BaseModel):
    """`GET /api/artifacts/{id}/analysis`：聚合情感分析。"""

    method: str = Field(
        METHOD_RULE_PLACEHOLDER,
        description=(
            "情感来源：真实模型名（textcnn / bilstm_attention）；"
            "模型不可用时降级为 rule_placeholder（规则占位）。默认值仅为契约缺省兼容"
        ),
    )
    total: int = Field(0, ge=0)
    sentiment: AnalysisSentiment
    keywords: KeywordsBlock = Field(default_factory=KeywordsBlock)
    length: LengthStats = Field(default_factory=LengthStats)
    sample: list[SampleItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 打开 / 删除
# --------------------------------------------------------------------------- #
class OpenRequest(BaseModel):
    """`POST /api/artifacts/{id}/open` 请求体（可省略，默认打开 clean 阶段）。"""

    stage: str = Field("clean", description="initial | clean | excluded")


class OpenResponse(BaseModel):
    """`POST /api/artifacts/{id}/open` 响应。"""

    opened: bool = Field(..., description="固定为 true（失败时返回 400 / 404）")
    path: str = Field(..., description="被打开的 CSV 的绝对路径（位于 data/uploads/ 之内）")


class ArtifactDeleteResponse(BaseModel):
    """`DELETE /api/artifacts/{id}` 响应。"""

    deleted: bool = Field(..., description="固定为 true（项目不存在时返回 404）")


class ArtifactPathResponse(BaseModel):
    """`GET /api/artifacts/{id}/path` 响应：**只读**返回路径。

    **无副作用**：绝不调用 opener、不打开任何程序。界面的「复制文件地址」用它，
    避免为了拿路径而真的弹一次 Excel。
    """

    stage: str = Field(..., description="initial | clean | excluded")
    path: str = Field(
        ...,
        description="该阶段 CSV 的绝对路径（解析后位于 data/uploads/ 或 data/processed/ 之内）",
    )
    exists: bool = Field(
        ...,
        description="文件当前是否存在；不存在也返回 200（便于前端提示「该阶段暂无文件」）",
    )


# --------------------------------------------------------------------------- #
# 单独评论（契约 4.5.2）
# --------------------------------------------------------------------------- #
class SoloCreateRequest(BaseModel):
    """`POST /api/solo-comments` 请求体。

    **刻意使用宽松类型**（``Any``）：契约要求「文本为空或仅空白返回 400」、
    「label 枚举 / confidence ∈ [0,1] 由后端做基本校验」，因此校验放在
    service 层（`ArtifactValidationError` → HTTP 400），而不是让 Pydantic
    提前返回 422。
    """

    text: Any = Field(None, description="评论文本（非空，纯空白返回 400）")
    label: Any = Field(None, description="positive | negative | neutral")
    confidence: Any = Field(None, description="置信度，∈ [0, 1]")
    probs: Any = Field(None, description="两类概率，与 POST /api/predict 的返回一致")
    method: Any = Field(
        None, description="情感来源：真实模型名（textcnn / bilstm_attention）或 rule_placeholder"
    )


class SoloCommentItem(BaseModel):
    """单条评论对象（左栏「单独评论」段的 items）。"""

    id: str = Field(..., description="服务端生成的 id（形如 c_20261016_1531_9f8e7d）")
    text: str
    label: str = Field(..., description="positive | negative | neutral")
    confidence: float = Field(..., ge=0.0, le=1.0)
    probs: dict[str, float] = Field(default_factory=dict)
    method: str = Field(
        METHOD_RULE_PLACEHOLDER,
        description="情感来源：真实模型名或 rule_placeholder（默认值仅为缺省兼容）",
    )
    created_at: str = Field(..., description="创建时间（ISO8601，带时区）")


class SoloListResponse(BaseModel):
    """`GET /api/solo-comments`：按 `created_at` 倒序。"""

    total: int = Field(0, ge=0)
    offset: int = Field(0, ge=0)
    limit: int = Field(0, ge=0)
    items: list[SoloCommentItem] = Field(default_factory=list)


class SoloDeleteResponse(BaseModel):
    """`DELETE /api/solo-comments/{id}` 与批量删除响应。

    - 单条删除：评论不存在 → 404；
    - 批量删除（`?ids=a,b`）：**幂等**，`ids` 缺省 / 为空 / 不存在都返回 200，
      此时 `deleted_count` 为真实删除条数（可为 0）。
    """

    deleted: bool = Field(..., description="固定为 true（单条删除不存在时返回 404）")
    deleted_count: int = Field(..., ge=0, description="实际删除的条数（批量删除可为 0）")
