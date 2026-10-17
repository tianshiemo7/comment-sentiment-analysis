"""`/api/videos` 流水线的可配置参数（**阶段时长必须可配**）。

默认四段耗时合计 **30 秒**，与前端 mock 的节奏一致（`docs/API契约.md` 第四节）。
测试与联调时用环境变量把时长压到毫秒级即可跑完整个状态机，无需真等 30 秒。

环境变量（全部可选；非法值回退默认值，绝不抛异常）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_CRAWL_SECONDS` | `12` | 「采集评论」阶段耗时（秒） |
| `VIDEO_CLEAN_SECONDS` | `6` | 「文本清洗」阶段耗时（秒） |
| `VIDEO_SENTIMENT_SECONDS` | `8` | 「情感推理」阶段耗时（秒） |
| `VIDEO_STATS_SECONDS` | `4` | 「统计分析」阶段耗时（秒） |
| `VIDEO_STEPS_PER_PHASE` | `4` | 每个阶段拆成几步推进 `progress` |
| `VIDEO_SIMULATE_FAILURE` | `0` | 置 `1` 时流水线在「情感推理」阶段失败（用于验证 `failed` + retry） |
| `VIDEOS_DIR` | `outputs/videos` | 任务落盘根目录（见 `store.py`） |

真实采集（**opt-in，默认关闭**）相关的环境变量集中在
`src/videos/crawler_adapter.py`，这里只把解析结果接进 :class:`PipelineConfig`：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_REAL_CRAWL` | `0` | 置 `1` 时「采集评论」阶段改调真实爬虫（`src.crawler.fetch_video_comments`） |
| `VIDEO_CRAWL_MAX_PAGES` | `1` | 最多采集多少页一级评论（合法范围 1~50） |
| `VIDEO_CRAWL_MAX_ITEMS` | `200` | 本次最多新增评论数（`0` 表示不限制） |
| `VIDEO_CRAWL_DELAY` | `2.5` | 请求最小间隔（秒，不得为负） |
| `VIDEO_CRAWL_INCLUDE_REPLIES` | `0` | 是否采集楼中楼 |
| `VIDEO_CRAWL_TIMEOUT` | `15` | 单次请求超时（秒，须为正数） |
| `VIDEO_CRAWL_PAGE_SIZE` | `20` | 一级评论每页条数（1~50） |
| `VIDEO_CRAWL_SORT_MODE` | `time` | 一级评论排序：`time` / `hot` |
| `VIDEO_CRAWL_INIT_BUVID` | `0` | 是否初始化 buvid3/buvid4 设备指纹 |
| `VIDEO_CRAWL_REQUIRE_LOGIN` | `0` | 未登录时直接失败 |

注意：带 ``settings`` 提交的任务（分步流水线）使用**任务级设置快照**，优先级高于
上述环境变量；环境变量只影响不带 ``settings`` 的旧路径与 ``PipelineConfig.from_env``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import crawler_adapter

ENV_CRAWL_SECONDS = "VIDEO_CRAWL_SECONDS"
ENV_CLEAN_SECONDS = "VIDEO_CLEAN_SECONDS"
ENV_SENTIMENT_SECONDS = "VIDEO_SENTIMENT_SECONDS"
ENV_STATS_SECONDS = "VIDEO_STATS_SECONDS"
ENV_STEPS_PER_PHASE = "VIDEO_STEPS_PER_PHASE"
ENV_SIMULATE_FAILURE = "VIDEO_SIMULATE_FAILURE"

#: 默认阶段时长（秒）：合计 30 秒，与前端 mock 一致
DEFAULT_CRAWL_SECONDS = 12.0
DEFAULT_CLEAN_SECONDS = 6.0
DEFAULT_SENTIMENT_SECONDS = 8.0
DEFAULT_STATS_SECONDS = 4.0
DEFAULT_STEPS_PER_PHASE = 4

#: 真实采集默认参数（单一真相来源在 `crawler_adapter.py`，这里只做转发）
DEFAULT_CRAWL_MAX_PAGES = crawler_adapter.DEFAULT_MAX_PAGES
DEFAULT_CRAWL_MAX_SUB_PAGES = crawler_adapter.DEFAULT_MAX_SUB_PAGES
DEFAULT_CRAWL_MAX_ITEMS = crawler_adapter.DEFAULT_MAX_ITEMS
DEFAULT_CRAWL_DELAY_SECONDS = crawler_adapter.DEFAULT_DELAY_SECONDS
DEFAULT_CRAWL_INCLUDE_REPLIES = crawler_adapter.DEFAULT_INCLUDE_REPLIES
DEFAULT_CRAWL_TIMEOUT = crawler_adapter.DEFAULT_TIMEOUT
DEFAULT_CRAWL_PAGE_SIZE = crawler_adapter.DEFAULT_PAGE_SIZE
DEFAULT_CRAWL_SORT_MODE = crawler_adapter.DEFAULT_SORT_MODE
DEFAULT_CRAWL_INIT_BUVID = crawler_adapter.DEFAULT_INIT_BUVID
DEFAULT_CRAWL_REQUIRE_LOGIN = crawler_adapter.DEFAULT_REQUIRE_LOGIN


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量；缺失或非法时回退默认值（绝不因配置写错而启动失败）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；缺失、非法或 < 1 时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量：``1/true/yes/on`` 为真，其余为假。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PipelineConfig:
    """流水线时序配置（不可变；测试直接用构造参数覆盖，无需改环境变量）。

    Attributes:
        crawl_seconds: 「采集评论」阶段耗时。
        clean_seconds: 「文本清洗」阶段耗时。
        sentiment_seconds: 「情感推理」阶段耗时。
        stats_seconds: 「统计分析」阶段耗时。
        steps_per_phase: 每个阶段推进 `progress` 的步数（≥1，步数越大进度越平滑）。
        simulate_failure: 为 ``True`` 时在「情感推理」阶段抛错，用于验证 `failed` / retry。
        real_crawl: 为 ``True`` 时「采集评论」阶段调用真实爬虫（opt-in，默认关闭；
            默认 ``False`` 保证现有占位行为与测试完全不变）。
        crawl_max_pages: 真实采集最多翻多少页一级评论（0~50；0 表示不限制）。
        crawl_max_items: 真实采集本次最多新增多少条评论（``0`` 表示不限制）。
        crawl_delay_seconds: 真实采集的请求最小间隔（秒，不得为负）。
        crawl_include_replies: 真实采集是否包含楼中楼。
        crawl_timeout: 真实采集的单次请求超时（秒，须为正数）。
        crawl_max_sub_pages: 每条根评论最多采多少页楼中楼（0~20；0 表示不限制）。
        crawl_page_size: 一级评论每页请求条数（1~50）。
        crawl_sort_mode: 一级评论排序（``time`` / ``hot``）。
        crawl_init_buvid: 是否初始化 buvid3/buvid4 设备指纹。
        crawl_require_login: 是否要求登录态（未登录直接失败）。
    """

    crawl_seconds: float = DEFAULT_CRAWL_SECONDS
    clean_seconds: float = DEFAULT_CLEAN_SECONDS
    sentiment_seconds: float = DEFAULT_SENTIMENT_SECONDS
    stats_seconds: float = DEFAULT_STATS_SECONDS
    steps_per_phase: int = DEFAULT_STEPS_PER_PHASE
    simulate_failure: bool = False
    # ---- 真实采集（opt-in；默认关闭，字段追加在末尾以保持位置参数兼容）----
    real_crawl: bool = False
    crawl_max_pages: int = DEFAULT_CRAWL_MAX_PAGES
    crawl_max_items: int = DEFAULT_CRAWL_MAX_ITEMS
    crawl_delay_seconds: float = DEFAULT_CRAWL_DELAY_SECONDS
    crawl_include_replies: bool = DEFAULT_CRAWL_INCLUDE_REPLIES
    crawl_timeout: float = DEFAULT_CRAWL_TIMEOUT
    crawl_max_sub_pages: int = DEFAULT_CRAWL_MAX_SUB_PAGES
    crawl_page_size: int = DEFAULT_CRAWL_PAGE_SIZE
    crawl_sort_mode: str = DEFAULT_CRAWL_SORT_MODE
    crawl_init_buvid: bool = DEFAULT_CRAWL_INIT_BUVID
    crawl_require_login: bool = DEFAULT_CRAWL_REQUIRE_LOGIN

    def __post_init__(self) -> None:
        for name in ("crawl_seconds", "clean_seconds", "sentiment_seconds", "stats_seconds"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} 不能为负数（阶段时长必须可配且非负）")
        if self.steps_per_phase < 1:
            raise ValueError("steps_per_phase 必须 ≥ 1")
        if not 0 <= int(self.crawl_max_pages) <= crawler_adapter.MAX_PAGES_LIMIT:
            raise ValueError(
                f"crawl_max_pages 必须在 0~{crawler_adapter.MAX_PAGES_LIMIT} 之间（0=不限）"
            )
        if not 0 <= int(self.crawl_max_sub_pages) <= crawler_adapter.MAX_SUB_PAGES_LIMIT:
            raise ValueError(
                f"crawl_max_sub_pages 必须在 0~{crawler_adapter.MAX_SUB_PAGES_LIMIT} 之间（0=不限）"
            )
        if not 1 <= int(self.crawl_page_size) <= crawler_adapter.MAX_PAGE_SIZE_LIMIT:
            raise ValueError(
                f"crawl_page_size 必须在 1~{crawler_adapter.MAX_PAGE_SIZE_LIMIT} 之间"
            )
        if self.crawl_sort_mode not in crawler_adapter.SORT_MODE_TO_CRAWL_MODE:
            raise ValueError("crawl_sort_mode 只能是 time / hot")
        if int(self.crawl_max_items) < 0:
            raise ValueError("crawl_max_items 不能为负数（0 表示不限制）")
        if float(self.crawl_delay_seconds) < 0:
            raise ValueError("crawl_delay_seconds 不能为负数（限速是硬性要求）")
        if float(self.crawl_timeout) <= 0:
            raise ValueError("crawl_timeout 必须为正数")

    @property
    def total_seconds(self) -> float:
        """四段耗时合计（默认 30 秒）。"""
        return (
            self.crawl_seconds
            + self.clean_seconds
            + self.sentiment_seconds
            + self.stats_seconds
        )

    def crawl_config(self) -> crawler_adapter.VideoCrawlConfig:
        """把本配置里的真实采集参数转成适配层配置（便于测试直接断言）。"""
        return crawler_adapter.VideoCrawlConfig(
            max_pages=self.crawl_max_pages,
            include_replies=self.crawl_include_replies,
            max_sub_pages=self.crawl_max_sub_pages,
            max_items=self.crawl_max_items,
            delay_seconds=self.crawl_delay_seconds,
            timeout=self.crawl_timeout,
            real_crawl=self.real_crawl,
            page_size=self.crawl_page_size,
            sort_mode=self.crawl_sort_mode,
            init_buvid=self.crawl_init_buvid,
            require_login=self.crawl_require_login,
        )

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """按环境变量构造配置（未设置的项用默认值；非法值回退默认，不抛异常）。"""
        crawl = crawler_adapter.VideoCrawlConfig.from_env()
        return cls(
            crawl_seconds=_env_float(ENV_CRAWL_SECONDS, DEFAULT_CRAWL_SECONDS),
            clean_seconds=_env_float(ENV_CLEAN_SECONDS, DEFAULT_CLEAN_SECONDS),
            sentiment_seconds=_env_float(ENV_SENTIMENT_SECONDS, DEFAULT_SENTIMENT_SECONDS),
            stats_seconds=_env_float(ENV_STATS_SECONDS, DEFAULT_STATS_SECONDS),
            steps_per_phase=_env_int(ENV_STEPS_PER_PHASE, DEFAULT_STEPS_PER_PHASE),
            simulate_failure=_env_bool(ENV_SIMULATE_FAILURE, False),
            real_crawl=crawl.real_crawl,
            crawl_max_pages=crawl.max_pages,
            crawl_max_items=crawl.max_items,
            crawl_delay_seconds=crawl.delay_seconds,
            crawl_include_replies=crawl.include_replies,
            crawl_timeout=crawl.timeout,
            crawl_max_sub_pages=crawl.max_sub_pages,
            crawl_page_size=crawl.page_size,
            crawl_sort_mode=crawl.sort_mode,
            crawl_init_buvid=crawl.init_buvid,
            crawl_require_login=crawl.require_login,
        )
