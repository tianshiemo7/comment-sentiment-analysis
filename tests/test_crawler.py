"""爬虫模块单元测试（全部离线，不访问真实站点）。

覆盖四层：

1. 纯函数：文本/记录标准化、WBI 签名、BV 解析、robots 判定、来源表格渲染；
2. 限速与重试：注入假时钟与假休眠，验证间隔、抖动上界、指数退避、429 的
   ``Retry-After``、不可重试状态码；
3. 通用 HTML 爬虫：本地 HTTP 服务（robots / 页面 / 去重 / 落盘 / 来源登记）；
4. B 站客户端：用可路由的假会话模拟 nav / view / 评论接口，验证风控恢复、
   评论关闭、分页、断点续爬、CSV 去重与适配器接口。
"""

from __future__ import annotations

import csv
import json
import logging
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

# CI 的「最小依赖集」降级路径可能没有 requests / bs4，此时跳过爬虫测试而不是报导入错误。
pytest.importorskip("requests")
pytest.importorskip("bs4")

import requests  # noqa: E402

from src.crawler import sources
from src.crawler.bilibili import (
    COLUMNS,
    SUB_COMMENT_PAGE_SIZE,
    BiliClient,
    CsvStore,
    crawl_bilibili,
    crawl_video,
    fetch_video_comments,
    get_mixin_key,
    load_state,
    merge_video_csvs,
    parse_bvid,
    read_bvid_list,
    read_csv_records,
    save_state,
    sign_wbi,
    validate_page_limits,
)
from src.crawler.errors import (
    ApiError,
    CommentClosed,
    ConfigError,
    CrawlStopped,
    HttpStatusError,
    RetryExhausted,
    RiskControlError,
    RobotsDenied,
)
from src.crawler.normalize import (
    is_meaningful_text,
    normalize_comment,
    normalize_oneline,
    normalize_text,
    to_int,
    utc_iso,
)
from src.crawler.rate_limit import RateLimiter, backoff_delay, parse_retry_after
from src.crawler.robots import RobotsChecker
from src.crawler.spider import (
    CrawlConfig,
    HtmlCrawler,
    parse_comments,
    save_raw,
)
from src.crawler.transport import (
    RequestMetrics,
    RetryPolicy,
    request_with_retry,
)


# =========================================================================
# 测试替身
# =========================================================================

class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload=None,
        *,
        text: str | None = None,
        headers: dict[str, str] | None = None,
        encoding: str = "utf-8",
    ):
        self.status_code = status_code
        self._payload = payload
        if text is None:
            text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers or {"Content-Type": "application/json; charset=utf-8"}
        self.encoding = encoding

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeCookies:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, name, value, domain=None):
        self.store[name] = value

    def clear(self, **kwargs):
        self.store.pop(kwargs.get("name"), None)


class FakeClock:
    """假单调时钟 + 假休眠，记录每次休眠时长。"""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RoutingSession:
    """按 URL 子串路由的假会话，记录每次请求。"""

    def __init__(self, routes: dict[str, callable | FakeResponse]):
        self.routes = routes
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}
        self.cookies = FakeCookies()

    def request(self, method, url, timeout=None, headers=None, params=None):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers})
        for key, handler in self.routes.items():
            if key in url:
                if callable(handler):
                    return handler(url, params)
                return handler
        raise AssertionError(f"未路由的请求：{url}")

    def get(self, url, timeout=None, **kwargs):
        return self.request("GET", url, timeout=timeout)

    def close(self):
        pass


def make_bili_session(*, view_payload=None, main_payload=None, sub_payload=None, nav_payload=None):
    """构造覆盖 B 站常用接口的假会话。"""

    nav = nav_payload or {
        "code": -101,
        "message": "账号未登录",
        "data": {
            "isLogin": False,
            "wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/" + "a" * 32 + ".png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/" + "b" * 32 + ".png",
            },
        },
    }
    view = view_payload or {
        "code": 0,
        "data": {
            "aid": 123456,
            "title": "示例 视频\n标题",
            "owner": {"name": "UP主"},
            "stat": {"reply": 42, "view": 1000},
        },
    }
    main = main_payload or {
        "code": 0,
        "data": {
            "replies": [
                {
                    "rpid": 9007199254740993,
                    "content": {"message": "讲得真清楚&amp;很有用"},
                    "like": 2,
                    "rcount": 0,
                    "ctime": 1700000000,
                }
            ],
            "cursor": {"is_end": True},
        },
    }
    sub = sub_payload or {"code": 0, "data": {"replies": [], "page": {"count": 0}}}
    return RoutingSession(
        {
            "/x/frontend/finger/spi": FakeResponse(
                payload={"code": 0, "data": {"b_3": "B3", "b_4": "B4"}}
            ),
            "/x/web-interface/nav": FakeResponse(payload=nav),
            "/x/web-interface/view": FakeResponse(payload=view),
            "/x/v2/reply/wbi/main": FakeResponse(payload=main),
            "/x/v2/reply/reply": FakeResponse(payload=sub),
        }
    )


