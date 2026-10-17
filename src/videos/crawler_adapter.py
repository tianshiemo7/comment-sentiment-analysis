"""真实爬虫适配层：把 `src.crawler.fetch_video_comments` 接到 ``/api/videos`` 流水线。

**opt-in（默认关闭）**：只有 ``VIDEO_REAL_CRAWL=1``（或测试里显式传
``PipelineConfig(real_crawl=True)``）时才会真正联网采集；默认仍走
`src/videos/placeholder.py` 的占位数据，现有行为与测试保持不变。

环境变量（全部可选；**缺失或非法值一律回退默认，绝不抛异常**）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_REAL_CRAWL` | `0` | `1/true/yes/on` 时启用真实采集（其余为 `0`） |
| `VIDEO_CRAWL_MAX_PAGES` | `1` | 最多采集多少页一级评论（合法范围 1~50） |
| `VIDEO_CRAWL_MAX_ITEMS` | `200` | 本次最多新增多少条评论（`0` 表示不限制） |
| `VIDEO_CRAWL_DELAY` | `2.5` | 请求最小间隔（秒，不得为负；默认值满足限速合规） |
| `VIDEO_CRAWL_INCLUDE_REPLIES` | `0` | 是否采集楼中楼 |
| `VIDEO_CRAWL_TIMEOUT` | `15` | 单次请求超时（秒，须为正数） |
| `VIDEO_CRAWL_PAGE_SIZE` | `20` | 一级评论每页条数（1~50） |
| `VIDEO_CRAWL_SORT_MODE` | `time` | 一级评论排序：`time` / `hot` |
| `VIDEO_CRAWL_INIT_BUVID` | `0` | 是否初始化 buvid3/buvid4 设备指纹 |
| `VIDEO_CRAWL_REQUIRE_LOGIN` | `0` | 未登录时直接失败 |

任务级设置（Web 设置页 / ``POST /api/videos`` 的 ``settings``）优先于环境变量：
:class:`VideoCrawlConfig` 的 ``cookie / page_size / sort_mode / init_buvid /
require_login`` 由分步流水线逐任务构造并透传，**Cookie 不落日志、不进任务索引**
（``cookie`` 字段 ``repr=False``；错误信息由 `settings.redact_secrets` 兜底）。

失败语义：:func:`collect_video_comments` 把底层任何异常统一包装成
:class:`CrawlAdapterError`（保留原始异常链与可读原因），由流水线把任务写成
``failed`` 并带上 ``error``，**绝不会让任务卡在 ``crawling``**。

离线测试：:func:`collect_video_comments` 支持注入 ``fetcher``；测试可同时
monkeypatch ``src.crawler.fetch_video_comments``，全流程不触网。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .schemas import BVID_RE

# --------------------------------------------------------------------------- #
# 环境变量与默认值
# --------------------------------------------------------------------------- #
ENV_REAL_CRAWL = "VIDEO_REAL_CRAWL"
ENV_CRAWL_MAX_PAGES = "VIDEO_CRAWL_MAX_PAGES"
ENV_CRAWL_MAX_ITEMS = "VIDEO_CRAWL_MAX_ITEMS"
ENV_CRAWL_DELAY = "VIDEO_CRAWL_DELAY"
ENV_CRAWL_INCLUDE_REPLIES = "VIDEO_CRAWL_INCLUDE_REPLIES"
ENV_CRAWL_TIMEOUT = "VIDEO_CRAWL_TIMEOUT"
ENV_CRAWL_PAGE_SIZE = "VIDEO_CRAWL_PAGE_SIZE"
ENV_CRAWL_SORT_MODE = "VIDEO_CRAWL_SORT_MODE"
ENV_CRAWL_INIT_BUVID = "VIDEO_CRAWL_INIT_BUVID"
ENV_CRAWL_REQUIRE_LOGIN = "VIDEO_CRAWL_REQUIRE_LOGIN"

#: 默认只采 1 页一级评论（安全、可预期，避免一次任务打太多请求）
DEFAULT_MAX_PAGES = 1
#: 默认不采楼中楼
DEFAULT_INCLUDE_REPLIES = False
#: 每条根评论最多采 1 页楼中楼（仅在 include_replies=True 时生效）
DEFAULT_MAX_SUB_PAGES = 1
#: 默认单次任务最多入库 200 条（0 在爬虫里表示「不限制」，这里给安全上限）
DEFAULT_MAX_ITEMS = 200
#: 默认请求间隔 2.5 秒（与 `src.crawler.bilibili` 的默认限速一致）
DEFAULT_DELAY_SECONDS = 2.5
#: 默认单次请求超时 15 秒
DEFAULT_TIMEOUT = 15.0
#: 一级评论每页条数（与爬虫 CLI 的 ``--page-size`` 默认值一致）
DEFAULT_PAGE_SIZE = 20
#: 一级评论排序模式：time（按时间）/ hot（按热度）
DEFAULT_SORT_MODE = "time"
#: 是否初始化 buvid3/buvid4 设备指纹（默认关闭，避免影响匿名可见量）
DEFAULT_INIT_BUVID = False
#: 是否要求登录态（默认关闭；开启后未登录直接失败）
DEFAULT_REQUIRE_LOGIN = False

#: 翻页 / 楼中楼翻页 / 页大小的合法上限（与爬虫 CLI 的校验保持一致）
MAX_PAGES_LIMIT = 50
MAX_SUB_PAGES_LIMIT = 20
MAX_PAGE_SIZE_LIMIT = 50

#: 排序模式 → 爬虫 ``mode`` 参数（time→2 / hot→3）
SORT_MODE_TO_CRAWL_MODE: dict[str, int] = {"time": 2, "hot": 3}

#: 布尔环境变量的真 / 假取值
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

#: 爬虫 CSV 的标准列（`src/crawler/bilibili.py:COLUMNS`），适配层输出保持同名
STANDARD_FIELDS: tuple[str, ...] = (
    "bvid",
    "aid",
    "video_title",
    "rpid",
    "level",
    "root_rpid",
    "parent_rpid",
    "text",
    "likes",
    "reply_count",
    "ctime_utc",
    "collected_at_utc",
)


class CrawlAdapterError(RuntimeError):
    """真实采集失败（原因可读，含底层异常类型与信息；保留 ``__cause__``）。"""


# --------------------------------------------------------------------------- #
# 环境变量解析（非法值回退默认，绝不抛异常）
# --------------------------------------------------------------------------- #
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    """读取整数环境变量；缺失、非法或越界时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    exclusive_minimum: bool = False,
) -> float:
    """读取浮点环境变量；缺失、非法（含 NaN / inf）或越界时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return default
    if exclusive_minimum:
        if value <= minimum:
            return default
    elif value < minimum:
        return default
    return value


def _env_choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    """读取枚举环境变量；缺失或不在允许集合内时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    return value if value in allowed else default


