"""分步流水线（带 settings 提交）与 `continue` / `retry` / 重启恢复的离线测试。

对照 spec 第 3 节与第 4 节第 4/5/6/7 条：

- 暂停：带 settings 提交在阶段边界暂停为 ``crawled`` / ``cleaned``，中间产物落盘
  ``outputs/videos/<bvid>/``（不覆盖 ``<bvid>.json``）；
- 继续：``crawled → continue → cleaned → continue → completed``；
- 自动连续：``auto_clean`` / ``auto_analyze`` 控制是否跨过暂停点；
- 语义：continue 的 409 / 404、retry 清空中间产物、重启保留等待态；
- 真实采集：逐任务参数生效、账号模式注入服务端 Cookie（索引 / 错误不出现明文）、
  清洗阶段真实执行 ``clean_text``（去空去重）、分析阶段逐条 ``predict_rule_based``、
  统计复用 ``placeholder.build_detail(comments=...)``；
- 占位模式（``real_crawl=false``）同样遵守阶段暂停，清洗阶段空操作。

所有用例都不联网：真实采集函数一律用假函数替换（或直接给流水线注入 collector）。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import src.crawler
from src.inference.app import app
from src.videos import pipeline, placeholder
from src.videos import service as videos_service
from src.videos import settings as video_settings
from src.videos.config import PipelineConfig
from src.videos.service import VideoService
from src.videos.store import VideoStore, now_iso

BVID = "BV1xx411c7mD"
BVID_SECOND = "BV1Ab411c7mE"
UNKNOWN_BVID = "BV1Zz411c7mF"

RAW_COOKIE = "SESSDATA=abcdef1234567890; bili_jct=csrf-token-value; DedeUserID=42"
SESSDATA_VALUE = "abcdef1234567890"

#: 0 秒配置：状态机瞬间跑完，测试不需要真等
INSTANT = PipelineConfig(0.0, 0.0, 0.0, 0.0)

#: 离线占位设置（real_crawl=false，避免任何网络）
OFFLINE_SETTINGS = {
    "account": {"mode": "anonymous"},
    "crawl": {"real_crawl": False},
    "pipeline": {"auto_clean": False, "auto_analyze": False},
}


def _offline(**overrides) -> dict:
    """基于 OFFLINE_SETTINGS 派生的任务设置（深拷贝，避免用例间互相影响）。"""
    data = json.loads(json.dumps(OFFLINE_SETTINGS))
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return data


def _make_client(tmp_path, name: str = "videos-staged", **config) -> tuple[TestClient, VideoService]:
    """构造注入临时目录 / 阶段时长的服务与绑定它的 TestClient。"""
    pipeline_config = PipelineConfig(**config) if config else INSTANT
    service = VideoService(root=tmp_path / name, config=pipeline_config)
    videos_service.set_service(service)
    return TestClient(app), service


@pytest.fixture
def staged_client(tmp_path):
    """瞬时阶段配置的客户端（分步 + continue 全链路）。"""
    client, service = _make_client(tmp_path)
    with client:
        yield client, service


def _wait_status(client: TestClient, bvid: str, statuses: set[str], timeout: float = 15.0) -> dict:
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


def _list_item(client: TestClient, bvid: str) -> dict:
    videos = client.get("/api/videos").json()["videos"]
    return next(item for item in videos if item["bvid"] == bvid)


def _submit(client: TestClient, settings: dict | None = None, bvid: str = BVID):
    payload: dict = {"bvid": bvid}
    if settings is not None:
        payload["settings"] = settings
    return client.post("/api/videos", json=payload)


def _continue(client: TestClient, bvid: str = BVID):
    return client.post(f"/api/videos/{bvid}/continue")


def _no_network(*args, **kwargs):  # pragma: no cover - 被调用即测试失败
    raise AssertionError("real_crawl=false 的任务不应调用真实爬虫")


# --------------------------------------------------------------------------- #
# 1. 带 settings 提交：在 crawled 暂停 + 落盘中间产物
# --------------------------------------------------------------------------- #
def test_submit_with_settings_pauses_at_crawled(staged_client, monkeypatch):
    client, service = staged_client
    monkeypatch.setattr(src.crawler, "fetch_video_comments", _no_network)

    response = _submit(client, OFFLINE_SETTINGS)
    assert response.status_code == 200
    assert set(response.json()) == {"bvid", "status", "progress", "created_at"}
    assert response.json()["status"] == "queued"

    detail = _wait_status(client, BVID, {"crawled"})
    assert detail["analysis"]["message"] == "采集完成，等待清洗"
    assert detail["analysis"]["progress"] == 40
    assert detail["sentiment"] is None
    assert detail["error"] is None

    # 列表项体现新状态与 phases
    item = _list_item(client, BVID)
    assert item["status"] == "crawled"
    assert item["progress"] == 40
    assert [phase["status"] for phase in item["phases"]] == ["done", "pending", "pending", "pending"]
    assert item["comment_count"] == placeholder.planned_comment_total(BVID)

    # detail.pipeline（additive）反映任务快照
    pipeline_view = detail["pipeline"]
    assert pipeline_view["account_mode"] == "anonymous"
    assert pipeline_view["auto_clean"] is False
    assert pipeline_view["auto_analyze"] is False
    assert pipeline_view["next_action"] == "clean"
    assert pipeline_view["crawl"]["real_crawl"] is False

    # 中间产物落盘到 <root>/<bvid>/，不覆盖 <bvid>.json
    assert (service.store.root / f"{BVID}.json").is_file() is False  # 未完成时还没有 detail
    crawl_artifact = service.store.read_stage(BVID, "crawl")
    assert crawl_artifact is not None
    assert crawl_artifact["mode"] == "placeholder"
    assert crawl_artifact["records"] == []
    assert crawl_artifact["comment_total"] == item["comment_count"]
    assert service.store.read_stage(BVID, "clean") is None

    # 任务内部字段：settings_snapshot 不含 Cookie
    task = service.store.get_task(BVID)
    assert "cookie" not in json.dumps(task["settings_snapshot"], ensure_ascii=False)
    assert task["resume_stage"] == "clean"
    assert task["stage_state"] == {"crawl": "done", "clean": "pending", "analyze": "pending"}


# --------------------------------------------------------------------------- #
# 2. 暂停 → continue → 再暂停 → continue → completed
# --------------------------------------------------------------------------- #
def test_continue_crawled_to_cleaned_then_completed(staged_client):
    client, service = staged_client
    assert _submit(client, OFFLINE_SETTINGS).status_code == 200
    _wait_status(client, BVID, {"crawled"})

    started = _continue(client)
    assert started.status_code == 200
    assert set(started.json()) == {"bvid", "status", "progress", "created_at"}
    assert started.json()["status"] == "queued" and started.json()["progress"] == 0

    cleaned_detail = _wait_status(client, BVID, {"cleaned"})
    assert cleaned_detail["analysis"]["message"] == "清洗完成，等待情感分析"
    assert cleaned_detail["analysis"]["progress"] == 60
    assert cleaned_detail["pipeline"]["next_action"] == "analyze"

    item = _list_item(client, BVID)
    assert item["status"] == "cleaned"
    assert [phase["status"] for phase in item["phases"]] == ["done", "done", "pending", "pending"]

    clean_artifact = service.store.read_stage(BVID, "clean")
    assert clean_artifact is not None
    assert clean_artifact["comment_total"] == 0  # 占位模式清洗阶段空操作
    assert clean_artifact["stats"]["raw_total"] == 0

    finished = _continue(client)
    assert finished.status_code == 200 and finished.json()["status"] == "queued"

    completed = _wait_status(client, BVID, {"completed"})
    assert completed["analysis"]["status"] == "completed"
    assert completed["analysis"]["progress"] == 100
    assert completed["analysis"]["message"] == "分析完成"
    assert completed["pipeline"]["next_action"] is None
    assert completed["placeholder"] is True  # 占位数据如实标注
    assert completed["sentiment"]["total"] == completed["video"]["comment_total"]
    assert completed["sentiment"]["total"] > 0
    assert _list_item(client, BVID)["status"] == "completed"
    assert (service.store.root / f"{BVID}.json").is_file()


# --------------------------------------------------------------------------- #
# 3. auto 连续执行
# --------------------------------------------------------------------------- #
def test_auto_clean_only_pauses_at_cleaned(staged_client):
    client, _ = staged_client
    settings = _offline(pipeline={"auto_clean": True, "auto_analyze": False})
    assert _submit(client, settings).status_code == 200

    body = _wait_status(client, BVID, {"cleaned"})
    assert body["pipeline"]["auto_clean"] is True
    assert body["pipeline"]["auto_analyze"] is False
    assert body["pipeline"]["next_action"] == "analyze"

    assert _continue(client).status_code == 200
    completed = _wait_status(client, BVID, {"completed"})
    assert completed["analysis"]["status"] == "completed"


def test_auto_clean_and_auto_analyze_runs_to_completed_without_continue(staged_client):
    client, _ = staged_client
    settings = _offline(pipeline={"auto_clean": True, "auto_analyze": True})
    assert _submit(client, settings).status_code == 200

    completed = _wait_status(client, BVID, {"completed"})
    assert completed["analysis"]["status"] == "completed"
    assert completed["pipeline"]["next_action"] is None
    # 没有经过等待态（中间态停留被自动连续跳过）
    assert _list_item(client, BVID)["status"] == "completed"


def test_auto_clean_does_not_stop_at_crawled(staged_client):
    """自动连续开启时不应停留在 crawled（轮询观察到的状态序列不含 crawled/cleaned 暂停）。"""
    client, service = staged_client
    settings = _offline(pipeline={"auto_clean": True, "auto_analyze": True})
    assert _submit(client, settings).status_code == 200

    observed: list[str] = []
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        state = _list_item(client, BVID)["status"]
        if not observed or observed[-1] != state:
            observed.append(state)
        if state == "completed":
            break
        time.sleep(0.005)
    # 终态完成；即使轮询恰好捕捉到中间态，也只在瞬时经过、不会停留
    assert observed[-1] == "completed"
    assert service.store.get_task(BVID)["status"] == "completed"


# --------------------------------------------------------------------------- #
# 4. continue 语义：409 / 404
# --------------------------------------------------------------------------- #
def test_continue_on_completed_returns_409(staged_client):
    client, _ = staged_client
    settings = _offline(pipeline={"auto_clean": True, "auto_analyze": True})
    assert _submit(client, settings).status_code == 200
    _wait_status(client, BVID, {"completed"})

    response = _continue(client)
    assert response.status_code == 409
    assert response.json()["detail"] == "任务当前状态 completed，无需继续"


def test_continue_unknown_bvid_returns_404(staged_client):
    client, _ = staged_client
    response = _continue(client, UNKNOWN_BVID)
    assert response.status_code == 404
    assert "detail" in response.json()


def test_continue_while_running_returns_409(tmp_path):
    client, _ = _make_client(tmp_path, name="videos-slow", crawl_seconds=2.0)
    with client:
        assert _submit(client, OFFLINE_SETTINGS).status_code == 200
        deadline = time.monotonic() + 5.0
        status = "queued"
        while time.monotonic() < deadline:
            status = _list_item(client, BVID)["status"]
            if status in {"crawling", "processing"}:
                break
            time.sleep(0.005)
        response = _continue(client)
        assert response.status_code == 409
        assert "无需继续" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 5. retry 清空中间产物
# --------------------------------------------------------------------------- #
def test_retry_clears_intermediate_artifacts(tmp_path, monkeypatch):
    client, service = _make_client(tmp_path, name="videos-retry", crawl_seconds=0.4)
    with client:
        assert _submit(client, OFFLINE_SETTINGS).status_code == 200
        _wait_status(client, BVID, {"crawled"})
        assert service.store.read_stage(BVID, "crawl") is not None

        # 用闸门把重跑后的「采集阶段」挡在写盘之前，保证断言无并发竞态
        import threading

        gate = threading.Event()
        original = pipeline._staged_crawl_stage

        def blocked_stage(*args, **kwargs):
            gate.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(pipeline, "_staged_crawl_stage", blocked_stage)

        retried = client.post(f"/api/videos/{BVID}/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"
        # retry 同步清空中间产物并重置 resume_stage
        assert service.store.read_stage(BVID, "crawl") is None
        task = service.store.get_task(BVID)
        assert task["resume_stage"] == "crawl"
        assert task["stage_state"] == {"crawl": "pending", "clean": "pending", "analyze": "pending"}

        # 放行后重新跑完再次停在 crawled，中间产物重新生成
        gate.set()
        _wait_status(client, BVID, {"crawled"})
        assert service.store.read_stage(BVID, "crawl") is not None


# --------------------------------------------------------------------------- #
# 6. 重启保留等待态
# --------------------------------------------------------------------------- #
def test_restart_preserves_waiting_state_and_can_continue(tmp_path):
    root = tmp_path / "videos-restart"

    first = VideoService(root=root, config=INSTANT)
    videos_service.set_service(first)
    client_one = TestClient(app)
    with client_one:
        assert _submit(client_one, OFFLINE_SETTINGS).status_code == 200
        _wait_status(client_one, BVID, {"crawled"})
        run_id_before = first.store.get_task(BVID)["run_id"]

    # 模拟服务重启：同一落盘目录、新单例
    assert first.store.get_task(BVID)["status"] == "crawled"
    second = VideoService(root=root, config=INSTANT)
    videos_service.set_service(second)
    client_two = TestClient(app)
    with client_two:
        time.sleep(0.2)  # 等待态不会被启动恢复逻辑重跑
        task = second.store.get_task(BVID)
        assert task["status"] == "crawled"
        assert task["run_id"] == run_id_before
        assert client_two.get(f"/api/videos/{BVID}/detail").json()["analysis"]["status"] == "crawled"

        # 重启后 continue 仍然可用
        assert _continue(client_two).status_code == 200
        _wait_status(client_two, BVID, {"cleaned"})
        assert _continue(client_two).status_code == 200
        final = _wait_status(client_two, BVID, {"completed"})
        assert final["analysis"]["status"] == "completed"


def test_restart_requeues_running_staged_task_and_resumes_from_stage(tmp_path):
    """服务重启：processing 任务被重新入队，并从已落盘的中间产物继续（不从头采集）。"""
    root = tmp_path / "videos-running-restart"
    store = VideoStore(root)
    snapshot = video_settings.snapshot_for_task(
        video_settings.merge_for_task(
            {
                "crawl": {"real_crawl": False},
                "pipeline": {"auto_clean": True, "auto_analyze": True},
            }
        )
    )
    _seed_staged_task(store, snapshot)
    # 模拟进程在「清洗」阶段崩溃：crawl.json 已落盘，状态停在 processing
    store.write_stage(
        BVID,
        "crawl",
        {"bvid": BVID, "mode": "placeholder", "comment_total": 10, "records": []},
    )
    store.update_task(
        BVID,
        1,
        status="processing",
        resume_stage="clean",
        stage_state={"crawl": "done", "clean": "pending", "analyze": "pending"},
    )

    service = VideoService(root=root, config=INSTANT)
    try:
        service.start()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            task = service.store.get_task(BVID)
            if task and task["status"] == "completed":
                break
            time.sleep(0.01)
        task = service.store.get_task(BVID)
        assert task is not None and task["status"] == "completed"
        detail = service.detail(BVID)
        assert detail["analysis"]["status"] == "completed"
        assert detail["video"]["comment_total"] == 10  # 从 crawl.json 的计数继续
        assert service.store.read_stage(BVID, "clean") is not None
    finally:
        service.shutdown()


# --------------------------------------------------------------------------- #
# 7. 真实采集（离线注入）+ 清洗 + 分析
# --------------------------------------------------------------------------- #
def _raw_records() -> list[dict]:
    """模拟爬虫返回的原始记录（未标准化；适配层会补全字段）。"""
    return [
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1",
            "text": "这个视频讲得太好了！ https://example.com/a?b=1",
            "likes": "100",
            "ctime_utc": "2026-10-15T08:00:00+00:00",
        },
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "2",
            # 清洗后与第 1 条相同 → 去重
            "text": "这个视频讲得太好了！",
            "likes": "50",
            "ctime_utc": "2026-10-15T09:00:00+00:00",
        },
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "3",
            "text": "广告太长，画质差，直接劝退。",
            "likes": "30",
            "ctime_utc": "2026-10-16T08:00:00+00:00",
        },
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "4",
            "text": "   ",  # 清洗后为空 → 丢弃
            "likes": "1",
            "ctime_utc": "2026-10-16T09:00:00+00:00",
        },
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "5",
            "text": "https://example.com/only-url",  # 清洗后为空 → 丢弃
            "likes": "2",
            "ctime_utc": "2026-10-16T10:00:00+00:00",
        },
    ]


def test_real_crawl_staged_cleans_dedupes_and_analyzes(staged_client, monkeypatch):
    client, service = staged_client
    captured: dict = {}

    def fake_fetch(bvid, **kwargs):
        captured["bvid"] = bvid
        captured["kwargs"] = dict(kwargs)
        return _raw_records()

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)

    settings = _offline(
        crawl={
            "real_crawl": True,
            "max_pages": 2,
            "page_size": 30,
            "sort_mode": "hot",
            "init_buvid": True,
            "include_replies": True,
        }
    )
    assert _submit(client, settings).status_code == 200
    crawled = _wait_status(client, BVID, {"crawled"})
    assert crawled["pipeline"]["crawl"]["real_crawl"] is True
    assert crawled["pipeline"]["crawl"]["max_pages"] == 2

    # 逐任务参数透传到爬虫
    assert captured["bvid"] == BVID
    kwargs = captured["kwargs"]
    assert kwargs["max_pages"] == 2
    assert kwargs["page_size"] == 30
    assert kwargs["mode"] == 3  # hot → 3
    assert kwargs["include_replies"] is True
    assert kwargs["init_buvid"] is True
    assert kwargs["cookie"] is None  # 匿名模式不注入 Cookie
    assert kwargs["require_login"] is False

    crawl_artifact = service.store.read_stage(BVID, "crawl")
    assert crawl_artifact["mode"] == "real"
    # 适配层会丢弃正文为空的记录（rpid=4 只有空白），保留 4 条
    assert len(crawl_artifact["records"]) == 4
    assert _list_item(client, BVID)["title"] == "真实视频标题（爬虫返回）"

    # 清洗阶段：clean_text + 去空 + 去重
    assert _continue(client).status_code == 200
    _wait_status(client, BVID, {"cleaned"})
    clean_artifact = service.store.read_stage(BVID, "clean")
    texts = [record["text"] for record in clean_artifact["records"]]
    # clean_text 保留感叹与分隔词元，删除 URL。
    assert texts == ["这个视频讲得太好了 标点感叹", "广告太长 标点分隔 画质差 标点分隔 直接劝退"]
    assert all("http" not in text for text in texts)
    assert clean_artifact["stats"] == {
        "raw_total": 4,
        "kept": 2,
        "dropped_empty": 1,
        "dropped_duplicate": 1,
    }

    # 分析阶段：逐条 predict_rule_based + 复用占位聚合
    assert _continue(client).status_code == 200
    completed = _wait_status(client, BVID, {"completed"})
    assert completed["placeholder"] is False
    assert completed["video"]["comment_total"] == 2
    assert completed["sentiment"]["total"] == 2
    top_texts = {
        item["text"]
        for side in ("positive", "negative")
        for item in completed["top_comments"][side]
    }
    assert top_texts <= set(texts)

    analyze_artifact = service.store.read_stage(BVID, "analyze")
    assert len(analyze_artifact["predictions"]) == 2
    for prediction in analyze_artifact["predictions"]:
        assert set(prediction) == {"text", "label", "confidence", "probs"}


def test_account_mode_injects_saved_cookie_without_leaking(staged_client, monkeypatch):
    client, service = staged_client
    saved = client.put(
        "/api/settings", json={"account": {"mode": "account", "cookie": RAW_COOKIE}}
    )
    assert saved.status_code == 200

    captured: dict = {}

    def fake_fetch(bvid, **kwargs):
        captured["kwargs"] = dict(kwargs)
        return _raw_records()

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fake_fetch)

    settings = _offline(
        account={"mode": "account"},
        crawl={"real_crawl": True, "require_login": True},
    )
    assert _submit(client, settings).status_code == 200
    _wait_status(client, BVID, {"crawled"})

    # 服务端已保存 Cookie 逐任务注入 BiliClient；require_login 透传
    assert captured["kwargs"]["cookie"] == RAW_COOKIE
    assert captured["kwargs"]["require_login"] is True

    # 任务快照 / 索引文件不含 Cookie 明文
    task = service.store.get_task(BVID)
    assert "cookie" not in json.dumps(task["settings_snapshot"], ensure_ascii=False)
    index_text = service.store.index_path.read_text(encoding="utf-8")
    assert SESSDATA_VALUE not in index_text
    assert "csrf-token-value" not in index_text

    detail = client.get(f"/api/videos/{BVID}/detail").json()
    assert detail["pipeline"]["account_mode"] == "account"
    assert SESSDATA_VALUE not in json.dumps(detail, ensure_ascii=False)
    assert "masked_cookie" not in json.dumps(detail, ensure_ascii=False)

    # 继续跑完成，详情不出现 Cookie
    assert _continue(client).status_code == 200
    _wait_status(client, BVID, {"cleaned"})
    assert _continue(client).status_code == 200
    completed = _wait_status(client, BVID, {"completed"})
    assert completed["placeholder"] is False
    assert SESSDATA_VALUE not in json.dumps(completed, ensure_ascii=False)


def test_crawl_failure_error_redacts_cookie(staged_client, monkeypatch):
    client, service = staged_client
    client.put("/api/settings", json={"account": {"cookie": RAW_COOKIE}})

    def boom(bvid, **kwargs):
        raise RuntimeError(f"网络错误，请求头包含 {RAW_COOKIE}")

    monkeypatch.setattr(src.crawler, "fetch_video_comments", boom)

    settings = _offline(
        account={"mode": "account"},
        crawl={"real_crawl": True},
    )
    assert _submit(client, settings).status_code == 200
    failed = _wait_status(client, BVID, {"failed"})

    assert failed["error"]
    assert SESSDATA_VALUE not in failed["error"]
    assert "csrf-token-value" not in failed["error"]
    assert "…" in failed["error"]
    index_text = service.store.index_path.read_text(encoding="utf-8")
    assert SESSDATA_VALUE not in index_text
    assert failed["pipeline"]["next_action"] is None


# --------------------------------------------------------------------------- #
# 8. 直接调用分步流水线（注入 collector，不经过 HTTP）
# --------------------------------------------------------------------------- #
def _seed_staged_task(store: VideoStore, snapshot: dict) -> dict:
    """往存储里塞一条分步流水线任务（模拟 service.submit 的落盘结果）。"""
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


def test_run_staged_pipeline_with_injected_collector(tmp_path):
    store = VideoStore(tmp_path / "videos")
    merged = video_settings.merge_for_task(
        {
            "account": {"mode": "anonymous"},
            "crawl": {"real_crawl": True},
            "pipeline": {"auto_clean": False, "auto_analyze": False},
        }
    )
    snapshot = video_settings.snapshot_for_task(merged)
    _seed_staged_task(store, snapshot)

    calls: list = []
    records = [_raw_records()[0], _raw_records()[2]]

    def collector(bvid, crawl_config):
        calls.append((bvid, crawl_config))
        return records

    result = pipeline.run_pipeline(
        store, BVID, INSTANT, task_settings=snapshot, collector=collector
    )
    assert result is None  # 在 crawled 暂停
    assert store.get_task(BVID)["status"] == "crawled"
    assert calls and calls[0][1].real_crawl is True
    assert calls[0][1].cookie is None

    # 模拟 continue：resume_stage=clean 后继续跑 → cleaned 暂停
    store.update_task(BVID, 1, status="queued", resume_stage="clean", progress=0)
    assert (
        pipeline.run_pipeline(store, BVID, INSTANT, task_settings=snapshot, collector=collector)
        is None
    )
    assert store.get_task(BVID)["status"] == "cleaned"

    # 再 continue：resume_stage=analyze → completed
    store.update_task(BVID, 1, status="queued", resume_stage="analyze", progress=0)
    detail = pipeline.run_pipeline(store, BVID, INSTANT, task_settings=snapshot, collector=collector)
    assert store.get_task(BVID)["status"] == "completed"
    assert detail is not None and detail["placeholder"] is False
    assert detail["video"]["comment_total"] == 2


# --------------------------------------------------------------------------- #
# 9. 提交时的设置校验（400）与旧路径不受影响
# --------------------------------------------------------------------------- #
def test_submit_with_invalid_settings_returns_400(staged_client):
    client, service = staged_client
    response = _submit(client, {"crawl": {"max_pages": 99}})
    assert response.status_code == 400
    assert "max_pages" in response.json()["detail"]
    assert client.get("/api/videos").json() == {"total": 0, "videos": []}

    response = _submit(client, {"account": {"mode": "account"}})
    assert response.status_code == 400
    assert "Cookie" in response.json()["detail"]


def test_submit_without_settings_keeps_legacy_behavior(staged_client):
    """不带 settings 仍是旧路径：一次跑完四阶段，不出现等待态。"""
    client, service = staged_client
    assert _submit(client).status_code == 200

    completed = _wait_status(client, BVID, {"completed"})
    assert completed["analysis"]["message"] == "分析完成"
    task = service.store.get_task(BVID)
    assert task.get("settings_snapshot") is None
    assert task.get("resume_stage") is None
    assert completed["pipeline"]["account_mode"] == "anonymous"
    assert completed["pipeline"]["next_action"] is None
    # 旧路径不产生任务级中间产物目录
    assert not (service.store.root / BVID).exists()