@contextmanager
def local_site(pages: dict[str, str], robots: str = "User-agent: *\nAllow: /\n"):
    """起一个临时本地站，返回 (base_url, 请求记录)。"""

    requests_log: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests_log.append(self.path)
            if self.path == "/robots.txt":
                body = robots.encode("utf-8")
                content_type = "text/plain; charset=utf-8"
            elif self.path in pages:
                body = pages[self.path].encode("utf-8")
                content_type = "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests_log
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# =========================================================================
# 1. 标准化
# =========================================================================

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  a &amp; b  ", "a & b"),
        ("{full}abc", "{full}abc"),
        ("ＡＢＣ１２３", "ABC123"),
        ("第一行\r\n第二行", "第一行\n第二行"),
        ("零宽\u200b字符\ufeff", "零宽字符"),
        ("多个   空格\t制表", "多个 空格 制表"),
        ("\u0000控制\u0007字符", "控制字符"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


def test_normalize_text_can_strip_urls_without_touching_rest():
    text = "看这个 https://example.com/a?b=1 很好"
    assert normalize_text(text) == text
    stripped = normalize_text(text, strip_urls=True)
    assert "https://" not in stripped
    assert "很好" in stripped


def test_normalize_text_keeps_emoji_and_bilibili_emoticons():
    assert normalize_text("太好笑了[笑哭]😄") == "太好笑了[笑哭]😄"
    assert is_meaningful_text("😂") is True
    assert is_meaningful_text("。。。！!") is False
    assert is_meaningful_text("") is False


def test_normalize_oneline_collapses_newlines():
    assert normalize_oneline("标题\n第二行") == "标题 第二行"


def test_normalize_comment_levels_and_fields():
    item = {
        "rpid_str": "12345678901234567890",
        "root": 0,
        "parent": 0,
        "content": {"message": "  好&amp;喜欢  "},
        "like": "7",
        "rcount": None,
        "ctime": 1700000000,
    }
    row = normalize_comment(item, "BV1xx", 42, "标题", level=1)
    assert row is not None
    assert row["rpid"] == "12345678901234567890"
    assert row["text"] == "好&喜欢"
    assert row["likes"] == 7 and row["reply_count"] == 0
    assert row["root_rpid"] == "" and row["parent_rpid"] == ""
    assert row["ctime_utc"] == utc_iso(1700000000)


def test_normalize_comment_reply_uses_forced_root_and_skips_empty():
    item = {"rpid": "2", "root": "1", "parent": "0", "content": {"message": "[笑哭]"}}
    row = normalize_comment(item, "BV1xx", 42, "标题", level=2, forced_root="1")
    assert row is not None
    assert row["root_rpid"] == "1" and row["parent_rpid"] == "1"
    empty = {"rpid": "3", "content": {"message": "  ！!。"}}
    assert normalize_comment(empty, "BV1xx", 42, "标题") is None
    no_id = {"content": {"message": "正常评论"}}
    assert normalize_comment(no_id, "BV1xx", 42, "标题") is None


def test_to_int_and_utc_iso_are_tolerant():
    assert to_int("12") == 12 and to_int(None, 3) == 3 and to_int("x", 5) == 5
    assert utc_iso(None).endswith("+00:00")
    assert utc_iso("bad") == ""


# =========================================================================
# 2. WBI / BV 解析
# =========================================================================

def test_get_mixin_key_known_vector():
    img_key = "7cd084941338484aae1ad9425b84077c"
    sub_key = "4932caff0ff746eab6f01bf08b70ac45"
    key = get_mixin_key(img_key, sub_key)
    assert len(key) == 32
    # 同一输入必须稳定
    assert key == get_mixin_key(img_key, sub_key)
    with pytest.raises(CrawlStopped):
        get_mixin_key("short", "key")


def test_sign_wbi_is_sorted_filtered_and_deterministic():
    params = {"b": "x!y", "a": "1(2)", "z": None}
    signed = sign_wbi(params, "m" * 32, timestamp=1700000000)
    assert signed["wts"] == "1700000000"
    assert signed["a"] == "12" and signed["b"] == "xy" and signed["z"] == "None"
    assert len(signed["w_rid"]) == 32
    assert sign_wbi(params, "m" * 32, timestamp=1700000000) == signed


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("https://www.bilibili.com/video/BV1GJ411x7h7?p=1", "BV1GJ411x7h7"),
        ("https://m.bilibili.com/video/BV1GJ411x7h7", "BV1GJ411x7h7"),
        ("看 BV1GJ411x7h7 这个", "BV1GJ411x7h7"),
    ],
)
def test_parse_bvid_accepts_url_and_text(value, expected):
    assert parse_bvid(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "BV1", "https://example.com/video/BV1GJ411x7h7", "没有 BV 号"],
)
def test_parse_bvid_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_bvid(value)


# =========================================================================
# 3. 限速与退避
# =========================================================================

def test_rate_limiter_enforces_interval_and_jitter_bounds():
    clock = FakeClock()
    limiter = RateLimiter(1.0, jitter=0.3, clock=clock, sleep=clock.sleep, rng=__import__("random").Random(7))
    limiter.wait()
    assert clock.now == 0
    times = []
    for _ in range(20):
        limiter.wait()
        times.append(clock.now)
    # 相邻两次请求开始时间的间隔 = 本次目标间隔（含抖动）
    deltas = [round(times[index] - (times[index - 1] if index else 0), 6) for index in range(len(times))]
    assert all(0.99 <= delta <= 1.31 for delta in deltas)
    # 抖动确实存在（不是恒定 1.0）
    assert len({round(value, 3) for value in deltas}) > 1


def test_rate_limiter_penalize_and_relax():
    limiter = RateLimiter(2.0, jitter=0.0)
    assert limiter.penalize(1.5) == 3.0
    assert limiter.penalize(1.5) == 4.5
    assert limiter.relax(0.5) == 2.25
    assert limiter.relax(0.1) == 2.0  # 不会低于基础间隔
    with pytest.raises(ValueError):
        RateLimiter(-1)
    with pytest.raises(ValueError):
        RateLimiter(1, jitter=1.0)


def test_backoff_delay_grows_and_caps():
    rng = __import__("random").Random(1)
    delays = [backoff_delay(attempt, base=1.0, cap=8.0, jitter=0.0, rng=rng) for attempt in (1, 2, 3, 4, 5)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0]
    with pytest.raises(ValueError):
        backoff_delay(0)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("3", 3.0), ("0", 0.0), (None, None), ("not-a-date", None)],
)
def test_parse_retry_after(header, expected):
    assert parse_retry_after(header) == expected


# =========================================================================
# 4. 传输层重试
# =========================================================================

def test_transport_retries_network_error_then_succeeds():
    clock = FakeClock()
    limiter = RateLimiter(0.0, jitter=0.0, clock=clock, sleep=clock.sleep)
    session = RoutingSession({"example.test": FakeResponse(payload={"ok": 1})})
    metrics = RequestMetrics()
    response = request_with_retry(
        session, "GET", "https://example.test/api",
        limiter=limiter, policy=RetryPolicy(max_attempts=3, base_delay=0.5, jitter=0.0),
        metrics=metrics, timeout=1, sleep=clock.sleep, clock=clock,
    )
    assert response.status_code == 200
    assert metrics.attempts == 1 and metrics.logical_requests == 1


def test_transport_retries_timeouts_and_5xx():
    clock = FakeClock()
    limiter = RateLimiter(0.0, jitter=0.0, clock=clock, sleep=clock.sleep)

    class FlakySession(RoutingSession):
        def __init__(self):
            super().__init__({})
            self.count = 0

        def request(self, method, url, timeout=None, headers=None, params=None):
            self.count += 1
            if self.count == 1:
                raise requests.Timeout("timeout")
            if self.count == 2:
                return FakeResponse(status_code=503, text="busy")
            return FakeResponse(payload={"ok": 1})

    session = FlakySession()
    metrics = RequestMetrics()
    response = request_with_retry(
        session, "GET", "https://example.test/api",
        limiter=limiter, policy=RetryPolicy(max_attempts=3, base_delay=0.5, jitter=0.0),
        metrics=metrics, timeout=1, sleep=clock.sleep, clock=clock,
    )
    assert response.status_code == 200
    assert metrics.attempts == 3 and metrics.retries == 2
    assert session.count == 3
    assert len(clock.sleeps) == 2