# --------------------------------------------------------------------------- #
# 采集配置
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VideoCrawlConfig:
    """真实采集参数（不可变；默认值即安全默认，测试可直接构造覆盖）。

    Attributes:
        max_pages: 最多采集多少页一级评论（0~50；``0`` 表示不限制）。
        include_replies: 是否采集楼中楼。
        max_sub_pages: 每条根评论最多采集多少页楼中楼（0~20；``0`` 表示不限制）。
        max_items: 本次最多新增多少条评论（``0`` 表示不限制，与爬虫语义一致）。
        delay_seconds: 请求最小间隔（秒，不得为负；默认 2.5 满足限速合规）。
        timeout: 单次请求超时（秒，须为正数）。
        real_crawl: 是否启用真实采集（对应 ``VIDEO_REAL_CRAWL``；由
            :meth:`PipelineConfig.from_env` 传给流水线，默认 ``False``）。
        page_size: 一级评论每页请求条数（1~50，默认 20）。
        sort_mode: 一级评论排序：``time``（按时间）/ ``hot``（按热度）。
        init_buvid: 是否初始化 buvid3/buvid4 设备指纹。
        require_login: 为 ``True`` 时未登录直接失败（建议登录账号后开启）。
        cookie: 逐任务注入的 Cookie 串（与 ``BILI_COOKIE`` 同格式）；**不参与
            repr / 相等比较**，避免误写日志或任务索引。
    """

    max_pages: int = DEFAULT_MAX_PAGES
    include_replies: bool = DEFAULT_INCLUDE_REPLIES
    max_sub_pages: int = DEFAULT_MAX_SUB_PAGES
    max_items: int = DEFAULT_MAX_ITEMS
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    timeout: float = DEFAULT_TIMEOUT
    real_crawl: bool = False
    page_size: int = DEFAULT_PAGE_SIZE
    sort_mode: str = DEFAULT_SORT_MODE
    init_buvid: bool = DEFAULT_INIT_BUVID
    require_login: bool = DEFAULT_REQUIRE_LOGIN
    cookie: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not 0 <= int(self.max_pages) <= MAX_PAGES_LIMIT:
            raise ValueError(f"max_pages 必须在 0~{MAX_PAGES_LIMIT} 之间（0=不限）")
        if not 0 <= int(self.max_sub_pages) <= MAX_SUB_PAGES_LIMIT:
            raise ValueError(f"max_sub_pages 必须在 0~{MAX_SUB_PAGES_LIMIT} 之间（0=不限）")
        if not 1 <= int(self.page_size) <= MAX_PAGE_SIZE_LIMIT:
            raise ValueError(f"page_size 必须在 1~{MAX_PAGE_SIZE_LIMIT} 之间")
        if self.sort_mode not in SORT_MODE_TO_CRAWL_MODE:
            raise ValueError("sort_mode 只能是 time / hot")
        if int(self.max_items) < 0:
            raise ValueError("max_items 不能为负数（0 表示不限制）")
        if float(self.delay_seconds) < 0:
            raise ValueError("delay_seconds 不能为负数（限速是硬性要求）")
        if float(self.timeout) <= 0:
            raise ValueError("timeout 必须为正数")
        object.__setattr__(self, "cookie", str(self.cookie).strip() or None if self.cookie else None)

    @property
    def crawl_mode(self) -> int:
        """排序模式映射成爬虫的 ``mode`` 参数（``time→2`` / ``hot→3``）。"""
        return SORT_MODE_TO_CRAWL_MODE[self.sort_mode]

    @classmethod
    def from_env(cls) -> "VideoCrawlConfig":
        """按环境变量构造配置；任何非法值都回退到安全默认值（不抛异常）。"""
        return cls(
            max_pages=_env_int(
                ENV_CRAWL_MAX_PAGES, DEFAULT_MAX_PAGES, minimum=1, maximum=MAX_PAGES_LIMIT
            ),
            include_replies=_env_bool(ENV_CRAWL_INCLUDE_REPLIES, DEFAULT_INCLUDE_REPLIES),
            max_sub_pages=DEFAULT_MAX_SUB_PAGES,
            max_items=_env_int(ENV_CRAWL_MAX_ITEMS, DEFAULT_MAX_ITEMS, minimum=0),
            delay_seconds=_env_float(ENV_CRAWL_DELAY, DEFAULT_DELAY_SECONDS, minimum=0.0),
            timeout=_env_float(
                ENV_CRAWL_TIMEOUT, DEFAULT_TIMEOUT, minimum=0.0, exclusive_minimum=True
            ),
            real_crawl=_env_bool(ENV_REAL_CRAWL, False),
            page_size=_env_int(
                ENV_CRAWL_PAGE_SIZE,
                DEFAULT_PAGE_SIZE,
                minimum=1,
                maximum=MAX_PAGE_SIZE_LIMIT,
            ),
            sort_mode=_env_choice(
                ENV_CRAWL_SORT_MODE, DEFAULT_SORT_MODE, tuple(SORT_MODE_TO_CRAWL_MODE)
            ),
            init_buvid=_env_bool(ENV_CRAWL_INIT_BUVID, DEFAULT_INIT_BUVID),
            require_login=_env_bool(ENV_CRAWL_REQUIRE_LOGIN, DEFAULT_REQUIRE_LOGIN),
        )


