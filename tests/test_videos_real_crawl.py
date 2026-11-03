"""真实爬虫接入（opt-in）的**离线**测试：`/api/videos` 采集评论阶段接入真实爬虫。

覆盖：

- 默认 ``real_crawl=False``：不调用爬虫、输出仍是占位数据（与既有行为一致）；
- ``real_crawl=True`` + 注入假采集器 / monkeypatch ``src.crawler.fetch_video_comments``：
  任务完成、``comment_count`` / ``comment_total`` 等于真实条数、detail 使用真实评论文本，
  顶层 ``placeholder=False``（评论数据真实；sentiment 仍是规则占位预测）；
- 采集失败 / 空结果：任务写成 ``failed`` 且 ``error`` 可读，不会卡在 ``crawling``；
- 适配层环境变量解析（非法值回退默认）与记录标准化；
- ``placeholder.build_detail`` 真实评论分支的结构 / 契约自洽。

所有用例都不联网：真实采集函数一律用假函数替换。
"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

import src.crawler
from src.inference.app import app
from src.videos import crawler_adapter, pipeline, placeholder
from src.videos import service as videos_service
from src.videos import settings as video_settings
from src.videos.config import PipelineConfig
from src.videos.errors import PipelineFailure
from src.videos.service import VideoService
from src.videos.store import VideoStore, now_iso

BVID = "BV1xx411c7mD"
BVID_SECOND = "BV1Ab411c7mE"

#: 0 秒配置：状态机瞬间跑完
INSTANT = PipelineConfig(0.0, 0.0, 0.0, 0.0)
#: 真实采集 + 0 秒配置（采集本身由假函数瞬间返回，不联网、不等待限速）
REAL_INSTANT = PipelineConfig(0.0, 0.0, 0.0, 0.0, real_crawl=True)

#: 详情顶层字段（与 `docs/API契约.md` 第三节 + placeholder 标注 + pipeline 一致）
DETAIL_FIELDS = {
    "video",
    "analysis",
    "sentiment",
    "trend",
    "keywords",
    "model",
    "top_comments",
    "error",
    "placeholder",
    "pipeline",
}

#: `placeholder.build_detail` 的原始产物字段（pipeline 由 service 层组装，不在 payload 里）
DETAIL_PAYLOAD_FIELDS = DETAIL_FIELDS - {"pipeline"}


def _fake_records() -> list[dict]:
    """模拟 ``fetch_video_comments`` 的 CSV 记录（字符串字段，贴近真实返回值）。"""
    return [
        {
            "bvid": BVID,
            "aid": "170001",
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1001",
            "level": "1",
            "root_rpid": "",
            "parent_rpid": "",
            "text": "这个视频讲得太清楚了，非常好用，强烈推荐！",
            "likes": "120",
            "reply_count": "2",
            "ctime_utc": "2026-10-15T08:00:00+00:00",
            "collected_at_utc": "2026-10-17T00:00:00+00:00",
        },
        {
            "bvid": BVID,
            "aid": "170001",
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1002",
            "level": "1",
            "root_rpid": "",
            "parent_rpid": "",
            "text": "节奏把握得很好，干货密度高，感谢UP主。",
            "likes": "300",
            "reply_count": "0",
            "ctime_utc": "2026-10-15T09:30:00+00:00",
            "collected_at_utc": "2026-10-17T00:00:00+00:00",
        },
        {
            "bvid": BVID,
            "aid": "170001",
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1003",
            "level": "1",
            "root_rpid": "",
            "parent_rpid": "",
            "text": "广告太长了，画质差，直接劝退。",
            "likes": "88",
            "reply_count": "1",
            "ctime_utc": "2026-10-16T08:00:00+00:00",
            "collected_at_utc": "2026-10-17T00:00:00+00:00",
        },
        {
            "bvid": BVID,
            "aid": "170001",
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1004",
            "level": "1",
            "root_rpid": "",
            "parent_rpid": "",
            "text": "更新太慢了，等得都忘了前面讲什么。",
            "likes": "42",
            "reply_count": "0",
            "ctime_utc": "2026-10-16T10:00:00+00:00",
            "collected_at_utc": "2026-10-17T00:00:00+00:00",
        },
        {
            # ctime 缺失：不参与趋势分桶，但不能让详情构建失败
            "bvid": BVID,
            "aid": "170001",
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1005",
            "level": "1",
            "root_rpid": "",
            "parent_rpid": "",
            "text": "标题和内容不符，纯标题党浪费我时间。",
            "likes": "5",
            "reply_count": "0",
            "ctime_utc": "",
            "collected_at_utc": "2026-10-17T00:00:00+00:00",
        },
    ]


def _seed_task(store: VideoStore, bvid: str = BVID) -> dict:
    """往存储里塞一条 queued 任务（模拟提交后的状态，run_id = 1）。"""
    task = {
        "bvid": bvid,
        "title": None,
        "owner": None,
        "cover_url": None,
        "status": "queued",
        "progress": 0,
        "message": "排队中…",
        "phases": pipeline.initial_phases(),
        "comment_count": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "error": None,
        "run_id": 1,
        "seq": 1,
    }
    store.upsert_task(task)
    return task


def _all_comment_texts(detail: dict) -> list[str]:
    return [
        item["text"]
        for side in ("positive", "negative")
        for item in detail["top_comments"][side]
    ]


def _wait_for_status(client: TestClient, bvid: str, statuses, timeout: float = 15.0) -> dict:
    """轮询详情直到 `analysis.status` 落在 `statuses` 内，返回详情响应体。"""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/videos/{bvid}/detail")
        assert response.status_code == 200
        body = response.json()
        last = body["analysis"]
        if last["status"] in statuses:
            return body
        time.sleep(0.01)
    raise AssertionError(f"等待 {bvid} 进入 {statuses} 超时，最后状态：{last}")


# --------------------------------------------------------------------------- #
# 1. 默认关闭：不触网、仍是占位数据
# --------------------------------------------------------------------------- #
def test_default_pipeline_does_not_call_real_crawler(tmp_path, monkeypatch):
    """real_crawl=False（默认）时必须完全不碰爬虫，输出仍是占位 detail。"""

    def unexpected(*args, **kwargs):  # pragma: no cover - 被调用即测试失败
        raise AssertionError("默认配置不应调用真实爬虫")

    monkeypatch.setattr(src.crawler, "fetch_video_comments", unexpected)

    store = VideoStore(tmp_path / "videos")
    _seed_task(store)
    detail = pipeline.run_pipeline(store, BVID, INSTANT)

    assert detail["placeholder"] is True
    assert detail["video"]["comment_total"] == placeholder.planned_comment_total(BVID)
    assert store.get_task(BVID)["comment_count"] == detail["video"]["comment_total"]
    # 代表性评论来自占位池（不是真实爬虫记录）
    placeholder_texts = {
        text for text, _like, _confidence in (*placeholder.POSITIVE_COMMENT_POOL, *placeholder.NEGATIVE_COMMENT_POOL)
    }
    assert set(_all_comment_texts(detail)) <= placeholder_texts


# --------------------------------------------------------------------------- #
# 2. 开启真实采集：真实条数 / 真实评论进入 detail
# --------------------------------------------------------------------------- #
def test_real_crawl_pipeline_uses_injected_records(tmp_path):
    records = _fake_records()
    calls: list[tuple[str, crawler_adapter.VideoCrawlConfig]] = []

    def fake_collector(bvid: str, crawl_config: crawler_adapter.VideoCrawlConfig):
        calls.append((bvid, crawl_config))
        return records

    store = VideoStore(tmp_path / "videos")
    _seed_task(store)
    detail = pipeline.run_pipeline(store, BVID, REAL_INSTANT, collector=fake_collector)

    assert [call[0] for call in calls] == [BVID]
    # 采集配置来自 PipelineConfig 的默认安全值
    assert calls[0][1].real_crawl is True
    assert calls[0][1].max_pages == 1

    task = store.get_task(BVID)
    assert task["status"] == "completed"
    assert task["progress"] == 100
    assert task["error"] is None
    assert task["comment_count"] == len(records) == 5
    assert all(phase["status"] == "done" for phase in task["phases"])

    # 真实条数 / 真实标题 / 真实评论进入 detail
    assert detail["video"]["comment_total"] == len(records)
    assert detail["video"]["title"] == "真实视频标题（爬虫返回）"
    assert detail["video"]["owner"] is None  # 爬虫 CSV 不含 UP 主 → 诚实置 None
    assert detail["placeholder"] is False  # 评论数据真实
    assert set(detail) == DETAIL_PAYLOAD_FIELDS

    # sentiment 只由规则占位预测聚合（计数自洽，无中性）
    sentiment = detail["sentiment"]
    assert sentiment["total"] == len(records)
    assert sentiment["positive"] + sentiment["negative"] + sentiment["neutral"] == len(records)
    assert sentiment["positive"] == 2 and sentiment["negative"] == 3
    assert sentiment["neutral"] == 0

    # top_comments：真实文本、按点赞降序
    positive_comments = detail["top_comments"]["positive"]
    negative_comments = detail["top_comments"]["negative"]
    assert [item["text"] for item in positive_comments] == [
        "节奏把握得很好，干货密度高，感谢UP主。",
        "这个视频讲得太清楚了，非常好用，强烈推荐！",
    ]
    assert [item["like"] for item in positive_comments] == [300, 120]
    assert [item["like"] for item in negative_comments] == [88, 42, 5]
    assert all(item["label"] == "positive" for item in positive_comments)
    assert all(item["label"] == "negative" for item in negative_comments)
    for item in (*positive_comments, *negative_comments):
        assert set(item) == {"text", "like", "confidence", "label"}
        assert 0.0 <= item["confidence"] <= 1.0

    # trend：按真实 ctime_utc 按天分桶（缺时间的记录被跳过）
    assert detail["trend"] == [
        {"bucket": "2026-10-15", "positive": 2, "negative": 0, "neutral": 0},
        {"bucket": "2026-10-16", "positive": 0, "negative": 2, "neutral": 0},
    ]

    # keywords：来自真实文本分词，每侧非空且词确实出现在对应评论文本里
    for side in ("positive", "negative"):
        items = detail["keywords"][side]
        assert items, f"keywords.{side} 不应为空"
        joined = " ".join(
            _fake_text
            for _fake_text, label in _labeled_texts(records)
            if label == side
        ).casefold()
        for item in items:
            assert set(item) == {"word", "count", "weight"}
            assert item["count"] >= 1
            assert 0.0 <= item["weight"] <= 1.0
            assert item["word"] in joined

    # length_stats：真实长度统计
    lengths = [len(record["text"]) for record in records]
    assert detail["analysis"]["length_stats"]["max_chars"] == max(lengths)
    assert detail["analysis"]["length_stats"]["avg_chars"] == pytest.approx(
        sum(lengths) / len(lengths), abs=0.05
    )


def _labeled_texts(records: list[dict]) -> list[tuple[str, str]]:
    """用规则预测标注每条记录的极性（只为断言 keywords 的分组口径）。"""
    from src.inference.service import predict_rule_based

    return [
        (str(record["text"]), predict_rule_based(str(record["text"]))["label"])
        for record in records
    ]


def test_real_crawl_via_monkeypatched_crawler(tmp_path, monkeypatch):
    """monkeypatch `src.crawler.fetch_video_comments` 即走真实适配器（全离线）。"""
    records = _fake_records()
    captured: dict = {}

    def fake_fetch(bvid, **kwargs):
        captured["bvid"] = bvid
        captured["kwargs"] = kwargs
        return records

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)

    config = PipelineConfig(
        0.0,
        0.0,
        0.0,
        0.0,
        real_crawl=True,
        crawl_max_pages=2,
        crawl_max_items=5,
        crawl_delay_seconds=1.0,
        crawl_include_replies=True,
        crawl_timeout=3.0,
    )
    store = VideoStore(tmp_path / "videos")
    _seed_task(store)
    detail = pipeline.run_pipeline(store, BVID, config)

    assert captured["bvid"] == BVID
    assert callable(captured["kwargs"].pop("on_page"))
    assert captured["kwargs"] == {
        "max_pages": 2,
        "include_replies": True,
        "max_sub_pages": crawler_adapter.DEFAULT_MAX_SUB_PAGES,
        "max_items": 5,
        "delay": 1.0,
        "timeout": 3.0,
        "page_size": crawler_adapter.DEFAULT_PAGE_SIZE,
        "mode": 2,
        "cookie": None,
        "init_buvid": False,
        "require_login": False,
    }
    assert store.get_task(BVID)["status"] == "completed"
    assert store.get_task(BVID)["comment_count"] == len(records)
    assert detail["placeholder"] is False
    assert detail["video"]["comment_total"] == len(records)


# --------------------------------------------------------------------------- #
# 3. 失败：任务必须 failed 且 error 可读，不能卡在 crawling
# --------------------------------------------------------------------------- #
def test_real_crawl_failure_marks_task_failed(tmp_path):
    def boom(bvid, crawl_config):
        raise RuntimeError("网络超时（模拟）")

    store = VideoStore(tmp_path / "videos")
    _seed_task(store)

    with pytest.raises(PipelineFailure) as excinfo:
        pipeline.run_pipeline(store, BVID, REAL_INSTANT, collector=boom)

    task = store.get_task(BVID)
    assert task is not None
    assert task["status"] == "failed"
    assert task["status"] != "crawling"
    assert task["error"] and "网络超时" in task["error"]
    assert task["message"].startswith("分析失败")
    assert task["phases"][0] == {"name": "采集评论", "status": "failed"}
    assert task["progress"] == 0
    assert "网络超时" in str(excinfo.value)


def test_real_crawl_adapter_error_keeps_readable_reason(tmp_path, monkeypatch):
    """适配层包装底层异常：任务 error 里保留原始类型与信息。"""

    def broken_fetch(bvid, **kwargs):
        raise src.crawler.CrawlStopped("评论区已关闭或视频不存在")

    monkeypatch.setattr(src.crawler, "fetch_video_comments", broken_fetch)

    store = VideoStore(tmp_path / "videos")
    _seed_task(store)
    with pytest.raises(PipelineFailure):
        pipeline.run_pipeline(store, BVID, REAL_INSTANT)

    task = store.get_task(BVID)
    assert task["status"] == "failed"
    assert "CrawlStopped" in task["error"]
    assert "评论区已关闭或视频不存在" in task["error"]


def test_real_crawl_empty_result_marks_failed(tmp_path):
    store = VideoStore(tmp_path / "videos")
    _seed_task(store)
    with pytest.raises(PipelineFailure):
        pipeline.run_pipeline(store, BVID, REAL_INSTANT, collector=lambda bvid, cfg: [])

    task = store.get_task(BVID)
    assert task["status"] == "failed"
    assert "没有返回任何评论" in task["error"]


def test_service_real_crawl_failure_is_not_stuck_in_crawling(tmp_path, monkeypatch):
    """后台服务线程版本：采集失败后任务落在 failed（不会被挂死在 crawling）。"""

    def boom(bvid, **kwargs):
        raise RuntimeError("连接被重置（模拟）")

    monkeypatch.setattr(src.crawler, "fetch_video_comments", boom)
    service = VideoService(root=tmp_path / "videos", config=REAL_INSTANT)
    try:
        service.start()
        service.submit(BVID)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            task = service.store.get_task(BVID)
            if task and task["status"] in {"failed", "completed"}:
                break
            time.sleep(0.01)
        task = service.store.get_task(BVID)
        assert task is not None and task["status"] == "failed"
        assert task["error"] and "连接被重置" in task["error"]
        assert service.detail(BVID)["analysis"]["status"] == "failed"
    finally:
        service.shutdown()


# --------------------------------------------------------------------------- #
# 4. HTTP 契约：真实采集完成后 placeholder=false 且结构不被裁剪
# --------------------------------------------------------------------------- #
def test_api_detail_reflects_real_crawl(tmp_path, monkeypatch):
    records = _fake_records()

    def fake_fetch(bvid, **kwargs):
        return records

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)

    service = VideoService(root=tmp_path / "videos", config=REAL_INSTANT)
    videos_service.set_service(service)
    try:
        with TestClient(app) as client:
            assert client.post("/api/videos", json={"bvid": BVID}).status_code == 200
            body = _wait_for_status(client, BVID, {"completed"})

            assert set(body) == DETAIL_FIELDS
            assert body["placeholder"] is False
            assert body["video"]["comment_total"] == len(records)
            assert body["sentiment"]["total"] == len(records)
            real_texts = {record["text"] for record in records}
            assert set(_all_comment_texts(body)) <= real_texts
            assert body["error"] is None
    finally:
        videos_service.set_service(None)


# --------------------------------------------------------------------------- #
# 5. 适配层：env 解析、记录标准化、异常包装
# --------------------------------------------------------------------------- #
def test_crawl_config_defaults_are_safe():
    config = crawler_adapter.VideoCrawlConfig()
    assert config.real_crawl is False
    assert config.max_pages == 1
    assert config.include_replies is False
    assert config.max_sub_pages == 1
    assert config.max_items == 200
    assert config.delay_seconds == pytest.approx(2.5)
    assert config.timeout == pytest.approx(15.0)
    # 新增的逐任务参数（spec 4.2 / 4.3）
    assert config.page_size == 20
    assert config.sort_mode == "time" and config.crawl_mode == 2
    assert config.init_buvid is False
    assert config.require_login is False
    assert config.cookie is None
    # Cookie 不参与 repr（防止日志 / 任务索引泄密）
    assert "SESSDATA" not in repr(crawler_adapter.VideoCrawlConfig(cookie="SESSDATA=secret123"))
    assert crawler_adapter.VideoCrawlConfig(sort_mode="hot").crawl_mode == 3


def test_crawl_config_from_env_and_invalid_fallback(monkeypatch):
    for name in (
        crawler_adapter.ENV_REAL_CRAWL,
        crawler_adapter.ENV_CRAWL_MAX_PAGES,
        crawler_adapter.ENV_CRAWL_MAX_ITEMS,
        crawler_adapter.ENV_CRAWL_DELAY,
        crawler_adapter.ENV_CRAWL_INCLUDE_REPLIES,
        crawler_adapter.ENV_CRAWL_TIMEOUT,
    ):
        monkeypatch.delenv(name, raising=False)

    # 未设置 → 默认值
    defaults = crawler_adapter.VideoCrawlConfig.from_env()
    assert defaults == crawler_adapter.VideoCrawlConfig()

    # 合法值 → 按 env 生效
    monkeypatch.setenv(crawler_adapter.ENV_REAL_CRAWL, "1")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_PAGES, "3")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_ITEMS, "50")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_DELAY, "1.5")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_INCLUDE_REPLIES, "true")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_TIMEOUT, "9")
    config = crawler_adapter.VideoCrawlConfig.from_env()
    assert config.real_crawl is True
    assert config.max_pages == 3
    assert config.max_items == 50
    assert config.delay_seconds == pytest.approx(1.5)
    assert config.include_replies is True
    assert config.timeout == pytest.approx(9.0)

    # 非法值 → 逐项回退默认，不抛异常
    monkeypatch.setenv(crawler_adapter.ENV_REAL_CRAWL, "不是布尔值")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_PAGES, "0")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_ITEMS, "-5")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_DELAY, "abc")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_INCLUDE_REPLIES, "??")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_TIMEOUT, "0")
    fallback = crawler_adapter.VideoCrawlConfig.from_env()
    assert fallback == crawler_adapter.VideoCrawlConfig()


def test_pipeline_config_from_env_wires_real_crawl(monkeypatch):
    monkeypatch.delenv(crawler_adapter.ENV_REAL_CRAWL, raising=False)
    assert PipelineConfig().real_crawl is False
    assert PipelineConfig().total_seconds == pytest.approx(30.0)

    monkeypatch.setenv(crawler_adapter.ENV_REAL_CRAWL, "1")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_PAGES, "2")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_MAX_ITEMS, "30")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_DELAY, "2.5")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_INCLUDE_REPLIES, "1")
    monkeypatch.setenv(crawler_adapter.ENV_CRAWL_TIMEOUT, "20")
    config = PipelineConfig.from_env()
    assert config.real_crawl is True
    assert config.crawl_max_pages == 2
    assert config.crawl_max_items == 30
    assert config.crawl_delay_seconds == pytest.approx(2.5)
    assert config.crawl_include_replies is True
    assert config.crawl_timeout == pytest.approx(20.0)
    assert config.crawl_config().max_pages == 2


def test_adapter_normalizes_csv_records():
    records = crawler_adapter.collect_video_comments(
        BVID, fetcher=lambda bvid, **kwargs: _fake_records()
    )
    assert len(records) == 5
    first = records[0]
    assert set(first) == set(crawler_adapter.STANDARD_FIELDS)
    assert first["text"] == "这个视频讲得太清楚了，非常好用，强烈推荐！"
    assert first["likes"] == 120 and isinstance(first["likes"], int)
    assert first["level"] == 1 and isinstance(first["level"], int)
    assert first["reply_count"] == 2
    assert first["video_title"] == "真实视频标题（爬虫返回）"
    assert first["ctime_utc"] == "2026-10-15T08:00:00+00:00"


def test_adapter_wraps_errors_and_rejects_bad_results():
    def boom(bvid, **kwargs):
        raise RuntimeError("接口 412 风控")

    with pytest.raises(crawler_adapter.CrawlAdapterError) as excinfo:
        crawler_adapter.collect_video_comments(BVID, fetcher=boom)
    assert "接口 412 风控" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)

    with pytest.raises(crawler_adapter.CrawlAdapterError):
        crawler_adapter.collect_video_comments(BVID, fetcher=lambda bvid, **kwargs: None)
    with pytest.raises(crawler_adapter.CrawlAdapterError):
        crawler_adapter.collect_video_comments(BVID, fetcher=lambda bvid, **kwargs: "not-a-list")
    with pytest.raises(crawler_adapter.CrawlAdapterError):
        crawler_adapter.collect_video_comments(BVID, fetcher=lambda bvid, **kwargs: [123])
    with pytest.raises(crawler_adapter.CrawlAdapterError):
        crawler_adapter.collect_video_comments("not-a-bvid")

    # 正文为空的记录被跳过（爬虫本身也会过滤；适配层再兜底一次）
    records = crawler_adapter.collect_video_comments(
        BVID, fetcher=lambda bvid, **kwargs: [{"text": "  "}, {"text": "有用", "likes": "3"}]
    )
    assert [record["text"] for record in records] == ["有用"]


# --------------------------------------------------------------------------- #
# 6. 账号 Cookie 被内容接口限制（风控 / 空页）：单次匿名回退
# --------------------------------------------------------------------------- #
#: 与真实现场一致的底层异常文案（Lead 用真实请求复现：登录态有效但评论接口空页）
RISK_CONTROL_MESSAGE = "当前页没有评论，但未收到结束标记（可能被风控）"
RAW_COOKIE = "SESSDATA=secret-sessdata-value; bili_jct=secret-csrf-token; DedeUserID=42"
SESSDATA_VALUE = "secret-sessdata-value"

#: 匿名回退时必须原样透传的内容参数（cookie / require_login 单独断言）
_PASSTHROUGH_KEYS = (
    "max_pages",
    "include_replies",
    "max_sub_pages",
    "max_items",
    "delay",
    "timeout",
    "page_size",
    "mode",
    "init_buvid",
)


def test_cookie_restricted_falls_back_to_anonymous_and_warns(caplog):
    """首次带 Cookie 因风控失败 → 第二次 cookie=None 采集成功，并有 warning。"""
    records = _fake_records()
    calls: list[dict] = []

    def fetcher(bvid, **kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise src.crawler.CrawlStopped(RISK_CONTROL_MESSAGE)
        return records

    config = crawler_adapter.VideoCrawlConfig(
        cookie=RAW_COOKIE,
        max_pages=2,
        page_size=30,
        sort_mode="hot",
        require_login=True,
    )
    with caplog.at_level(logging.WARNING):
        result = crawler_adapter.collect_video_comments(BVID, config, fetcher=fetcher)

    assert len(calls) == 2
    assert calls[0]["cookie"] == RAW_COOKIE
    assert calls[0]["require_login"] is True
    # 回退只摘掉 Cookie；require_login 也必须关掉——匿名会话不可能通过登录态检查
    assert calls[1]["cookie"] is None
    assert calls[1]["require_login"] is False
    # 内容参数原样透传（页数 / 页大小 / 排序 / 条数上限 / 限速 / 超时 ……）
    assert {key: calls[1][key] for key in _PASSTHROUGH_KEYS} == {
        key: calls[0][key] for key in _PASSTHROUGH_KEYS
    }
    assert len(result) == len(records)

    warnings = [item for item in caplog.records if item.levelno == logging.WARNING]
    assert any("回退匿名采集" in item.getMessage() for item in warnings)
    # 日志绝不出现 Cookie 明文
    assert SESSDATA_VALUE not in caplog.text
    assert "secret-csrf-token" not in caplog.text


def test_fallback_failure_returns_actionable_error_and_redacts_cookie(caplog):
    """两次都失败 → CrawlAdapterError 带可执行建议，且错误 / 日志不含 Cookie。"""
    cookie_seen: list[object] = []

    def fetcher(bvid, **kwargs):
        cookie_seen.append(kwargs["cookie"])
        if kwargs["cookie"]:
            raise RuntimeError(f"接口 412 风控：请求头包含 {kwargs['cookie']}")
        raise RuntimeError("匿名接口返回格式异常")

    config = crawler_adapter.VideoCrawlConfig(cookie=RAW_COOKIE, require_login=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(crawler_adapter.CrawlAdapterError) as excinfo:
            crawler_adapter.collect_video_comments(BVID, config, fetcher=fetcher)

    message = str(excinfo.value)
    assert cookie_seen == [RAW_COOKIE, None]
    # 可执行建议：重新登录 / 关闭「必须登录」改用匿名 / 稍后重试
    assert "重新登录" in message
    assert "匿名" in message
    assert "稍后重试" in message
    # 两次原因都保留（首次风控 + 匿名回退失败）
    assert "风控" in message
    assert "匿名接口返回格式异常" in message
    assert isinstance(excinfo.value.__cause__, crawler_adapter.CrawlAdapterError)
    # Cookie 明文绝不出现在错误 / warning 中
    assert SESSDATA_VALUE not in message
    assert SESSDATA_VALUE not in caplog.text
    assert "secret-csrf-token" not in caplog.text


def test_risk_control_adapter_error_also_falls_back():
    """fetcher 直接抛 CrawlAdapterError（已包装）且带风控特征时同样回退。"""
    cookies: list[object] = []

    def fetcher(bvid, **kwargs):
        cookies.append(kwargs["cookie"])
        if len(cookies) == 1:
            raise crawler_adapter.CrawlAdapterError("账号接口 412 风控，已停止采集")
        return _fake_records()

    result = crawler_adapter.collect_video_comments(
        BVID, crawler_adapter.VideoCrawlConfig(cookie=RAW_COOKIE), fetcher=fetcher
    )
    assert cookies == [RAW_COOKIE, None]
    assert len(result) == len(_fake_records())


def test_no_cookie_never_falls_back():
    """无 Cookie（含空串归一成 None）→ 只调用一次，行为与之前完全一致。"""
    calls: list[object] = []

    def fetcher(bvid, **kwargs):
        calls.append(kwargs["cookie"])
        raise src.crawler.CrawlStopped(RISK_CONTROL_MESSAGE)

    assert crawler_adapter.VideoCrawlConfig(cookie="   ").cookie is None
    for config in (crawler_adapter.VideoCrawlConfig(), crawler_adapter.VideoCrawlConfig(cookie="  ")):
        calls.clear()
        with pytest.raises(crawler_adapter.CrawlAdapterError) as excinfo:
            crawler_adapter.collect_video_comments(BVID, config, fetcher=fetcher)
        assert calls == [None]
        # 单次失败的原始原因仍可读
        assert "当前页没有评论" in str(excinfo.value)


def test_non_risk_control_failure_does_not_fall_back():
    """带 Cookie 但异常不是风控 / 空页（如解析失败）→ 不回退，原样包装抛出。"""
    calls: list[object] = []

    def fetcher(bvid, **kwargs):
        calls.append(kwargs["cookie"])
        raise RuntimeError("解析失败：CSV 表头不兼容")

    with pytest.raises(crawler_adapter.CrawlAdapterError) as excinfo:
        crawler_adapter.collect_video_comments(
            BVID, crawler_adapter.VideoCrawlConfig(cookie=RAW_COOKIE), fetcher=fetcher
        )
    assert calls == [RAW_COOKIE]  # 没有第二次请求
    assert "解析失败：CSV 表头不兼容" in str(excinfo.value)


def _seed_staged_task(store: VideoStore, snapshot: dict) -> dict:
    """往存储里塞一条带设置快照的分步任务（模拟 service.submit 的落盘结果）。"""
    task = {
        "bvid": BVID,
        "title": None,
        "owner": None,
        "cover_url": None,
        "status": "queued",
        "progress": 0,
        "message": "排队中…",
        "phases": pipeline.initial_phases(),
        "comment_count": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "error": None,
        "run_id": 1,
        "seq": 1,
        "settings_snapshot": snapshot,
        "resume_stage": "crawl",
        "stage_state": pipeline.initial_stage_state(),
    }
    store.upsert_task(task)
    return task


def test_staged_crawl_fallback_success_reaches_crawled(tmp_path, monkeypatch):
    """Web 分步路径（账号模式）：Cookie 被限制 → 匿名回退成功 → 任务采集完成。"""
    video_settings.save_account_cookie(RAW_COOKIE)
    snapshot = video_settings.snapshot_for_task(
        video_settings.merge_for_task({"crawl": {"real_crawl": True}})
    )
    store = VideoStore(tmp_path / "videos")
    _seed_staged_task(store, snapshot)

    cookies: list[object] = []
    records = _fake_records()

    def fake_fetch(bvid, **kwargs):
        cookies.append(kwargs["cookie"])
        if len(cookies) == 1:
            raise src.crawler.CrawlStopped(RISK_CONTROL_MESSAGE)
        return records

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)
    result = pipeline.run_pipeline(store, BVID, REAL_INSTANT, task_settings=snapshot)

    assert result is None  # 停在 crawled 等待继续清洗
    task = store.get_task(BVID)
    assert task["status"] == "crawled"
    assert task["error"] is None
    assert task["comment_count"] == len(records)
    assert cookies == [RAW_COOKIE, None]
    index_text = store.index_path.read_text(encoding="utf-8")
    assert SESSDATA_VALUE not in index_text
    assert "secret-csrf-token" not in index_text


def test_staged_crawl_fallback_failure_shows_actionable_error(tmp_path, monkeypatch):
    """Web 分步路径：两次都失败 → 任务 failed，error 给用户可执行建议且不泄露 Cookie。"""
    video_settings.save_account_cookie(RAW_COOKIE)
    snapshot = video_settings.snapshot_for_task(
        video_settings.merge_for_task({"crawl": {"real_crawl": True}})
    )
    store = VideoStore(tmp_path / "videos")
    _seed_staged_task(store, snapshot)

    def fake_fetch(bvid, **kwargs):
        raise RuntimeError(f"接口 412 风控：请求头包含 {kwargs['cookie']}")

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)
    with pytest.raises(PipelineFailure):
        pipeline.run_pipeline(store, BVID, REAL_INSTANT, task_settings=snapshot)

    task = store.get_task(BVID)
    assert task["status"] == "failed"
    assert "重新登录" in task["error"]
    assert "匿名" in task["error"]
    assert SESSDATA_VALUE not in task["error"]
    detail = store.read_detail(BVID)
    assert detail is None or SESSDATA_VALUE not in str(detail)
    assert SESSDATA_VALUE not in store.index_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 7. placeholder.build_detail 真实评论分支的结构 / 契约自洽
# --------------------------------------------------------------------------- #
def test_build_detail_with_real_comments_is_contract_complete():
    from datetime import datetime, timedelta, timezone

    fixed = datetime(2026, 10, 17, 14, 30, 0, tzinfo=timezone(timedelta(hours=8)))
    records = crawler_adapter.collect_video_comments(
        BVID, fetcher=lambda bvid, **kwargs: _fake_records()
    )
    detail = placeholder.build_detail(BVID, finished_at=fixed, comments=records)

    assert set(detail) == DETAIL_PAYLOAD_FIELDS
    assert detail["placeholder"] is False
    assert detail["error"] is None
    assert detail["analysis"]["status"] == "completed"
    assert detail["analysis"]["progress"] == 100
    assert detail["video"]["bvid"] == BVID
    assert detail["video"]["comment_total"] == len(records)
    assert detail["video"]["pubdate"] is None  # 未知就置 None，不伪造
    assert detail["sentiment"]["total"] == len(records)
    assert detail["sentiment"]["positive"] + detail["sentiment"]["negative"] + detail["sentiment"]["neutral"] == len(records)
    assert sum(
        detail["sentiment"][key]
        for key in ("positive_ratio", "negative_ratio", "neutral_ratio")
    ) == pytest.approx(1.0, abs=0.01)
    assert detail["trend"] and all(
        set(point) == {"bucket", "positive", "negative", "neutral"} for point in detail["trend"]
    )
    assert detail["keywords"]["positive"] and detail["keywords"]["negative"]
    for side in ("positive", "negative"):
        assert len(detail["top_comments"][side]) <= 10
        likes = [item["like"] for item in detail["top_comments"][side]]
        assert likes == sorted(likes, reverse=True)
    assert set(detail["model"]) == {"name", "accuracy", "precision", "recall", "f1"}

    # comments=None / 空列表：必须与既有占位输出逐字段一致
    baseline = placeholder.build_detail(BVID, finished_at=fixed)
    assert placeholder.build_detail(BVID, finished_at=fixed, comments=None) == baseline
    assert placeholder.build_detail(BVID, finished_at=fixed, comments=[]) == baseline
    assert baseline["placeholder"] is True


def test_real_keywords_ignore_urls_mentions_and_emoticons():
    """真实评论的关键词不应出现 URL/域名/@提及/[表情] 噪声。"""

    records = crawler_adapter.collect_video_comments(
        BVID,
        fetcher=lambda bvid, **kwargs: [
            {
                "bvid": bvid,
                "text": "这个教程讲得真好 https://www.bilibili.com/video/BV1GJ411x7h7 "
                        "@某UP主 [笑哭] 学会了",
                "likes": "5",
                "reply_count": "0",
                "level": "1",
                "ctime_utc": "2026-10-15T10:00:00+00:00",
            },
            {
                "bvid": bvid,
                "text": "讲解清晰，很有帮助 https://example.com/a?b=1",
                "likes": "2",
                "reply_count": "0",
                "level": "1",
                "ctime_utc": "2026-10-16T10:00:00+00:00",
            },
        ],
    )
    detail = placeholder.build_detail(BVID, comments=records)
    words = [
        item["word"]
        for side in ("positive", "negative")
        for item in detail["keywords"][side]
    ]
    assert words, "真实评论应能产出关键词"
    forbidden = {"https", "http", "www", "com", "bilibili", "share", "music", "video", "某up主"}
    assert not (set(words) & forbidden)
    assert not any("http" in word or "www" in word for word in words)
    assert any("\u4e00" <= char <= "\u9fff" for word in words for char in word)