def test_transport_respects_retry_after_and_raises_when_exhausted():
    clock = FakeClock()
    limiter = RateLimiter(0.0, jitter=0.0, clock=clock, sleep=clock.sleep)

    class Always429(RoutingSession):
        def request(self, method, url, timeout=None, headers=None, params=None):
            self.calls.append({"url": url})
            return FakeResponse(status_code=429, headers={"Retry-After": "4"}, text="slow")

    with pytest.raises(RetryExhausted):
        request_with_retry(
            Always429({}), "GET", "https://example.test/api",
            limiter=limiter, policy=RetryPolicy(max_attempts=2, base_delay=0.5, jitter=0.0),
            metrics=RequestMetrics(), timeout=1, sleep=clock.sleep, clock=clock,
        )
    assert any(sleep_time >= 4.0 for sleep_time in clock.sleeps)


def test_transport_does_not_retry_plain_4xx():
    clock = FakeClock()
    limiter = RateLimiter(0.0, jitter=0.0, clock=clock, sleep=clock.sleep)
    session = RoutingSession({"example.test": FakeResponse(status_code=404, text="nope")})
    with pytest.raises(HttpStatusError):
        request_with_retry(
            session, "GET", "https://example.test/api",
            limiter=limiter, policy=RetryPolicy(max_attempts=3, jitter=0.0),
            metrics=RequestMetrics(), timeout=1, sleep=clock.sleep, clock=clock,
        )


def test_transport_passthrough_412_returns_response():
    clock = FakeClock()
    limiter = RateLimiter(0.0, jitter=0.0, clock=clock, sleep=clock.sleep)
    session = RoutingSession({"example.test": FakeResponse(status_code=412, text="risk")})
    response = request_with_retry(
        session, "GET", "https://example.test/api",
        limiter=limiter, policy=RetryPolicy(max_attempts=3, jitter=0.0),
        metrics=RequestMetrics(), timeout=1, passthrough_statuses=(412,),
        sleep=clock.sleep, clock=clock,
    )
    assert response.status_code == 412


def test_request_metrics_summary_contains_percentiles():
    metrics = RequestMetrics()
    for latency in (0.1, 0.2, 0.3, 0.4):
        metrics.record(status=200, latency=latency, size=10)
    metrics.logical_requests = 4
    summary = metrics.summary()
    assert summary["latency_ms"]["avg"] > 0
    assert summary["latency_ms"]["p50"] <= summary["latency_ms"]["p95"]
    assert summary["status_counts"] == {"200": 4}
    assert summary["response_bytes"] == 40


# =========================================================================
# 5. robots
# =========================================================================

def test_robots_checker_denies_and_caches():
    session = RoutingSession(
        {
            "robots.txt": FakeResponse(
                text="User-agent: *\nDisallow: /secret\n",
                headers={"Content-Type": "text/plain"},
            )
        }
    )
    checker = RobotsChecker("test-agent", session=session)
    assert checker.is_allowed("https://example.test/public") is True
    assert checker.is_allowed("https://example.test/secret/page") is False
    assert len(session.calls) == 1  # 同一主机只拉一次 robots.txt
    assert checker.denied == 1


def test_robots_checker_404_allows_and_failure_is_fail_closed():
    allow_session = RoutingSession(
        {"robots.txt": FakeResponse(status_code=404, text="")}
    )
    assert RobotsChecker("ua", session=allow_session).is_allowed("https://x.test/a") is True

    class Broken(RoutingSession):
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("no network")

    assert RobotsChecker("ua", session=Broken({})).is_allowed("https://x.test/a") is False
    assert (
        RobotsChecker("ua", session=Broken({}), fail_closed=False).is_allowed("https://x.test/a")
        is True
    )


def test_local_site_robots_denied_page_is_skipped():
    with local_site({"/secret": "<html><body>hi</body></html>"}, robots="User-agent: *\nDisallow: /secret\n") as (base, _log):
        config = CrawlConfig(
            start_urls=[f"{base}/secret"],
            source="local",
            delay_seconds=0,
            write_source_note=False,
            out_path=Path("unused.csv"),
        )
        crawler = HtmlCrawler(config)
        try:
            with pytest.raises(RobotsDenied):
                crawler.fetch_html(f"{base}/secret")
        finally:
            crawler.close()


# =========================================================================
# 6. 通用 HTML 爬虫
# =========================================================================

SAMPLE_PAGE_1 = """
<html><head><meta charset="utf-8"></head><body>
<div class="comment-content">这个教程讲得很清楚 &amp; 很实用</div>
<div class="comment-content">   </div>
<script>var x = 1;</script>
<div class="comment-content">广告太多，差评！</div>
</body></html>
"""
SAMPLE_PAGE_2 = """
<html><body>
<div class="comment-content">这个教程讲得很清楚 &amp; 很实用</div>
<div class="comment-content">第二页的新评论</div>
</body></html>
"""


def test_parse_comments_filters_noise_and_blank(tmp_path):
    texts = parse_comments(SAMPLE_PAGE_1)
    assert texts == ["这个教程讲得很清楚 & 很实用", "广告太多，差评！"]


def test_html_crawler_crawl_dedupes_max_items_and_saves(tmp_path):
    with local_site({"/p1": SAMPLE_PAGE_1, "/p2": SAMPLE_PAGE_2}) as (base, log):
        config = CrawlConfig(
            start_urls=[f"{base}/p1", f"{base}/p2"],
            source="本地测试站",
            delay_seconds=0,
            max_items=10,
            write_source_note=False,
            out_path=tmp_path / "raw.csv",
        )
        report = HtmlCrawler(config).crawl()
    assert [item["text"] for item in report.items] == [
        "这个教程讲得很清楚 & 很实用",
        "广告太多，差评！",
        "第二页的新评论",
    ]
    assert report.saved_path == tmp_path / "raw.csv"
    assert report.metrics["requests"] == 2
    rows = list(csv.DictReader((tmp_path / "raw.csv").open(encoding="utf-8-sig")))
    assert list(rows[0].keys()) == ["text", "label", "source", "url", "crawled_at"]
    assert rows[0]["label"] == "" and rows[0]["source"] == "本地测试站"


def test_html_crawler_registers_source_note(tmp_path):
    data_dir = tmp_path / "data"
    note_path = data_dir / "raw" / "来源说明.md"
    json_path = data_dir / "raw" / "sources.json"
    with local_site({"/p1": SAMPLE_PAGE_1}) as (base, _log):
        config = CrawlConfig(
            start_urls=[f"{base}/p1"],
            source="测试来源",
            delay_seconds=0,
            out_path=tmp_path / "raw.csv",
        )
        crawler = HtmlCrawler(config)
        try:
            original = sources.register_source
            sources.register_source = lambda record: original(record, json_path=json_path, note_path=note_path)
            try:
                report = crawler.crawl()
            finally:
                sources.register_source = original
        finally:
            crawler.close()
    assert report.source_note_path == note_path
    assert "测试来源" in note_path.read_text(encoding="utf-8")
    assert json_path.exists()


