#!/usr/bin/env python3
"""爬虫效率基准：离线 mock API + 真实联网轻量测试。

用法：

.. code-block:: bash

    # 1) 离线基准：本地 mock 服务模拟 B 站接口（含 WBI 校验、分页、楼中楼、风控恢复），
    #    测客户端/解析/落盘吞吐，不访问外网。
    python scripts/bench_crawler.py offline --pages 5 --per-page 20

    # 2) 真实联网轻量测试：默认 2.5 秒/请求，1 页一级评论，不采楼中楼。
    python scripts/bench_crawler.py live --bvid BV1GJ411x7h7 --max-pages 1

    # 结果默认写入 outputs/benchmarks/crawler_<模式>_<时间戳>.json，并打印 Markdown 表。

设计目标：给出**可复现**的效率数字，并把「配置、环境、请求数、重试、延迟分位、
评论吞吐、风控恢复」一起记录，避免只写一个没有上下文的「耗时 X 秒」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler.bilibili import (  # noqa: E402
    BiliClient,
    crawl_bilibili,
    get_mixin_key,
    read_csv_records,
)
from src.crawler.normalize import normalize_text  # noqa: E402
from src.crawler.rate_limit import RateLimiter  # noqa: E402

#: mock 服务用的 WBI 密钥（与真实接口同格式的 32 位 hex）
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"
MIXIN_KEY = get_mixin_key(IMG_KEY, SUB_KEY)

BENCH_DIR = ROOT / "outputs" / "benchmarks"


# =========================================================================
# 离线 mock B 站 API
# =========================================================================

def build_comment_pages(pages: int, per_page: int = 20) -> list[list[dict[str, Any]]]:
    """生成确定性的评论页（含 HTML 实体、零宽字符、空评论、楼中楼计数）。"""

    result: list[list[dict[str, Any]]] = []
    rpid = 1000
    for page in range(pages):
        items: list[dict[str, Any]] = []
        for index in range(per_page):
            rpid += 1
            if index == 3:
                message = "  "  # 空评论：应被标准化过滤
            elif index == 7:
                message = "这期 讲得真好&amp;很有用\u200b！"
            else:
                message = f"第{page + 1}页第{index + 1}条：内容不错，值得一看[doge]"
            items.append(
                {
                    "rpid": rpid,
                    "content": {"message": message},
                    "like": index,
                    "rcount": 2 if index % 5 == 0 else 0,
                    "ctime": 1700000000 + rpid,
                    "member": {"uname": "用户"},
                }
            )
        result.append(items)
    return result


class MockState:
    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        *,
        latency: float = 0.0,
        risk_mode: str = "412",
    ) -> None:
        self.pages = pages
        self.latency = latency
        self.risk_mode = risk_mode
        self.risk_done = False
        self.risk_hits = 0
        self.wbi_failures = 0
        self.requests = 0
        self.bytes_sent = 0
        self.lock = threading.Lock()


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class MockBiliHandler(BaseHTTPRequestHandler):
    state: MockState

    def log_message(self, *args):  # noqa: D401 - 静默
        pass

    def _send(self, payload: Any, status: int = 200, content_type: str = "application/json") -> None:
        body = _json_bytes(payload) if not isinstance(payload, bytes) else payload
        with self.state.lock:
            self.state.requests += 1
            self.state.bytes_sent += len(body)
        if self.state.latency:
            time.sleep(self.state.latency)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}

        if parsed.path == "/robots.txt":
            self._send(b"User-agent: *\nAllow: /\n", content_type="text/plain")
            return
        if parsed.path == "/x/frontend/finger/spi":
            self._send({"code": 0, "data": {"b_3": "MOCK-BUVID3", "b_4": "MOCK-BUVID4"}})
            return
        if parsed.path == "/x/web-interface/nav":
            self._send(
                {
                    "code": -101,
                    "message": "账号未登录",
                    "data": {
                        "isLogin": False,
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                        },
                    },
                }
            )
            return
        if parsed.path == "/x/web-interface/view":
            self._send(
                {
                    "code": 0,
                    "data": {
                        "aid": 123456,
                        "title": "【mock】效率基准视频",
                        "owner": {"name": "基准UP"},
                        "stat": {"reply": sum(len(page) for page in self.state.pages), "view": 1},
                    },
                }
            )
            return
        if parsed.path == "/x/v2/reply/wbi/main":
            # 风控注入：模拟首次请求被 412 / -352 拦截，验证客户端恢复能力
            if self.state.risk_mode != "none" and not self.state.risk_done:
                self.state.risk_done = True
                self.state.risk_hits += 1
                if self.state.risk_mode == "412":
                    self._send(b"Precondition Failed", status=412, content_type="text/plain")
                else:
                    self._send({"code": -352, "message": "风控校验失败"})
                return
            if not self._verify_wbi(query):
                with self.state.lock:
                    self.state.wbi_failures += 1
                self._send({"code": -352, "message": "wbi 签名校验失败"})
                return
            page_index = self._page_index(query.get("pagination_str", ""))
            if page_index >= len(self.state.pages):
                self._send(
                    {"code": 0, "data": {"replies": [], "cursor": {"is_end": True}}}
                )
                return
            replies = self.state.pages[page_index]
            is_end = page_index == len(self.state.pages) - 1
            cursor: dict[str, Any] = {"is_end": is_end}
            if not is_end:
                # 真实接口的 next_offset 是不透明字符串，客户端再包进 pagination_str
                cursor["pagination_reply"] = {"next_offset": f"page:{page_index + 1}"}
            self._send({"code": 0, "data": {"replies": replies, "cursor": cursor}})
            return
        if parsed.path == "/x/v2/reply/reply":
            root = query.get("root", "0")
            replies = [
                {
                    "rpid": int(root) * 10 + index,
                    "root": int(root),
                    "parent": int(root),
                    "content": {"message": f"楼中楼回复 {index}：同意"},
                    "like": 0,
                    "rcount": 0,
                    "ctime": 1700000001,
                }
                for index in (1, 2)
            ]
            self._send(
                {"code": 0, "data": {"replies": replies, "page": {"count": len(replies)}}}
            )
            return
        self._send({"code": -404, "message": "not found"}, status=404)

    @staticmethod
    def _page_index(pagination_str: str) -> int:
        if not pagination_str:
            return 0
        try:
            offset = json.loads(pagination_str).get("offset", "")
        except (ValueError, AttributeError):
            return 0
        if isinstance(offset, str) and offset.startswith("page:"):
            try:
                return int(offset.split(":", 1)[1])
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _verify_wbi(query: dict[str, str]) -> bool:
        w_rid = query.get("w_rid")
        if not w_rid:
            return False
        filtered = {
            key: "".join(char for char in value if char not in "!'()*")
            for key, value in query.items()
            if key != "w_rid"
        }
        ordered = dict(sorted(filtered.items()))
        expected = hashlib.md5(
            (urlencode(ordered, quote_via=quote, safe="") + MIXIN_KEY).encode("utf-8")
        ).hexdigest()
        return expected == w_rid


def start_mock_server(state: MockState):
    handler = type("BoundMockBiliHandler", (MockBiliHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


# =========================================================================
# 吞吐子基准
# =========================================================================

def bench_normalize(records: list[dict[str, Any]], iterations: int = 20000) -> dict[str, Any]:
    """标准化纯函数吞吐（条/秒）。"""

    if not records:
        return {"iterations": 0, "ops_per_second": 0.0}
    started = time.perf_counter()
    for index in range(iterations):
        normalize_text(records[index % len(records)].get("text", ""))
    elapsed = time.perf_counter() - started
    return {
        "iterations": iterations,
        "elapsed_seconds": round(elapsed, 4),
        "ops_per_second": round(iterations / elapsed, 0) if elapsed else 0.0,
    }


def bench_csv_write(records: list[dict[str, Any]], iterations: int = 20000) -> dict[str, Any]:
    """CSV 追加写入吞吐（条/秒）。"""

    if not records:
        return {"rows": 0, "rows_per_second": 0.0}
    from src.crawler.bilibili import CsvStore

    with tempfile.TemporaryDirectory(prefix="bench-csv-") as temp_dir:
        store = CsvStore(Path(temp_dir) / "bench.csv")
        rows = []
        for index in range(iterations):
            row = dict(records[index % len(records)])
            row["rpid"] = str(index + 1)
            rows.append(row)
        started = time.perf_counter()
        store.add(rows)
        elapsed = time.perf_counter() - started
    return {
        "rows": iterations,
        "elapsed_seconds": round(elapsed, 4),
        "rows_per_second": round(iterations / elapsed, 0) if elapsed else 0.0,
    }


def bench_limiter(count: int = 500, min_interval: float = 2.5, jitter: float = 0.3) -> dict[str, Any]:
    """用假时钟模拟限速器，给出实际间隔分布与固定 sleep 的差异。"""

    class SimClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps: list[float] = []

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    clock = SimClock()
    limiter = RateLimiter(min_interval, jitter=jitter, clock=clock, sleep=clock.sleep)
    starts: list[float] = []
    for _ in range(count):
        limiter.wait()
        starts.append(clock.now)
    intervals = [starts[index] - starts[index - 1] for index in range(1, len(starts))]
    return {
        "requests": count,
        "configured_min_interval": min_interval,
        "jitter": jitter,
        "simulated_total_seconds": round(clock.now, 3),
        "fixed_sleep_total_seconds": round(min_interval * (count - 1), 3),
        "interval_min": round(min(intervals), 3) if intervals else 0.0,
        "interval_max": round(max(intervals), 3) if intervals else 0.0,
        "interval_mean": round(statistics.mean(intervals), 3) if intervals else 0.0,
        "unique_intervals": len({round(value, 3) for value in intervals}),
    }


# =========================================================================
# 离线基准
# =========================================================================

def _rss_mb() -> float | None:
    """当前进程常驻内存（MB）；未安装 psutil 时返回 None（不新增硬依赖）。"""

    try:
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except Exception:
        return None


def run_offline(args: argparse.Namespace) -> dict[str, Any]:
    pages = build_comment_pages(args.pages, args.per_page)
    state = MockState(pages, latency=args.latency, risk_mode=args.risk_mode)
    server, thread, base = start_mock_server(state)
    client = BiliClient(
        delay=0.0,
        timeout=5.0,
        jitter_ratio=0.0,
        api_base=base,
        web_base=base,
        respect_robots=True,
        api_robots_policy="warn",
        risk_control_cooldown=0.0,
        sleep=lambda seconds: None,
    )
    rss_before = _rss_mb()
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="bench-bili-") as temp_dir:
            report = crawl_bilibili(
                ["BV1GJ411x7h7"],
                client=client,
                max_pages=args.pages + 2,
                include_replies=args.include_replies,
                max_sub_pages=1,
                max_items=0,
                out_dir=temp_dir,
                write_source_note=False,
            )
            wall = time.perf_counter() - started
            rows = read_csv_records(Path(report.videos[0].csv_path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        client.close()

    metrics = report.metrics
    requests = metrics.get("requests", 0)
    comments = len(rows)
    return {
        "mode": "offline",
        "source": "本地 mock B 站 API（含 WBI 校验 / 分页 / 楼中楼 / 风控恢复）",
        "config": {
            "pages": args.pages,
            "per_page": args.per_page,
            "include_replies": args.include_replies,
            "delay_seconds": 0,
            "risk_mode": args.risk_mode,
            "mock_latency_seconds": args.latency,
        },
        "result": {
            "wall_seconds": round(wall, 3),
            "requests": requests,
            "robots_fetches": metrics.get("robots_fetches", 0),
            "retries": metrics.get("retries", 0),
            "risk_control_hits": metrics.get("risk_control_hits", 0),
            "mock_server_requests": state.requests,
            "mock_risk_hits": state.risk_hits,
            "wbi_failures": state.wbi_failures,
            "response_bytes": metrics.get("response_bytes", 0),
            "comments_new": comments,
            "comments_total": report.total_saved,
            "requests_per_second": round(requests / wall, 2) if wall else 0.0,
            "comments_per_second": round(comments / wall, 2) if wall else 0.0,
            "client_overhead_ms_per_request": round(wall / requests * 1000, 3) if requests else 0.0,
            "latency_ms": metrics.get("latency_ms"),
            "rss_mb_before": rss_before,
            "rss_mb_after": _rss_mb(),
        },
        "normalize": bench_normalize(rows),
        "csv_write": bench_csv_write(rows),
        "limiter": bench_limiter(),
        "sample_records": len(rows),
    }


def _aggregate_offline(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """把多次离线测量聚合为「中位数为主 + 最小/最大」的报告。"""

    if len(runs) == 1:
        payload = dict(runs[0])
        payload["repeat"] = 1
        payload["aggregate"] = None
        return payload

    ordered = sorted(runs, key=lambda run: run["result"]["wall_seconds"])
    median = ordered[len(ordered) // 2]
    payload = dict(median)
    payload["repeat"] = len(runs)
    payload["runs"] = [run["result"] for run in runs]

    def spread(key: str) -> dict[str, float]:
        values = sorted(float(run["result"][key]) for run in runs)
        return {
            "min": round(values[0], 3),
            "median": round(values[len(values) // 2], 3),
            "max": round(values[-1], 3),
        }

    payload["aggregate"] = {
        "runs": len(runs),
        "wall_seconds": spread("wall_seconds"),
        "comments_per_second": spread("comments_per_second"),
        "requests_per_second": spread("requests_per_second"),
        "client_overhead_ms_per_request": spread("client_overhead_ms_per_request"),
    }
    return payload



# =========================================================================
# 真实联网轻量测试
# =========================================================================

def run_live(args: argparse.Namespace) -> dict[str, Any]:
    rss_before = _rss_mb()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bench-live-") as temp_dir:
        report = crawl_bilibili(
            [args.bvid],
            delay=args.delay,
            timeout=args.timeout,
            max_pages=args.max_pages,
            include_replies=args.include_replies,
            max_sub_pages=args.max_sub_pages,
            max_items=args.max_items,
            out_dir=temp_dir,
            write_source_note=False,
        )
        wall = time.perf_counter() - started
        csv_path = Path(report.videos[0].csv_path) if report.videos and report.videos[0].csv_path else None
        rows = read_csv_records(csv_path) if csv_path else []
    metrics = report.metrics
    requests = metrics.get("requests", 0)
    comments = len(rows)
    video = report.videos[0] if report.videos else None
    return {
        "mode": "live",
        "source": f"https://www.bilibili.com/video/{args.bvid}",
        "config": {
            "bvid": args.bvid,
            "max_pages": args.max_pages,
            "include_replies": args.include_replies,
            "max_sub_pages": args.max_sub_pages,
            "max_items": args.max_items,
            "delay_seconds": args.delay,
            "timeout_seconds": args.timeout,
        },
        "result": {
            "wall_seconds": round(wall, 3),
            "requests": requests,
            "robots_fetches": metrics.get("robots_fetches", 0),
            "retries": metrics.get("retries", 0),
            "risk_control_hits": metrics.get("risk_control_hits", 0),
            "response_bytes": metrics.get("response_bytes", 0),
            "comments_new": comments,
            "comments_total": report.total_saved,
            "comments_per_second": round(comments / wall, 2) if wall else 0.0,
            "latency_ms": metrics.get("latency_ms"),
            "limiter_interval": metrics.get("limiter_interval"),
            "limiter_total_wait": metrics.get("limiter_total_wait"),
            "video_title": video.title if video else "",
            "comment_total_on_site": video.comment_total_on_site if video else 0,
            "error": video.error if video else None,
            "rss_mb_before": rss_before,
            "rss_mb_after": _rss_mb(),
        },
        "normalize": bench_normalize(rows),
        "csv_write": bench_csv_write(rows),
        "sample_records": len(rows),
    }


# =========================================================================
# 输出
# =========================================================================

def environment() -> dict[str, Any]:
    return {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def write_report(payload: dict[str, Any], path: Path | None) -> Path:
    target = path or BENCH_DIR / f"crawler_{payload['mode']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def print_markdown(payload: dict[str, Any], json_path: Path) -> None:
    result = payload["result"]
    config = payload["config"]
    print(f"\n## 爬虫效率基准（{payload['mode']}）\n")
    print(f"- 时间（UTC）：`{payload['environment']['time_utc']}`")
    print(f"- 环境：`{payload['environment']['platform']}` / Python {payload['environment']['python']} / CPU {payload['environment']['cpu_count']} 核")
    print(f"- 来源：{payload['source']}")
    print(f"- 配置：`{json.dumps(config, ensure_ascii=False)}`")
    print()
    print("| 指标 | 数值 |")
    print("| --- | --- |")
    labels = [
        ("wall_seconds", "总耗时（s）"),
        ("requests", "业务请求数"),
        ("robots_fetches", "robots.txt 拉取次数"),
        ("retries", "重试次数"),
        ("risk_control_hits", "风控触发/恢复次数"),
        ("response_bytes", "响应字节数"),
        ("comments_new", "采集评论数"),
        ("comments_per_second", "评论吞吐（条/s）"),
        ("requests_per_second", "请求吞吐（次/s）"),
        ("client_overhead_ms_per_request", "单请求平均耗时（ms）"),
    ]
    for key, label in labels:
        if key in result and result[key] is not None:
            print(f"| {label} | {result[key]} |")
    latency = result.get("latency_ms")
    if latency:
        print(f"| 延迟 avg / p50 / p95 / max（ms） | {latency['avg']} / {latency['p50']} / {latency['p95']} / {latency['max']} |")
    for key in ("wbi_failures", "mock_server_requests", "mock_risk_hits", "limiter_interval", "limiter_total_wait"):
        if key in result:
            print(f"| {key} | {result[key]} |")
    print()
    if "normalize" in payload:
        print(f"- 文本标准化吞吐：`{payload['normalize']['ops_per_second']:,} 条/s`（{payload['normalize']['iterations']} 次）")
    if "csv_write" in payload:
        print(f"- CSV 写入吞吐：`{payload['csv_write']['rows_per_second']:,} 行/s`（{payload['csv_write']['rows']} 行）")
    if "limiter" in payload:
        limiter = payload["limiter"]
        print(
            f"- 限速模拟：{limiter['requests']} 次请求，间隔 {limiter['interval_min']}~{limiter['interval_max']}s"
            f"（均值 {limiter['interval_mean']}s，配置 {limiter['configured_min_interval']}s + 抖动 {limiter['jitter']:.0%}），"
            f"不同间隔取值 {limiter['unique_intervals']} 种"
        )
    if payload.get("aggregate"):
        aggregate = payload["aggregate"]
        print(f"\n### 多次测量汇总（{aggregate['runs']} 次）\n")
        print("| 指标 | min | median | max |")
        print("| --- | --- | --- | --- |")
        labels = {
            "wall_seconds": "总耗时（s）",
            "comments_per_second": "评论吞吐（条/s）",
            "requests_per_second": "请求吞吐（次/s）",
            "client_overhead_ms_per_request": "单请求耗时（ms）",
        }
        for key, label in labels.items():
            values = aggregate[key]
            print(f"| {label} | {values['min']} | {values['median']} | {values['max']} |")
    if payload["result"].get("rss_mb_after") is not None:
        print(
            f"\n> 进程 RSS：{payload['result'].get('rss_mb_before')} → "
            f"{payload['result'].get('rss_mb_after')} MB"
        )
    print(f"\n> JSON：`{json_path.as_posix()}`")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="爬虫效率基准（离线 mock + 真实联网轻量测试）")
    sub = parser.add_subparsers(dest="command", required=True)

    offline = sub.add_parser("offline", help="本地 mock API 基准（不访问外网）")
    offline.add_argument("--pages", type=int, default=5, help="mock 一级评论页数")
    offline.add_argument("--per-page", type=int, default=20, help="每页评论数")
    offline.add_argument("--include-replies", action="store_true", help="是否采集楼中楼")
    offline.add_argument("--risk-mode", choices=["none", "412", "-352"], default="412",
                         help="风控注入方式（验证恢复能力）")
    offline.add_argument("--latency", type=float, default=0.0, help="mock 服务每请求人为延迟（秒）")
    offline.add_argument("--repeat", type=int, default=3, help="重复测量次数（默认 3，报告中位数）")
    offline.add_argument("--json-out", default=None, help="JSON 输出路径")

    live = sub.add_parser("live", help="真实联网轻量测试（请保持保守频率）")
    live.add_argument("--bvid", required=True, help="视频 BV 号")
    live.add_argument("--max-pages", type=int, default=1, help="最多采集页数")
    live.add_argument("--max-sub-pages", type=int, default=1, help="每条根评论最多楼中楼页数")
    live.add_argument("--max-items", type=int, default=40, help="最多新增条数")
    live.add_argument("--include-replies", action="store_true", help="是否采集楼中楼")
    live.add_argument("--delay", type=float, default=2.5, help="请求最小间隔（秒），不得小于 2")
    live.add_argument("--timeout", type=float, default=15.0, help="单请求超时（秒）")
    live.add_argument("--json-out", default=None, help="JSON 输出路径")

    args = parser.parse_args(argv)
    if args.command == "live" and args.delay < 2:
        parser.error("--delay 不能小于 2 秒（限速是硬性要求）")
    return args


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    args = parse_args(argv)
    if args.command == "offline":
        if args.repeat < 1:
            args.repeat = 1
        runs = [run_offline(args) for _ in range(args.repeat)]
        payload = _aggregate_offline(runs)
    else:
        payload = run_live(args)
    payload["environment"] = environment()
    json_path = write_report(payload, Path(args.json_out) if args.json_out else None)
    print_markdown(payload, json_path)
    if payload["mode"] == "live" and payload["result"].get("error"):
        print(f"\n采集停止原因：{payload['result']['error']}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
