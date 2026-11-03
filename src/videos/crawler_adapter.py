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

内容受限时单次匿名回退（2026-11 加固）：账号 Cookie 的**登录态有效**并不代表
评论接口可用——B 站可能只对该会话返回空页（``replies=[]`` 且 ``is_end=false``，
继续翻页即 ``CrawlStopped("当前页没有评论，但未收到结束标记（可能被风控）")``）。
此时若 ``config.cookie`` 非空且首次采集异常带风控/空页特征，
:func:`collect_video_comments` 会用**同一内容参数**（页数 / 页大小 / 排序 /
条数上限等）并以 ``cookie=None`` 再采集一次；成功则用 warning 说明「账号 Cookie
会话被限制，已回退匿名采集」（**绝不输出 Cookie 明文**），两次都失败才抛
:class:`CrawlAdapterError` 并给出可执行建议（重新登录 / 关闭「必须登录」改用匿名 /
稍后重试）。无 Cookie、非风控异常的行为与之前完全一致，不会额外发请求。

离线测试：:func:`collect_video_comments` 支持注入 ``fetcher``；测试可同时
monkeypatch ``src.crawler.fetch_video_comments``，全流程不触网。
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .schemas import BVID_RE

logger = logging.getLogger(__name__)

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

#: 风控 / 空页特征：命中即认为「账号 Cookie 会话被内容接口限制」，允许单次匿名回退。
#: 这些片段来自底层爬虫的可读原因，刻意选得足够具体（避免 BV 号等普通数字误判）：
#: ``当前页没有评论，但未收到结束标记（可能被风控）`` / ``接口 412 风控`` /
#: ``接口返回非 JSON 数据（可能被风控/拦截）`` / ``HTTP 412/429`` / 业务码 ``-352`` / ``-509``。
RISK_CONTROL_MARKERS: tuple[str, ...] = (
    "当前页没有评论",
    "未收到结束标记",
    "风控",
    "风险控制",
    "HTTP 412",
    "HTTP 429",
    "-352",
    "-509",
)