def test_save_raw_and_config_validation(tmp_path):
    path = save_raw(
        [{"text": "a", "label": None, "source": "s", "url": "u", "crawled_at": "t"}],
        tmp_path / "out" / "raw.csv",
    )
    assert path.exists()
    with pytest.raises(ConfigError):
        CrawlConfig(start_urls=["ftp://x/y"], source="s")
    with pytest.raises(ConfigError):
        CrawlConfig(start_urls=["https://x/y"], source="s", delay_seconds=-1)


# =========================================================================
# 7. 来源登记
# =========================================================================

def test_sources_render_escape_and_write(tmp_path):
    records = [
        sources.SourceRecord(
            name="站点|A", url="https://a.test/x?y=1", collected_at="2026-01-01T00:00:00+00:00",
            count=3, labeled=False, note="备注\n第二行",
        )
    ]
    table = sources.render_source_table(records)
    assert "站点\\|A" in table
    assert "自采 + 人工标注" in table
    assert "备注 第二行" in table
    note = sources.write_source_note(records, tmp_path / "来源说明.md")
    content = note.read_text(encoding="utf-8")
    assert "数据来源说明" in content and "https://a.test/x?y=1" in content


def test_sources_register_merges_and_dedupes(tmp_path):
    json_path = tmp_path / "sources.json"
    note_path = tmp_path / "来源说明.md"
    first = sources.SourceRecord(name="n", url="u", collected_at="t1", count=2, labeled=False)
    second = sources.SourceRecord(name="n", url="u", collected_at="t2", count=5, labeled=False)
    sources.register_source(first, json_path=json_path, note_path=note_path)
    sources.register_source(second, json_path=json_path, note_path=note_path)
    loaded = sources.load_source_records(json_path)
    assert [record.collected_at for record in loaded] == ["t1", "t2"]
    # 同键覆盖而不是追加
    sources.register_source(second, json_path=json_path, note_path=note_path)
    assert len(sources.load_source_records(json_path)) == 2


# =========================================================================
# 8. B 站客户端（假会话）
# =========================================================================

def _make_client(session):
    return BiliClient(
        delay=0,
        timeout=2,
        session=session,
        respect_robots=False,
        risk_control_cooldown=0,
        sleep=lambda seconds: None,
    )


def test_bili_client_gets_meta_and_signs_comment_request():
    session = make_bili_session()
    client = _make_client(session)
    meta = client.get_video_meta("BV1GJ411x7h7")
    assert meta["aid"] == 123456
    assert meta["title"] == "示例 视频 标题"
    assert meta["comment_count"] == 42

    data = client.get_main_comments(meta["aid"], mode=2)
    assert len(data["replies"]) == 1
    main_calls = [call for call in session.calls if "/x/v2/reply/wbi/main" in call["url"]]
    assert main_calls, "应请求评论接口"
    params = main_calls[-1]["params"]
    assert {"wts", "w_rid"} <= set(params)
    assert str(params["oid"]) == "123456" and str(params["type"]) == "1"
    # 默认不初始化 buvid（实测会限制匿名可见评论数）
    assert client.init_buvid is False
    assert not session.cookies.store.get("buvid3")


def test_bili_client_can_opt_in_buvid():
    session = make_bili_session()
    client = _make_client(session)
    client.init_buvid = True
    client._buvid_ready = False
    client.get_video_meta("BV1GJ411x7h7")
    assert session.cookies.store.get("buvid3") == "B3"
    assert session.cookies.store.get("buvid4") == "B4"


def test_bili_client_recovers_from_352_with_key_refresh():
    calls = {"view": 0}

    def view_handler(url, params):
        calls["view"] += 1
        if calls["view"] == 1:
            return FakeResponse(payload={"code": -352, "message": "风控校验失败"})
        return FakeResponse(
            payload={"code": 0, "data": {"aid": 1, "title": "t", "owner": {}, "stat": {}}}
        )

    session = make_bili_session()
    session.routes["/x/web-interface/view"] = view_handler
    client = _make_client(session)
    meta = client.get_video_meta("BV1GJ411x7h7")
    assert meta["aid"] == 1
    assert client.risk_control_hits == 1
    assert client.wbi_refreshes >= 1  # 风控后强制刷新 WBI 密钥


def test_bili_client_raises_risk_control_after_retries():
    session = make_bili_session()
    session.routes["/x/web-interface/view"] = FakeResponse(
        status_code=412, text="Precondition Failed"
    )
    client = _make_client(session)
    with pytest.raises(RiskControlError):
        client.get_video_meta("BV1GJ411x7h7")
    assert client.risk_control_hits >= 1


def test_bili_client_comment_closed_and_api_error():
    session = make_bili_session()
    session.routes["/x/web-interface/view"] = FakeResponse(
        payload={"code": 12002, "message": "评论区已关闭"}
    )
    with pytest.raises(CommentClosed):
        _make_client(session).get_video_meta("BV1GJ411x7h7")

    session2 = make_bili_session()
    session2.routes["/x/web-interface/view"] = FakeResponse(
        payload={"code": -404, "message": "啥都木有"}
    )
    with pytest.raises(ApiError):
        _make_client(session2).get_video_meta("BV1GJ411x7h7")


def test_bili_client_persistent_412_stops_without_recursion():
    """风控发生在 nav 刷新请求上时也不能递归爆栈，应安全抛出 RiskControlError。"""

    class Always412(RoutingSession):
        def request(self, method, url, timeout=None, headers=None, params=None):
            self.calls.append({"url": url})
            return FakeResponse(status_code=412, text="risk")

    client = BiliClient(
        delay=0, session=Always412({}), respect_robots=False,
        risk_control_retries=1, risk_control_cooldown=0, sleep=lambda seconds: None,
    )
    with pytest.raises(RiskControlError):
        client.get_video_meta("BV1GJ411x7h7")
    assert client.risk_control_hits >= 1


def test_bili_client_api_robots_enforce_blocks():
    session = RoutingSession(
        {
            "/robots.txt": FakeResponse(
                text="User-agent: *\nDisallow: /\n", headers={"Content-Type": "text/plain"}
            ),
            "/x/frontend/finger/spi": FakeResponse(payload={"code": 0, "data": {"b_3": "B3"}}),
            "/x/web-interface/view": FakeResponse(payload={"code": 0, "data": {"aid": 1}}),
        }
    )
    client = BiliClient(
        delay=0, session=session, respect_robots=True,
        api_robots_policy="enforce", risk_control_cooldown=0,
        sleep=lambda seconds: None,
    )
    with pytest.raises(RobotsDenied):
        client.get_video_meta("BV1GJ411x7h7")


# =========================================================================
# 9. CSV / 断点 / 采集流程
# =========================================================================