# --------------------------------------------------------------------------- #
# 采集入口
# --------------------------------------------------------------------------- #
def collect_video_comments(
    bvid: str,
    config: VideoCrawlConfig | None = None,
    fetcher: Callable[..., list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """采集一个视频的评论并返回**标准化记录列表**。

    Args:
        bvid: 视频 BV 号（``^BV[0-9A-Za-z]{10}$``）。
        config: 采集参数；``None`` 时用 :class:`VideoCrawlConfig` 的默认值。
        fetcher: 可注入的采集函数（默认 ``src.crawler.fetch_video_comments``），
            签名需兼容 ``(bvid, *, max_pages=..., include_replies=...,
            max_sub_pages=..., max_items=..., delay=..., timeout=..., page_size=...,
            mode=..., cookie=..., init_buvid=..., require_login=...)``。
            测试用它完全离线。

    Returns:
        标准化记录列表：字段名与爬虫 CSV 的 :data:`STANDARD_FIELDS` 一致，
        ``likes`` / ``reply_count`` / ``level`` 为 ``int``，``text`` 已 strip。
        没有评论时返回 ``[]``（由调用方决定是否视为失败）。

    Raises:
        CrawlAdapterError: BV 号非法、底层采集抛错或返回了非法结构；
            错误信息带可读原因（保留 ``__cause__`` 便于排查）。
    """
    crawl_config = config or VideoCrawlConfig()
    if not isinstance(bvid, str) or not BVID_RE.match(bvid):
        raise CrawlAdapterError(f"非法的 BV 号：{bvid!r}，无法采集评论")

    collector = fetcher if fetcher is not None else _default_fetcher()
    try:
        raw_records = collector(
            bvid,
            max_pages=crawl_config.max_pages,
            include_replies=crawl_config.include_replies,
            max_sub_pages=crawl_config.max_sub_pages,
            max_items=crawl_config.max_items,
            delay=crawl_config.delay_seconds,
            timeout=crawl_config.timeout,
            page_size=crawl_config.page_size,
            mode=crawl_config.crawl_mode,
            cookie=crawl_config.cookie,
            init_buvid=crawl_config.init_buvid,
            require_login=crawl_config.require_login,
        )
    except CrawlAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成可读原因，不吞异常细节
        raise CrawlAdapterError(
            f"采集视频 {bvid} 的评论失败：{type(exc).__name__}: {exc}"
        ) from exc

    if raw_records is None:
        raise CrawlAdapterError(f"采集视频 {bvid} 的评论失败：爬虫未返回任何结果（None）")
    if isinstance(raw_records, (str, bytes, Mapping)) or not isinstance(
        raw_records, (list, tuple)
    ):
        raise CrawlAdapterError(
            f"采集视频 {bvid} 的评论失败：爬虫返回了非法类型 {type(raw_records).__name__}"
        )

    records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping):
            raise CrawlAdapterError(
                f"采集视频 {bvid} 的评论失败：第 {index} 条记录不是字典"
                f"（{type(raw).__name__}）"
            )
        normalized = _normalize_record(raw, bvid)
        if normalized is not None:
            records.append(normalized)
    return records


def _default_fetcher() -> Callable[..., list[dict[str, Any]]]:
    """延迟取默认采集函数：不在模块导入期引入 requests / bs4。"""
    from ..crawler import fetch_video_comments

    return fetch_video_comments


def _normalize_record(raw: Mapping[str, Any], bvid: str) -> dict[str, Any] | None:
    """把爬虫记录（CSV 读出来是字符串）标准化；正文为空时返回 ``None``。"""
    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    return {
        "bvid": str(raw.get("bvid") or bvid),
        "aid": str(raw.get("aid") or ""),
        "video_title": str(raw.get("video_title") or ""),
        "rpid": str(raw.get("rpid") or ""),
        "level": _to_int(raw.get("level"), default=1),
        "root_rpid": str(raw.get("root_rpid") or ""),
        "parent_rpid": str(raw.get("parent_rpid") or ""),
        "text": text,
        "likes": _to_int(raw.get("likes"), default=0),
        "reply_count": _to_int(raw.get("reply_count"), default=0),
        "ctime_utc": str(raw.get("ctime_utc") or ""),
        "collected_at_utc": str(raw.get("collected_at_utc") or ""),
    }


def _to_int(value: Any, *, default: int = 0) -> int:
    """宽松转 int：字符串数字（CSV）与数字都接受，非法值回落默认值。"""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
