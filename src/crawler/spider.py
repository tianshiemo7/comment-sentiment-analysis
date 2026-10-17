"""通用 HTML 评论采集（robots → 取页 → 解析 → 去重 → 落盘 → 来源登记）。

本模块是爬虫包的**通用骨架**，与具体站点无关；B 站接口式采集见
:mod:`src.crawler.bilibili`。两者共享同一套限速、重试、robots 校验、文本标准化与
来源登记实现，输出也遵循相同的下游字段约定。

设计要点（实现必须遵守）：

1. **先看 robots.txt**：只抓取 :class:`~src.crawler.robots.RobotsChecker` 判定允许的
   URL；网络异常默认按「拒绝」处理（fail-closed）；
2. **限速**：每次请求（含重试）前经过 :class:`~src.crawler.rate_limit.RateLimiter`，
   默认间隔 1.5 秒 + 30% 抖动，可通过环境变量 ``CRAWL_DELAY_SECONDS`` 调整；
3. **声明身份**：请求头使用 ``CRAWL_USER_AGENT`` 中的项目 UA，便于站点识别与联系；
4. **可追溯**：每条记录带 ``source`` / ``url`` / ``crawled_at``，并由
   :func:`src.crawler.sources.register_source` 汇总到 ``data/raw/来源说明.md``。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from . import sources
from .errors import ConfigError, CrawlStopped, RobotsDenied
from .normalize import is_meaningful_text, normalize_text, utc_iso
from .rate_limit import RateLimiter
from .robots import RobotsChecker
from .sources import SourceRecord
from .transport import (
    DEFAULT_USER_AGENT,
    RequestMetrics,
    RetryPolicy,
    build_session,
    request_with_retry,
)

logger = logging.getLogger(__name__)

# 与 .env.example 保持一致的默认值
DEFAULT_DELAY_SECONDS = float(os.getenv("CRAWL_DELAY_SECONDS", "1.5"))
DEFAULT_TIMEOUT = float(os.getenv("CRAWL_TIMEOUT", "10"))
DEFAULT_MAX_ITEMS = 2000

#: 原始数据落盘字段（CSV 列顺序）
RAW_FIELDS = ("text", "label", "source", "url", "crawled_at")

#: 通用评论节点选择器（找不到时由调用方通过 ``config.extra["selector"]`` 指定）
COMMENT_SELECTORS: tuple[str, ...] = (
    ".comment-content",
    ".comment-item .content",
    ".comment-item",
    ".reply-content",
    ".reply-item .content",
    ".reply-item",
    "[data-comment-text]",
    ".comment",
    ".comments .item",
)

#: 解析前需要删除的噪声节点
NOISE_SELECTORS = "script, style, noscript, template, iframe, button, .reply, .like, .actions, .btn"


@dataclass
class CrawlConfig:
    """一次通用 HTML 采集任务的配置。

    Attributes:
        start_urls: 起始页面 URL 列表（列表页 / 详情页皆可）。
        source: 数据来源名称，写入来源说明与每条记录的 ``source`` 字段。
        delay_seconds: 两次请求之间的最小间隔秒数（含抖动前）。
        user_agent: 请求头 User-Agent。
        timeout: 单次请求超时秒数。
        max_items: 单次任务最多采集条数。
        out_path: 原始数据输出文件，默认 ``data/raw/<source>.csv``。
        extra: 站点相关配置，例如 ``{"selector": ".comment-content"}``。
        respect_robots: 是否校验 robots.txt（合规默认开启，测试可关闭）。
        write_source_note: 是否写入来源说明（测试可关闭）。
    """

    start_urls: list[str]
    source: str
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = DEFAULT_TIMEOUT
    max_items: int = DEFAULT_MAX_ITEMS
    out_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    respect_robots: bool = True
    write_source_note: bool = True
    max_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 20.0
    jitter_ratio: float = 0.3
    encoding: str | None = None

    def __post_init__(self) -> None:
        if self.out_path is None:
            safe_source = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", self.source).strip("_") or "crawl"
            self.out_path = Path("data") / "raw" / f"{safe_source}.csv"
        else:
            self.out_path = Path(self.out_path)
        if self.delay_seconds < 0:
            raise ConfigError("delay_seconds 不能为负数（限速是硬性要求）")
        if self.timeout <= 0:
            raise ConfigError("timeout 必须为正数")
        if self.max_items < 0:
            raise ConfigError("max_items 不能为负数")
        if not self.start_urls:
            raise ConfigError("start_urls 不能为空")
        for url in self.start_urls:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ConfigError(f"非法起始 URL：{url}")
        if self.max_attempts < 1:
            raise ConfigError("max_attempts 至少为 1")


@dataclass
class CrawlReport:
    """一次采集任务的完整结果（含效率指标）。"""

    source: str
    urls: list[str]
    items: list[dict[str, Any]]
    saved_path: Path | None
    source_note_path: Path | None
    started_at: str
    finished_at: str
    elapsed_seconds: float
    metrics: dict[str, Any]
    robots_denied: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "urls": list(self.urls),
            "count": len(self.items),
            "saved_path": str(self.saved_path) if self.saved_path else None,
            "source_note_path": str(self.source_note_path) if self.source_note_path else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "items_per_second": round(len(self.items) / self.elapsed_seconds, 3)
            if self.elapsed_seconds > 0
            else 0.0,
            "robots_denied": list(self.robots_denied),
            "errors": list(self.errors),
            "metrics": self.metrics,
        }


class HtmlCrawler:
    """可复用的 HTML 采集器：持有会话、限速器、robots 缓存与指标。"""

    def __init__(
        self,
        config: CrawlConfig,
        *,
        session: Any = None,
        limiter: RateLimiter | None = None,
        robots: RobotsChecker | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self.config = config
        self._session = session or build_session(config.user_agent)
        self._limiter = limiter or RateLimiter(
            config.delay_seconds,
            jitter=config.jitter_ratio,
            clock=clock,
            sleep=sleep,
        )
        self._robots = robots or RobotsChecker(
            config.user_agent,
            session=self._session,
        )
        self._transport_sleep = sleep
        self._clock = clock
        self._policy = RetryPolicy(
            max_attempts=config.max_attempts,
            base_delay=config.retry_base_delay,
            max_delay=config.retry_max_delay,
            jitter=config.jitter_ratio,
        )
        self.metrics = RequestMetrics()
        self.robots_decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ public
    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()

    def fetch_html(self, url: str) -> str:
        """校验 robots 后抓取单个页面并返回解码后的 HTML。"""

        decision = self._robots.check(url)
        self.robots_decisions.append(decision.as_dict())
        if not decision.allowed and self.config.respect_robots:
            raise RobotsDenied(url, decision.reason or "robots.txt 不允许")

        response = request_with_retry(
            self._session,
            "GET",
            url,
            limiter=self._limiter,
            policy=self._policy,
            metrics=self.metrics,
            timeout=self.config.timeout,
            headers={"User-Agent": self.config.user_agent},
            sleep=self._transport_sleep,
            clock=self._clock,
        )
        return decode_html(response, override=self.config.encoding)

    def parse_comments(self, html: str, selector: str | None = None) -> list[str]:
        """从 HTML 解析评论正文（纯文本、标准化、过滤无意义文本）。"""

        return parse_comments(
            html,
            selector=selector or self.config.extra.get("selector"),
        )

    def crawl(self) -> CrawlReport:
        """执行采集，返回带指标的报告（同时按配置落盘）。"""

        started_at = utc_iso()
        started = self._clock()
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        denied: list[dict[str, Any]] = []
        errors: list[str] = []
        selector = self.config.extra.get("selector")

        for url in self.config.start_urls:
            if len(items) >= self.config.max_items:
                break
            try:
                html = self.fetch_html(url)
            except RobotsDenied as exc:
                denied.append({"url": url, "reason": str(exc)})
                logger.warning("跳过被 robots 禁止的页面：%s", url)
                continue
            except CrawlStopped as exc:
                errors.append(str(exc))
                logger.warning("抓取失败，跳过 %s：%s", url, exc)
                continue

            for text in self.parse_comments(html, selector):
                # 通用 HTML 页面没有稳定评论 ID，按「来源 + 标准化文本」在任务内去重，
                # 避免同一热评在列表页 / 详情页重复进入训练集。
                key = (self.config.source, text)
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "text": text,
                        "label": None,
                        "source": self.config.source,
                        "url": url,
                        "crawled_at": utc_iso(),
                    }
                )
                if len(items) >= self.config.max_items:
                    break

        saved_path = save_raw(items, self.config.out_path) if items else None
        source_note_path = None
        if self.config.write_source_note:
            finished = utc_iso()
            note = (
                f"HTML 页面采集；robots 校验={'开' if self.config.respect_robots else '关'}；"
                f"限速={self.config.delay_seconds:g}s+抖动；请求 {self.metrics.logical_requests} 次；"
                f"重试 {self.metrics.retries} 次；耗时 {self._clock() - started:.2f}s"
            )
            source_note_path = sources.register_source(
                SourceRecord(
                    name=self.config.source,
                    url=self.config.start_urls[0],
                    collected_at=finished,
                    count=len(items),
                    labeled=False,
                    note=note,
                )
            )

        elapsed = self._clock() - started
        return CrawlReport(
            source=self.config.source,
            urls=list(self.config.start_urls),
            items=items,
            saved_path=saved_path,
            source_note_path=source_note_path,
            started_at=started_at,
            finished_at=utc_iso(),
            elapsed_seconds=elapsed,
            metrics=self.metrics.summary(),
            robots_denied=denied,
            errors=errors,
        )


def decode_html(response: Any, *, override: str | None = None) -> str:
    """按「显式指定 → Content-Type → 响应编码 → meta charset → 常见中文编码」解码。"""

    content = getattr(response, "content", b"") or b""
    if override:
        return content.decode(override, errors="replace")

    headers = getattr(response, "headers", None) or {}
    content_type = ""
    try:
        content_type = headers.get("Content-Type", "") or ""
    except AttributeError:  # pragma: no cover
        pass
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    candidates: list[str] = []
    if match:
        candidates.append(match.group(1))
    encoding = getattr(response, "encoding", None)
    if encoding and encoding.lower() not in ("iso-8859-1", "ascii"):
        candidates.append(encoding)
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", content[:4096], re.I)
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    candidates.extend(["utf-8", "gb18030"])

    for candidate in candidates:
        try:
            return content.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


def parse_comments(html: str, selector: str | None = None) -> list[str]:
    """从 HTML 中解析评论正文。

    Args:
        html: 页面 HTML。
        selector: CSS 选择器；省略时依次尝试 :data:`COMMENT_SELECTORS`。
            找不到任何节点时返回空列表（调用方可据此判断选择器是否失效）。
    """

    if not html or not html.strip():
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - requirements 已包含
        raise CrawlStopped("解析 HTML 需要 beautifulsoup4：python -m pip install beautifulsoup4") from exc

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml 缺失时退回标准库解析器
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "template", "iframe", "button"]):
        tag.decompose()

    nodes: list[Any] = []
    selectors = [selector] if selector else list(COMMENT_SELECTORS)
    for css in selectors:
        try:
            nodes = soup.select(css)
        except Exception:  # 非法选择器：交给调用方
            continue
        if nodes:
            break

    texts: list[str] = []
    for node in nodes:
        for junk in node.select(NOISE_SELECTORS):
            junk.decompose()
        text = normalize_text(node.get_text(" ", strip=True))
        if is_meaningful_text(text):
            texts.append(text)
    return texts


def save_raw(items: Iterable[dict[str, Any]], out_path: str | Path) -> Path:
    """把原始记录写成 CSV（UTF-8 BOM），返回实际写入路径。"""

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {field: item.get(field) for field in RAW_FIELDS}
        for item in items
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return target


def is_allowed_by_robots(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    *,
    session: Any = None,
    timeout: float = 5.0,
    fail_closed: bool = True,
) -> bool:
    """检查 ``url`` 是否被目标站点 robots.txt 允许抓取。"""

    checker = RobotsChecker(
        user_agent,
        session=session,
        timeout=timeout,
        fail_closed=fail_closed,
    )
    return checker.is_allowed(url)


def fetch_html(url: str, config: CrawlConfig) -> str:
    """单页抓取便捷函数（新建临时采集器；连续抓取请复用 :class:`HtmlCrawler`）。"""

    crawler = HtmlCrawler(config)
    try:
        return crawler.fetch_html(url)
    finally:
        crawler.close()


def crawl_with_report(config: CrawlConfig) -> CrawlReport:
    """执行采集并返回完整报告（含效率指标、robots 判定与被跳过的页面）。"""

    crawler = HtmlCrawler(config)
    try:
        return crawler.crawl()
    finally:
        crawler.close()


def crawl(config: CrawlConfig) -> list[dict[str, Any]]:
    """执行一次采集任务，返回原始记录列表（兼容旧接口）。"""

    return crawl_with_report(config).items


def now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（秒级精度），用于 ``crawled_at`` 字段。"""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    """通用 HTML 爬虫命令行入口：``python -m src.crawler.spider``。"""

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(description="通用 HTML 评论采集（遵守 robots.txt + 限速）")
    parser.add_argument("--url", nargs="+", required=True, help="一个或多个页面 URL")
    parser.add_argument("--source", required=True, help="来源名称（写入来源说明与 CSV）")
    parser.add_argument("--selector", default=None, help="评论节点的 CSS 选择器")
    parser.add_argument("--max-items", type=int, default=200, help="最多采集条数")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="请求最小间隔（秒）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次请求超时（秒）")
    parser.add_argument("--out", default=None, help="输出 CSV 路径（默认 data/raw/<source>.csv）")
    parser.add_argument("--no-robots", action="store_true", help="不校验 robots.txt（仅本地测试用）")
    parser.add_argument("--json", action="store_true", help="向 stdout 输出 JSON 报告")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    config = CrawlConfig(
        start_urls=args.url,
        source=args.source,
        delay_seconds=args.delay,
        timeout=args.timeout,
        max_items=args.max_items,
        out_path=Path(args.out) if args.out else None,
        extra={"selector": args.selector} if args.selector else {},
        respect_robots=not args.no_robots,
    )
    report = crawl_with_report(config)
    payload = report.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"来源：{report.source}")
        print(f"页面：{len(report.urls)} 个；采集 {len(report.items)} 条；"
              f"请求 {report.metrics['requests']} 次；耗时 {report.elapsed_seconds:.2f}s")
        if report.saved_path:
            print(f"CSV：{report.saved_path}")
        if report.source_note_path:
            print(f"来源说明：{report.source_note_path}")
        if report.robots_denied:
            print(f"被 robots 跳过 {len(report.robots_denied)} 个页面")
        for error in report.errors:
            print(f"错误：{error}")
    return 0 if report.items else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