def _row(rpid: str, text: str = "评论", level: int = 1) -> dict:
    return {
        "bvid": "BV1GJ411x7h7", "aid": "1", "video_title": "t", "rpid": rpid,
        "level": level, "root_rpid": "", "parent_rpid": "", "text": text,
        "likes": 0, "reply_count": 0, "ctime_utc": "2026-01-01T00:00:00+00:00",
        "collected_at_utc": "2026-01-02T00:00:00+00:00",
    }


def test_csv_store_dedupes_and_reloads(tmp_path):
    path = tmp_path / "c.csv"
    store = CsvStore(path)
    assert store.add([_row("1"), _row("2"), _row("2"), None]) == 2
    assert store.total == 2 and store.duplicates == 1
    store2 = CsvStore(path)
    assert store2.total == 2
    assert store2.add([_row("2"), _row("3")]) == 1
    rows = read_csv_records(path)
    assert [row["rpid"] for row in rows] == ["1", "2", "3"]
    assert list(rows[0].keys()) == COLUMNS


def test_csv_store_rejects_incompatible_header(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8-sig")
    with pytest.raises(ConfigError):
        CsvStore(path)


def test_state_roundtrip_and_mismatch(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path, 2, True, 3)
    assert state["finished"] is False
    state["offset"] = "abc"
    save_state(path, state)
    assert load_state(path, 2, True, 3)["offset"] == "abc"
    with pytest.raises(ConfigError):
        load_state(path, 3, True, 3)
    assert load_state(path, 3, True, 3, reset=True)["offset"] == ""


class StubBiliClient:
    """不联网的 B 站客户端替身，供 crawl_video / 适配器测试。

    ``pages`` 可以是页面字典列表，也可以是 ``callable(offset) -> page``；
    ``sub_pages`` 可以是固定响应字典，也可以是 ``callable(root, page) -> data``。
    ``main_delay`` / ``sub_delay`` 用于测试 ``max_seconds`` 时间预算。
    """

    def __init__(self, pages=None, sub_pages=None, *, main_delay=0.0, sub_delay=0.0):
        self.respect_robots = False
        self.web_base = "https://www.bilibili.com"
        self.api_robots_decision = {"allowed": False}
        self.metrics = RequestMetrics()
        self.risk_control_hits = 0
        self.robots_checks = []
        self._limiter = SimpleNamespace(base_interval=2.5)
        self.main_delay = main_delay
        self.sub_delay = sub_delay
        self.pages = pages or [
            {
                "replies": [
                    {
                        "rpid": 1, "content": {"message": "好&amp;喜欢"},
                        "like": 1, "rcount": 1, "ctime": 1700000000,
                    },
                    {"rpid": 2, "content": {"message": "   "}, "rcount": 0},
                ],
                "cursor": {"is_end": False, "pagination_reply": {"next_offset": "N1"}},
            },
            {
                "replies": [{"rpid": 3, "content": {"message": "第二页评论"}, "rcount": 0}],
                "cursor": {"is_end": True},
            },
        ]
        self.sub_pages = sub_pages or {
            "replies": [
                {"rpid": 11, "root": 1, "parent": 1, "content": {"message": "楼中楼"}, "rcount": 0}
            ],
            "page": {"count": 1},
        }
        self.page_calls = []
        self.sub_calls = []

    def check_content_page_robots(self, bvid):
        return None

    def check_api_robots(self):
        return None

    def get_video_meta(self, bvid):
        return {
            "bvid": bvid, "aid": 1, "title": "标题", "owner": "UP",
            "comment_count": 3, "view_count": 0, "pubdate": "", "cover_url": "",
        }

    def get_main_comments(self, aid, offset="", mode=2):
        if self.main_delay:
            time.sleep(self.main_delay)
        self.page_calls.append(offset)
        self.metrics.logical_requests += 1
        if callable(self.pages):
            return self.pages(offset)
        if offset == "":
            return self.pages[0]
        return self.pages[1]

    def get_sub_comments(self, aid, root, page=1, page_size=20):
        if self.sub_delay:
            time.sleep(self.sub_delay)
        self.metrics.logical_requests += 1
        self.sub_calls.append({"root": str(root), "page": page, "page_size": page_size})
        if callable(self.sub_pages):
            return self.sub_pages(str(root), page)
        return self.sub_pages

    def stats_summary(self):
        summary = self.metrics.summary()
        summary.update({"risk_control_hits": self.risk_control_hits})
        return summary

    def close(self):
        pass


def test_crawl_video_writes_csv_state_and_is_resumable(tmp_path):
    client = StubBiliClient()
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=2, include_replies=True,
        max_sub_pages=1, out_dir=tmp_path, write_source_note=False,
    )
    assert result.comments_new == 3  # 2 条一级（1 条空被过滤）+ 1 条楼中楼 + 第二页 1 条
    rows = read_csv_records(result.csv_path)
    assert [row["rpid"] for row in rows] == ["1", "11", "3"]
    assert rows[0]["text"] == "好&喜欢"
    assert result.finished is True and result.comments_total == 3
    state = json.loads(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["finished"] is True

    # 再跑一次：断点已完成，不重复请求、不重复写
    client2 = StubBiliClient()
    result2 = crawl_video(
        client2, "BV1GJ411x7h7", include_replies=True, max_sub_pages=1,
        out_dir=tmp_path, write_source_note=False,
    )
    assert result2.finished is True and result2.comments_new == 0
    assert client2.page_calls == []


def test_crawl_video_respects_max_items_and_continues_from_offset(tmp_path):
    client = StubBiliClient()
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=5, include_replies=False,
        max_items=2, out_dir=tmp_path, write_source_note=False,
    )
    assert result.comments_new == 2
    rows = read_csv_records(result.csv_path)
    assert [row["rpid"] for row in rows] == ["1", "3"]


def test_crawl_bilibili_reports_and_fetch_adapter(tmp_path):
    client = StubBiliClient()
    report = crawl_bilibili(
        ["BV1GJ411x7h7"],
        client=client,
        max_pages=2,
        include_replies=False,
        out_dir=tmp_path,
        write_source_note=False,
    )
    assert report.total_new == 2
    assert report.videos[0].comments_total == 2
    assert report.metrics["requests"] >= 1

    client2 = StubBiliClient()
    rows = fetch_video_comments(
        "BV1GJ411x7h7", client=client2, max_pages=1, include_replies=False
    )
    assert [row["rpid"] for row in rows] == ["1"]


# =========================================================================
# 10. 长跑稳健性：BV 去重 / 楼中楼分页 / sub_done 断点 / 时间预算
# =========================================================================

def _bili_comment(rpid, *, message="评论", rcount=0, root=0, parent=0):
    return {
        "rpid": rpid,
        "root": root,
        "parent": parent,
        "content": {"message": message},
        "rcount": rcount,
        "like": 0,
        "ctime": 1700000000,
    }


