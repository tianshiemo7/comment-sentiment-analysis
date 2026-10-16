"""爬虫主流程骨架（遵纪守法版）。

设计要点（实现时必须遵守）：

1. **先看 robots.txt**：只抓取 `RobotFileParser.can_fetch` 允许的 URL；
2. **限速**：每次请求之间 `time.sleep(config.delay_seconds)`，默认 1.5 秒（见 `.env.example`）；
3. **声明身份**：请求头使用 `CRAWL_USER_AGENT` 中的项目 UA，便于站点识别；
4. **可追溯**：每条记录带 `source` / `url` / `crawled_at`，并由 `sources.write_source_note`
   汇总到 `data/raw/来源说明.md`。

当前为骨架：所有函数只给出签名、数据结构与文档，函数体抛 `NotImplementedError`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# 与 .env.example 保持一致的默认值
DEFAULT_DELAY_SECONDS = float(os.getenv("CRAWL_DELAY_SECONDS", "1.5"))
DEFAULT_USER_AGENT = os.getenv(
    "CRAWL_USER_AGENT",
    "comment-sentiment-analysis-course/0.1 "
    "(+https://github.com/tianshiemo7/comment-sentiment-analysis)",
)
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_ITEMS = 2000

#: 原始数据落盘字段（CSV 列顺序）
RAW_FIELDS = ("text", "label", "source", "url", "crawled_at")


@dataclass
class CrawlConfig:
    """一次采集任务的配置。

    Attributes:
        start_urls: 起始页面 URL 列表（列表页 / 详情页皆可）。
        source: 数据来源名称，写入 `data/raw/来源说明.md` 与每条记录的 `source` 字段。
        delay_seconds: 两次请求之间的间隔秒数，默认取环境变量 `CRAWL_DELAY_SECONDS`。
        user_agent: 请求头 User-Agent。
        timeout: 单次请求超时秒数。
        max_items: 单次任务最多采集条数（超过后停止翻页）。
        out_path: 原始数据输出文件，默认 `data/raw/<source>.csv`。
    """

    start_urls: list[str]
    source: str
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = DEFAULT_TIMEOUT
    max_items: int = DEFAULT_MAX_ITEMS
    out_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.out_path is None:
            self.out_path = Path("data") / "raw" / f"{self.source}.csv"
        if self.delay_seconds < 0:
            raise ValueError("delay_seconds 不能为负数（限速是硬性要求）")


def is_allowed_by_robots(url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
    """检查 ``url`` 是否被目标站点的 robots.txt 允许抓取。

    TODO(实现):
        1. 取 ``urljoin(url, "/robots.txt")``；
        2. 用 ``urllib.robotparser.RobotFileParser`` 读取并 ``can_fetch(user_agent, url)``；
        3. robots.txt 不存在（404）时按「允许」处理，网络异常时按「拒绝」处理并记录日志。
    """
    raise NotImplementedError("TODO: 尚未实现 robots.txt 检查")


def fetch_html(url: str, config: CrawlConfig) -> str:
    """抓取单个页面并返回 HTML 文本。

    TODO(实现):
        - 先调用 :func:`is_allowed_by_robots`，未通过直接返回空串或抛异常；
        - ``requests.get(url, headers={"User-Agent": config.user_agent}, timeout=config.timeout)``
          并 ``raise_for_status()``；
        - 按响应头/`<meta>` 推断编码（默认 utf-8），返回 ``response.text``；
        - 请求前后 ``time.sleep(config.delay_seconds)`` 限速；
        - 对 5xx / 超时做有限次重试（建议 3 次，指数退避）。
    """
    raise NotImplementedError("TODO: 尚未实现页面抓取")


def parse_comments(html: str, selector: str | None = None) -> list[str]:
    """从 HTML 中解析评论正文。

    TODO(实现):
        - 用 ``BeautifulSoup(html, "lxml")`` 解析；
        - 按站点结构定位评论容器（``selector`` 可由 config.extra 传入 CSS 选择器）；
        - 去除脚本、样式、回复按钮等噪声，返回去除首尾空白的纯文本列表；
        - 空文本与纯符号文本过滤掉。
    """
    raise NotImplementedError("TODO: 尚未实现评论解析")


def crawl(config: CrawlConfig) -> list[dict[str, Any]]:
    """执行一次采集任务，返回原始记录列表。

    Returns:
        ``[{"text": str, "label": str | None, "source": str, "url": str, "crawled_at": str}, ...]``
        `crawled_at` 为 ISO8601 字符串；自采数据无标签，`label` 置为 ``None``。

    TODO(实现):
        1. 逐页 :func:`fetch_html` → :func:`parse_comments`；
        2. 用 ``(source, url, text)`` 去重，累计到 ``config.max_items`` 后停止；
        3. 每条记录补 ``crawled_at=datetime.now().isoformat(timespec="seconds")``；
        4. 结束时调用 :func:`save_raw` 与 ``sources.write_source_note``。
    """
    raise NotImplementedError("TODO: 尚未实现采集主流程")


def save_raw(items: Iterable[dict[str, Any]], out_path: str | Path) -> Path:
    """把原始记录写成 CSV（表头见 :data:`RAW_FIELDS`），返回实际写入路径。

    TODO(实现):
        - 用标准库 ``csv`` 写出，编码 ``utf-8-sig``（Excel 直接打开不乱码）；
        - 自动创建父目录；已存在同名文件时追加还是覆盖由调用方决定（建议覆盖）。
    """
    raise NotImplementedError("TODO: 尚未实现原始数据落盘")


def now_iso() -> str:
    """返回当前时间的 ISO8601 字符串（秒级精度），用于 `crawled_at` 字段。"""
    return datetime.now().isoformat(timespec="seconds")
