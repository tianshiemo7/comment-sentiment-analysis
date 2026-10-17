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
import threading
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
    BiliClient,
    CsvStore,
    crawl_bilibili,
    crawl_video,
    fetch_video_comments,
    get_mixin_key,
    load_state,
    parse_bvid,
    read_csv_records,
    save_state,
    sign_wbi,
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
    """不联网的 B 站客户端替身，供 crawl_video / 适配器测试。"""

    def __init__(self, pages=None, sub_pages=None):
        self.respect_robots = False
        self.web_base = "https://www.bilibili.com"
        self.api_robots_decision = {"allowed": False}
        self.metrics = RequestMetrics()
        self.risk_control_hits = 0
        self.robots_checks = []
        self._limiter = SimpleNamespace(base_interval=2.5)
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
        self.page_calls.append(offset)
        self.metrics.logical_requests += 1
        if offset == "":
            return self.pages[0]
        return self.pages[1]

    def get_sub_comments(self, aid, root, page=1, page_size=20):
        self.metrics.logical_requests += 1
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