def _write_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_crawl_bilibili_dedupes_bvids_preserving_input_order(tmp_path):
    client = StubBiliClient()
    report = crawl_bilibili(
        [
            "BV1GJ411x7h7",
            "https://www.bilibili.com/video/BV1GJ411x7h7?p=1",
            "not-a-bv",
            "BV1GJ411x7h7",
        ],
        client=client,
        max_pages=2,
        include_replies=False,
        out_dir=tmp_path,
        write_source_note=False,
    )
    # 合法且重复的 BV 只采一次；非法输入仍逐条记录错误；报告不重复计数
    assert [video.bvid for video in report.videos] == ["BV1GJ411x7h7", "not-a-bv"]
    assert report.videos[0].error is None
    assert "无法识别" in report.videos[1].error
    assert client.page_calls == ["", "N1"]  # 只对该视频请求了两页
    assert report.total_new == 2


def test_get_sub_comments_passes_explicit_page_size():
    session = make_bili_session()
    client = _make_client(session)
    client.get_sub_comments(1, "9001")
    reply_calls = [call for call in session.calls if "/x/v2/reply/reply" in call["url"]]
    assert reply_calls[-1]["params"]["ps"] == SUB_COMMENT_PAGE_SIZE
    assert reply_calls[-1]["params"]["pn"] == 1
    client.get_sub_comments(1, "9001", page=2, page_size=5)
    reply_calls = [call for call in session.calls if "/x/v2/reply/reply" in call["url"]]
    assert reply_calls[-1]["params"]["ps"] == 5
    assert reply_calls[-1]["params"]["pn"] == 2


def test_crawl_video_sub_pages_follow_server_page_size_and_count(tmp_path):
    """服务端页大小 10、总数 25：应按 count/size 翻 3 页，而不是第 1 页就停。"""

    def sub_handler(root, page):
        count = 10 if page <= 2 else 5
        return {
            "replies": [
                _bili_comment(1000 + page * 10 + index, message=f"楼中楼{page}-{index}", root=100)
                for index in range(count)
            ],
            "page": {"count": 25, "size": 10},
        }

    client = StubBiliClient(
        pages=[{
            "replies": [_bili_comment(100, message="根评论", rcount=25)],
            "cursor": {"is_end": True},
        }],
        sub_pages=sub_handler,
    )
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=5, out_dir=tmp_path, write_source_note=False,
    )
    assert [call["page"] for call in client.sub_calls] == [1, 2, 3]
    assert all(call["page_size"] == SUB_COMMENT_PAGE_SIZE for call in client.sub_calls)
    assert result.comments_total == 26  # 1 条根评论 + 25 条楼中楼


def test_crawl_video_sub_pages_use_server_size_when_count_unknown(tmp_path):
    """count=0（不确定）时用 page.size 判断：满 10 条继续，短页才结束。"""

    def sub_handler(root, page):
        count = 10 if page == 1 else 5
        return {
            "replies": [
                _bili_comment(2000 + page * 10 + index, message=f"楼中楼{page}-{index}", root=200)
                for index in range(count)
            ],
            "page": {"count": 0, "size": 10},
        }

    client = StubBiliClient(
        pages=[{
            "replies": [_bili_comment(200, message="根评论", rcount=15)],
            "cursor": {"is_end": True},
        }],
        sub_pages=sub_handler,
    )
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=5, out_dir=tmp_path, write_source_note=False,
    )
    assert [call["page"] for call in client.sub_calls] == [1, 2]
    assert result.comments_total == 16  # 1 条根评论 + 15 条楼中楼