#: 两次采集都失败时的可执行建议（不含 Cookie，可直接展示给前端用户）。
COOKIE_FALLBACK_ADVICE = (
    "账号 Cookie 可能已失效或被风控；请到设置页重新登录，"
    "或关闭「必须登录」改用匿名模式后重试；也可以稍后重试"
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
    *,
    on_progress: Callable[[int, int], None] | None = None,
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

    ``require_login=True`` 的语义（**内容受限时仍允许匿名回退**）：
        该开关要求「进入采集前账号会话必须已登录」，由底层爬虫的
        ``login_status()`` 校验；它**不代表账号会话一定能拿到内容**——B 站可能
        对已登录会话单独限流（返回空页 ``replies=[]`` 且 ``is_end=false``，继续
        翻页即被判定风控）。首次采集既然已经通过登录态校验，说明账号凭据本身
        有效，此时失败属于**内容受限**而非配置错误；若直接失败，用户只会看到
        「8 秒即 failed」而拿不到任何评论。因此允许用 ``cookie=None`` 单次匿名
        回退（同时把 ``require_login`` 置 ``False``：匿名会话永远无法通过登录态
        检查，回退的目的是拿内容而不是强制账号模式）。**最多回退一次**，不会
        产生无限重试。

    Raises:
        CrawlAdapterError: BV 号非法、底层采集抛错或返回了非法结构；
            错误信息带可读原因（保留 ``__cause__`` 便于排查）。账号 Cookie 采集
            与匿名回退都失败时，信息里带 :data:`COOKIE_FALLBACK_ADVICE`。
    """
    crawl_config = config or VideoCrawlConfig()
    if not isinstance(bvid, str) or not BVID_RE.match(bvid):
        raise CrawlAdapterError(f"非法的 BV 号：{bvid!r}，无法采集评论")

    collector = fetcher if fetcher is not None else _default_fetcher()
    progress_kwargs: dict[str, Any] = {}
    if on_progress is not None:
        progress_kwargs["on_page"] = lambda result, page, added, total: on_progress(page, total)
    try:
        raw_records = _invoke_collector(collector, bvid, crawl_config, progress_kwargs)
    except CrawlAdapterError as first_error:
        # 只有「带 Cookie 且异常是风控 / 空页特征」才允许单次匿名回退；
        # 无 Cookie、非风控异常一律保持原有失败语义（不额外发请求）。
        if not crawl_config.cookie or not _is_risk_control_failure(first_error):
            raise
        raw_records = _collect_with_anonymous_fallback(
            collector, bvid, crawl_config, progress_kwargs, first_error
        )

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


def _invoke_collector(
    collector: Callable[..., list[dict[str, Any]]],
    bvid: str,
    crawl_config: VideoCrawlConfig,
    progress_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    """调用底层采集函数**一次**，并把任何异常包装成可读的 :class:`CrawlAdapterError`。"""
    try:
        return collector(
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
            **progress_kwargs,
        )
    except CrawlAdapterError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成可读原因，不吞异常细节
        raise CrawlAdapterError(
            f"采集视频 {bvid} 的评论失败：{type(exc).__name__}: {exc}"
        ) from exc


def _collect_with_anonymous_fallback(
    collector: Callable[..., list[dict[str, Any]]],
    bvid: str,
    crawl_config: VideoCrawlConfig,
    progress_kwargs: dict[str, Any],
    first_error: CrawlAdapterError,
) -> list[dict[str, Any]]:
    """账号 Cookie 会话被内容接口限制时的**单次**匿名回退（日志 / 错误不含 Cookie）。

    - 内容参数（页数 / 页大小 / 排序 / 条数上限 / 楼中楼 / 超时 / 限速）原样透传，
      只把 ``cookie`` 置 ``None``；同时把 ``require_login`` 置 ``False``——首次采集
      已确认账号会话登录态有效，匿名会话不可能再通过登录态检查，回退的目的是拿内容。
    - 回退成功即返回记录；回退也失败则抛带 :data:`COOKIE_FALLBACK_ADVICE` 的
      :class:`CrawlAdapterError`（以回退异常为 ``__cause__``，两次原因都保留）。
    """
    cookie = crawl_config.cookie or ""
    first_reason = _redact(_exception_chain_text(first_error), cookie)
    anonymous_config = dataclasses.replace(crawl_config, cookie=None, require_login=False)
    logger.warning(
        "采集视频 %s 时账号 Cookie 会话疑似被限制（%s），已回退匿名采集重试"
        "（本次不使用 Cookie；内容参数不变）",
        bvid,
        first_reason,
    )
    try:
        return _invoke_collector(collector, bvid, anonymous_config, progress_kwargs)
    except CrawlAdapterError as retry_error:
        second_reason = _redact(_exception_chain_text(retry_error), cookie)
        raise CrawlAdapterError(
            f"采集视频 {bvid} 的评论失败：{COOKIE_FALLBACK_ADVICE}。"
            f"账号 Cookie 采集失败原因：{first_reason}；"
            f"匿名回退失败原因：{second_reason}"
        ) from retry_error


def _exception_chain_text(exc: BaseException) -> str:
    """把异常及其 ``__cause__`` / ``__context__`` 链拼成单行文本（用于判定与展示）。"""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " ← ".join(parts)


def _is_risk_control_failure(exc: BaseException) -> bool:
    """异常链里是否带风控 / 空页特征（只有这类失败才允许单次匿名回退）。"""
    text = _exception_chain_text(exc)
    return any(marker in text for marker in RISK_CONTROL_MARKERS)


def _redact(text: str, cookie: str) -> str:
    """日志 / 错误文本兜底：把可能出现的 Cookie 明文替换成脱敏形式。"""
    if not cookie:
        return text
    from .settings import redact_secrets

    return redact_secrets(text, cookie)


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