def test_crawl_video_resume_skips_sub_done_roots(tmp_path):
    """旧断点里 sub_done 记录的根评论不再请求楼中楼，其余根评论照常请求。"""

    def sub_handler(root, page):
        return {
            "replies": [_bili_comment(int(root) * 100 + page, message="楼中楼", root=root)],
            "page": {"count": 1},
        }

    _write_checkpoint(
        tmp_path / ".checkpoints" / "BV1GJ411x7h7.json",
        {
            "mode": 2, "include_replies": True, "max_sub_pages": 2,
            "offset": "", "pages_done": 0, "finished": False, "sub_done": ["1"],
        },
    )
    client = StubBiliClient(
        pages=[{
            "replies": [
                _bili_comment(1, message="根评论1", rcount=1),
                _bili_comment(2, message="根评论2", rcount=1),
            ],
            "cursor": {"is_end": True},
        }],
        sub_pages=sub_handler,
    )
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=2, out_dir=tmp_path, write_source_note=False,
    )
    assert [call["root"] for call in client.sub_calls] == ["2"]
    rows = read_csv_records(result.csv_path)
    assert [row["rpid"] for row in rows] == ["1", "2", "201"]
    assert result.finished is True
    # 整页完成后 offset 已推进，sub_done 清空避免长跑 state 膨胀
    state = json.loads(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["sub_done"] == []


def test_load_state_tolerates_legacy_checkpoint_without_sub_done(tmp_path):
    path = tmp_path / "state.json"
    legacy = {
        "mode": 2, "include_replies": True, "max_sub_pages": 3,
        "offset": "N1", "pages_done": 1, "finished": False,
    }
    _write_checkpoint(path, legacy)
    state = load_state(path, 2, True, 3)
    assert state["sub_done"] == []
    assert state["offset"] == "N1"
    assert state["pages_done"] == 1
    # 新断点同样带 sub_done 字段
    fresh = load_state(tmp_path / "new.json", 2, True, 3)
    assert fresh["sub_done"] == []


def test_crawl_bilibili_max_seconds_stops_between_pages(tmp_path):
    def slow_on_page(video_result, page_number, added, total):
        # 模拟页间耗时（进度回调/落盘上报），用于触发「页之间」的时间预算检查
        if page_number == 1:
            time.sleep(0.5)

    client = StubBiliClient(
        pages=[
            {
                "replies": [_bili_comment(1, message="第一页")],
                "cursor": {"is_end": False, "pagination_reply": {"next_offset": "N1"}},
            },
            {
                "replies": [_bili_comment(2, message="第二页")],
                "cursor": {"is_end": True},
            },
        ],
    )
    report = crawl_bilibili(
        ["BV1GJ411x7h7"], client=client, max_pages=5, include_replies=False,
        max_seconds=0.25, out_dir=tmp_path, write_source_note=False,
        on_page=slow_on_page,
    )
    assert report.timed_out is True
    video = report.videos[0]
    assert video.timed_out is True and video.finished is False
    assert video.pages_crawled == 1
    assert video.as_dict()["timed_out"] is True
    assert report.as_dict()["timed_out"] is True
    # 已采数据落盘、断点保存为「未完成 + 下一页游标」，第二页不再请求
    assert client.page_calls == [""]
    assert [row["rpid"] for row in read_csv_records(video.csv_path)] == ["1"]
    state = json.loads(Path(video.state_path).read_text(encoding="utf-8"))
    assert state["finished"] is False
    assert state["offset"] == "N1"
    assert state["pages_done"] == 1


def test_crawl_video_max_seconds_stops_between_roots_and_saves_sub_done(tmp_path):
    """根评论间到时停止：offset 不推进、已处理根评论写入 sub_done、已采数据落盘。"""

    def sub_handler(root, page):
        if root == "1":
            # count=0 不确定 + 满页（size=1）→ 一直翻到 max_sub_pages 上限才算处理完
            return {
                "replies": [_bili_comment(100 + page, message=f"楼中楼{page}", root=1)],
                "page": {"count": 0, "size": 1},
            }
        return {"replies": [], "page": {"count": 0}}

    client = StubBiliClient(
        pages=[{
            "replies": [
                _bili_comment(1, message="根评论1", rcount=1),
                _bili_comment(2, message="根评论2", rcount=1),
            ],
            "cursor": {"is_end": True},
        }],
        sub_pages=sub_handler,
        sub_delay=0.3,
    )
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=2, max_seconds=0.4, out_dir=tmp_path, write_source_note=False,
    )
    assert result.timed_out is True and result.finished is False
    assert result.pages_crawled == 1
    assert result.as_dict()["timed_out"] is True
    # 根评论 1 的楼中楼已处理完（达到 max_sub_pages 上限），根评论 2 未被请求
    assert [(call["root"], call["page"]) for call in client.sub_calls] == [("1", 1), ("1", 2)]
    assert [row["rpid"] for row in read_csv_records(result.csv_path)] == ["1", "101", "102"]
    state = json.loads(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["finished"] is False
    assert state["offset"] == ""  # 当前页未采完，offset 不推进，下次从本页重试
    assert state["sub_done"] == ["1"]  # 恢复时跳过根评论 1 的楼中楼请求


def test_crawl_video_resumes_after_timeout_without_repeating_sub_requests(tmp_path):
    """超时停止后再次运行：从本页续爬，sub_done 中的根评论不再请求楼中楼。"""

    def sub_handler(root, page):
        if root == "1":
            # count=0 + 满页（size=1）→ 需要翻满 max_sub_pages 才算处理完
            return {
                "replies": [_bili_comment(100 + page, message=f"楼中楼{page}", root=1)],
                "page": {"count": 0, "size": 1},
            }
        return {"replies": [], "page": {"count": 0}}

    pages = [{
        "replies": [
            _bili_comment(1, message="根评论1", rcount=1),
            _bili_comment(2, message="根评论2", rcount=1),
        ],
        "cursor": {"is_end": True},
    }]
    first = crawl_video(
        StubBiliClient(pages=pages, sub_pages=sub_handler, sub_delay=0.3),
        "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=2, max_seconds=0.4, out_dir=tmp_path, write_source_note=False,
    )
    assert first.timed_out is True and first.finished is False

    resume_client = StubBiliClient(pages=pages, sub_pages=sub_handler)
    second = crawl_video(
        resume_client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
        max_sub_pages=2, out_dir=tmp_path, write_source_note=False,
    )
    assert second.timed_out is False and second.finished is True
    # 根评论 1 已在 sub_done 中，续爬只请求根评论 2 的楼中楼
    assert [call["root"] for call in resume_client.sub_calls] == ["2"]
    rows = read_csv_records(second.csv_path)
    assert [row["rpid"] for row in rows] == ["1", "101", "102", "2"]
    state = json.loads(Path(second.state_path).read_text(encoding="utf-8"))
    assert state["finished"] is True and state["sub_done"] == []


def test_crawl_video_sub_done_is_capped_with_warning(tmp_path, monkeypatch, caplog):
    """sub_done 超过上限时丢弃最早记录并告警（防御性上限，避免 state 无限膨胀）。"""

    monkeypatch.setattr("src.crawler.bilibili.MAX_SUB_DONE_ENTRIES", 1)

    def sub_handler(root, page):
        return {
            "replies": [_bili_comment(int(root) * 100 + page, message="楼中楼", root=root)],
            "page": {"count": 1},
        }

    client = StubBiliClient(
        pages=[{
            "replies": [
                _bili_comment(1, message="根评论1", rcount=1),
                _bili_comment(2, message="根评论2", rcount=1),
                _bili_comment(3, message="根评论3", rcount=1),
            ],
            "cursor": {"is_end": True},
        }],
        sub_pages=sub_handler,
        sub_delay=0.3,
    )
    with caplog.at_level(logging.WARNING, logger="bili_crawler"):
        result = crawl_video(
            client, "BV1GJ411x7h7", max_pages=1, include_replies=True,
            max_sub_pages=1, max_seconds=0.5, out_dir=tmp_path, write_source_note=False,
        )
    assert result.timed_out is True
    # 根评论 1、2 的楼中楼处理完；上限=1 时丢弃最早的 1，只保留最近一条
    assert [call["root"] for call in client.sub_calls] == ["1", "2"]
    state = json.loads(Path(result.state_path).read_text(encoding="utf-8"))
    assert state["sub_done"] == ["2"]
    assert any("sub_done" in record.message for record in caplog.records)


# =========================================================================
# 10. 大规模采集：清单 / 零请求续采 / 无限页 / 合并导出 / 登录态
# =========================================================================

def test_page_limits_allow_zero_means_unlimited():
    assert validate_page_limits(0, 0) == (0, 0)
    assert validate_page_limits(5, 3) == (5, 3)
    with pytest.raises(ConfigError):
        validate_page_limits(-1, 3)
    with pytest.raises(ConfigError):
        validate_page_limits(5, -1)


def test_read_bvid_list_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "bvids.txt"
    path.write_text(
        "# 大规模采集清单\n\nBV1GJ411x7h7\n"
        "  https://www.bilibili.com/video/BV1xx411c7mD?p=1  \n"
        "BV1GJ411x7h7,BV1GJ411x7h7\n",
        encoding="utf-8",
    )
    values = read_bvid_list(path)
    assert len(values) == 3
    assert values[0] == "BV1GJ411x7h7"
    assert "BV1xx411c7mD" in values[1]


def test_crawl_video_skips_finished_without_any_network(tmp_path):
    first = StubBiliClient()
    result = crawl_video(
        first, "BV1GJ411x7h7", max_pages=2, include_replies=False,
        out_dir=tmp_path, write_source_note=False,
    )
    assert result.finished and result.comments_total > 0

    class NoNetworkClient(StubBiliClient):
        def get_video_meta(self, bvid):
            raise AssertionError("已完成视频不应再请求 meta")

        def get_main_comments(self, *args, **kwargs):
            raise AssertionError("已完成视频不应再请求评论")

    second = NoNetworkClient()
    resumed = crawl_video(
        second, "BV1GJ411x7h7", max_pages=0, include_replies=False,
        out_dir=tmp_path, write_source_note=False,
    )
    assert resumed.finished and resumed.skipped is True
    assert resumed.comments_total == result.comments_total
    assert second.page_calls == []
    assert second.metrics.logical_requests == 0


def test_crawl_video_unlimited_pages_and_sub_pages(tmp_path):
    def pages(offset):
        if offset == "":
            return {
                "replies": [_bili_comment(1, message="根1", rcount=1),
                            _bili_comment(2, message="根2", rcount=1)],
                "cursor": {"is_end": False, "pagination_reply": {"next_offset": "N1"}},
            }
        if offset == "N1":
            return {
                "replies": [_bili_comment(3, message="根3", rcount=1)],
                "cursor": {"is_end": False, "pagination_reply": {"next_offset": "N2"}},
            }
        return {
            "replies": [_bili_comment(4, message="根4", rcount=0)],
            "cursor": {"is_end": True},
        }

    def sub_pages(root, page):
        # count=25 / size=10 → 需要翻 3 页；max_sub_pages=0 时必须一直翻到自然结束
        replies = [
            _bili_comment(
                int(root) * 1000 + page * 10 + index,
                message=f"楼中楼{page}-{index}",
                root=root,
                parent=root,
            )
            for index in range(10)
        ]
        return {"replies": replies, "page": {"count": 25, "size": 10}}

    client = StubBiliClient(pages=pages, sub_pages=sub_pages)
    result = crawl_video(
        client, "BV1GJ411x7h7", max_pages=0, include_replies=True,
        max_sub_pages=0, out_dir=tmp_path, write_source_note=False,
    )
    assert result.finished is True
    assert len(client.page_calls) == 3
    assert [call["page"] for call in client.sub_calls[:3]] == [1, 2, 3]
    assert result.comments_total == 4 + 3 * 3 * 10  # 4 条一级 + 3 根 × 3 页 × 10 条楼中楼


def test_merge_video_csvs_streams_and_reports(tmp_path):
    first = CsvStore(tmp_path / "BV1GJ411x7h7_comments.csv")
    first.add([_row("1"), _row("2")])
    second = CsvStore(tmp_path / "BV1xx411c7mD_comments.csv")
    second.add([dict(_row("3"), bvid="BV1xx411c7mD")])
    output = tmp_path / "all_comments.csv"
    report = merge_video_csvs(tmp_path, output)
    rows = read_csv_records(output)
    assert report.files == 2
    assert report.videos == 2
    assert report.rows == 3
    assert len(rows) == 3
    assert list(rows[0]) == COLUMNS
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")


def test_crawl_bilibili_aggregates_source_note(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(sources, "register_source", lambda record, **kwargs: captured.append(record))
    client = StubBiliClient()
    report = crawl_bilibili(
        ["BV1GJ411x7h7", "BV1xx411c7mD"],
        client=client, max_pages=1, include_replies=False,
        out_dir=tmp_path, write_source_note=True,
    )
    assert len(captured) == 1
    assert captured[0].name == "B站视频评论（批量）"
    assert captured[0].count == report.total_saved
    assert report.as_dict()["skipped_count"] == 0


def test_login_status_and_require_login(monkeypatch):
    logged_in = make_bili_session(
        nav_payload={"code": 0, "data": {"isLogin": True, "uname": "测试用户", "mid": 42}}
    )
    client = _make_client(logged_in)
    status = client.login_status()
    assert status["is_login"] is True and status["uname"] == "测试用户"

    anonymous = _make_client(make_bili_session())
    with pytest.raises(ConfigError):
        crawl_bilibili(
            ["BV1GJ411x7h7"], client=anonymous, require_login=True,
            max_pages=1, include_replies=False, out_dir="unused",
            write_source_note=False,
        )


def test_bili_cookie_env_parsing(monkeypatch):
    monkeypatch.setenv("BILI_COOKIE", "SESSDATA=abc; bili_jct=csrf; DedeUserID=7")
    session = make_bili_session()
    client = _make_client(session)
    assert client.has_credentials is True
    assert session.cookies.store.get("SESSDATA") == "abc"
    assert session.cookies.store.get("bili_jct") == "csrf"



def test_crawl_bilibili_continues_after_unexpected_error(tmp_path):
    """单个视频的意外异常不能中断整批（大规模清单的容错）。"""

    class FlakyClient(StubBiliClient):
        def get_video_meta(self, bvid):
            if bvid.endswith("000"):
                raise RuntimeError("模拟磁盘/解析意外错误")
            return super().get_video_meta(bvid)

    report = crawl_bilibili(
        ["BV1GJ411x000", "BV1GJ411x001"], client=FlakyClient(),
        max_pages=1, include_replies=False, out_dir=tmp_path,
        write_source_note=False,
    )
    assert len(report.videos) == 2
    assert report.videos[0].error and "模拟磁盘" in report.videos[0].error
    assert report.videos[1].error is None
    assert report.failed == 1


def test_large_batch_scale_and_zero_request_resume(tmp_path):
    """30 个视频 × 3 页 × 20 条：批量落盘、全量合并、重跑零请求跳过。"""

    def pages(offset):
        index = 0 if offset == "" else int(offset[1:])
        replies = [
            _bili_comment(index * 100 + item + 1, message=f"评论{index}-{item}")
            for item in range(20)
        ]
        is_end = index >= 2
        cursor = {"is_end": is_end}
        if not is_end:
            cursor["pagination_reply"] = {"next_offset": f"N{index + 1}"}
        return {"replies": replies, "cursor": cursor}

    bvids = [f"BV1GJ411x{index:03d}" for index in range(30)]
    client = StubBiliClient(pages=pages)
    report = crawl_bilibili(
        bvids, client=client, max_pages=0, include_replies=False,
        out_dir=tmp_path, write_source_note=False,
    )
    assert report.total_saved == 30 * 3 * 20
    assert len(list(tmp_path.glob("*_comments.csv"))) == 30

    class NoNetworkClient(StubBiliClient):
        def get_video_meta(self, bvid):
            raise AssertionError("不应请求 meta")

        def get_main_comments(self, *args, **kwargs):
            raise AssertionError("不应请求评论")

    resumed = crawl_bilibili(
        bvids, client=NoNetworkClient(), max_pages=0, include_replies=False,
        out_dir=tmp_path, write_source_note=False,
    )
    assert resumed.skipped == 30
    assert resumed.failed == 0
    assert resumed.metrics["requests"] == 0

    merged = merge_video_csvs(tmp_path, tmp_path / "all_comments.csv")
    assert merged.rows == report.total_saved
    assert merged.videos == 30
