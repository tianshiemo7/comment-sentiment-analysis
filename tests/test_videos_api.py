"""`/api/videos` 系列接口契约测试（对照 `docs/API契约.md` 第二、三节）。

设计要点：

- **不真等 30 秒**：用毫秒级 `PipelineConfig` 注入服务，状态机在 1 秒内跑完；
- 用 `TestClient(app)` 作为上下文管理器，触发 lifespan（启动恢复 + 优雅关停），
  这样才能覆盖真实的后台线程行为；
- 断言严格按契约字段名与状态枚举，改名 / 改结构立即可见；
- 覆盖：提交 400/409、列表字段与倒序、详情 404 与进行中分支、retry / delete
  成功与 404、状态机推进（progress 单调不减）、failed + error。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src.inference.app import app
from src.videos import service as videos_service
from src.videos.config import PipelineConfig
from src.videos.schemas import PHASE_NAMES, RUNNING_STATUSES
from src.videos.service import VideoService

BVID = "BV1xx411c7mD"
BVID_SECOND = "BV1Ab411c7mE"
UNKNOWN_BVID = "BV1Zz411c7mF"

#: 列表项字段（契约第二节，一个不多一个不少）
LIST_FIELDS = {
    "bvid",
    "title",
    "owner",
    "cover_url",
    "status",
    "progress",
    "message",
    "phases",
    "comment_count",
    "created_at",
    "updated_at",
}

#: 提交 / 重试响应字段（契约第二节接口 1）
SUBMIT_FIELDS = {"bvid", "status", "progress", "created_at"}

#: 详情顶层字段（契约第三节 + 新增的 placeholder 标注字段）
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
}

LEGACY_PATHS = {
    "/health",
    "/api/predict",
    "/api/predict/batch",
    "/api/predict/batch/download/{file_id}",
    "/api/models",
}


def _make_client(tmp_path, name: str, **overrides) -> tuple[TestClient, VideoService]:
    """构造注入临时目录 + 指定阶段时长的服务，并返回绑定它的 TestClient。"""
    service = VideoService(root=tmp_path / name, config=PipelineConfig(**overrides))
    videos_service.set_service(service)
    return TestClient(app), service


@pytest.fixture
def running_client(tmp_path):
    """阶段较慢（采集 3 秒）：用于「进行中」形态、409、列表排序、retry/delete。"""
    client, service = _make_client(
        tmp_path,
        "videos-running",
        crawl_seconds=3.0,
        clean_seconds=0.5,
        sentiment_seconds=0.5,
        stats_seconds=0.5,
    )
    with client:  # 触发 lifespan：start（含恢复）→ yield → shutdown（优雅退出）
        yield client, service


@pytest.fixture
def fast_client(tmp_path):
    """阶段极快（合计 0.75 秒）：用于跑完状态机并检查 completed 详情。"""
    client, service = _make_client(
        tmp_path,
        "videos-fast",
        crawl_seconds=0.3,
        clean_seconds=0.15,
        sentiment_seconds=0.15,
        stats_seconds=0.15,
    )
    with client:
        yield client, service


@pytest.fixture
def failing_client(tmp_path):
    """模拟「情感推理」阶段失败：用于 failed + error + retry（耗时约 0.6 秒失败一次）。"""
    client, service = _make_client(
        tmp_path,
        "videos-failing",
        crawl_seconds=0.4,
        clean_seconds=0.2,
        sentiment_seconds=0.1,
        stats_seconds=0.1,
        simulate_failure=True,
    )
    with client:
        yield client, service


def _wait_for_status(client: TestClient, bvid: str, statuses, timeout: float = 15.0) -> dict:
    """轮询详情直到 `analysis.status` 落在 `statuses` 内，返回该详情。"""
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
# 1. POST /api/videos
# --------------------------------------------------------------------------- #
def test_submit_valid_bvid_returns_queued(running_client):
    client, _ = running_client
    response = client.post("/api/videos", json={"bvid": BVID})
    assert response.status_code == 200

    body = response.json()
    assert set(body) == SUBMIT_FIELDS
    assert body["bvid"] == BVID
    assert body["status"] == "queued"
    assert body["progress"] == 0
    assert isinstance(body["created_at"], str) and body["created_at"]


@pytest.mark.parametrize(
    "bad_bvid",
    [
        "",  # 空
        "BV",  # 太短
        "BV1xx411c7m",  # 10 位总长（少 2 位）
        "BV1xx411c7mDD",  # 多一位
        "bv1xx411c7mD",  # 小写前缀
        "AV1xx411c7mD",  # 前缀错误
        "BV1xx411c7m!",  # 含非法字符
        " BV1xx411c7mD ",  # 首尾空白（正则整体匹配，不接受）
    ],
)
def test_submit_invalid_bvid_returns_400(running_client, bad_bvid):
    client, _ = running_client
    response = client.post("/api/videos", json={"bvid": bad_bvid})
    assert response.status_code == 400
    assert response.json() == {"detail": "BV 号格式不正确，应形如 BV1xx411c7mD"}


def test_submit_missing_bvid_returns_422(running_client):
    client, _ = running_client
    assert client.post("/api/videos", json={}).status_code == 422


def test_duplicate_submit_while_running_returns_409(running_client):
    client, _ = running_client
    assert client.post("/api/videos", json={"bvid": BVID}).status_code == 200

    duplicate = client.post("/api/videos", json={"bvid": BVID})
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "该视频已在分析队列中"}


def test_resubmit_after_completed_reruns(fast_client):
    client, _ = fast_client
    client.post("/api/videos", json={"bvid": BVID})
    _wait_for_status(client, BVID, {"completed"})

    again = client.post("/api/videos", json={"bvid": BVID})
    assert again.status_code == 200
    body = again.json()
    assert body["status"] == "queued" and body["progress"] == 0


# --------------------------------------------------------------------------- #
# 2. GET /api/videos
# --------------------------------------------------------------------------- #
def test_list_contract_and_order_desc(running_client):
    client, _ = running_client
    client.post("/api/videos", json={"bvid": BVID})
    client.post("/api/videos", json={"bvid": BVID_SECOND})

    response = client.get("/api/videos")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"total", "videos"}
    assert body["total"] == 2
    assert len(body["videos"]) == 2

    # 按 created_at 倒序：后提交的在前
    assert [item["bvid"] for item in body["videos"]] == [BVID_SECOND, BVID]

    for item in body["videos"]:
        assert set(item) == LIST_FIELDS
        assert item["status"] in RUNNING_STATUSES
        assert 0 <= item["progress"] <= 100
        assert isinstance(item["comment_count"], int) and item["comment_count"] >= 0
        assert [phase["name"] for phase in item["phases"]] == list(PHASE_NAMES)
        assert all(
            phase["status"] in {"pending", "active", "done", "failed"}
            for phase in item["phases"]
        )


def test_list_allows_null_title_owner_cover(running_client):
    """任务进行中 `title` / `owner` / `cover_url` 允许为 null（前端显示 BV 号占位）。"""
    client, _ = running_client
    client.post("/api/videos", json={"bvid": BVID})

    item = client.get("/api/videos").json()["videos"][0]
    assert item["title"] is None
    assert item["owner"] is None
    assert item["cover_url"] is None
    assert item["message"] in {"排队中…", "正在爬取评论…", "正在清洗与情感分析…"}


# --------------------------------------------------------------------------- #
# 3. GET /api/videos/{bvid}/detail
# --------------------------------------------------------------------------- #
def test_detail_unknown_bvid_returns_404(running_client):
    client, _ = running_client
    response = client.get(f"/api/videos/{UNKNOWN_BVID}/detail")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_detail_in_progress_returns_200_with_empty_data(running_client):
    """未完成时不报错：`sentiment` 为 null，trend / keywords / top_comments 为空。"""
    client, _ = running_client
    client.post("/api/videos", json={"bvid": BVID})

    response = client.get(f"/api/videos/{BVID}/detail")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == DETAIL_FIELDS
    assert body["analysis"]["status"] in RUNNING_STATUSES
    assert body["analysis"]["status"] != "completed"
    assert 0 <= body["analysis"]["progress"] < 100
    assert body["analysis"]["message"]
    assert body["analysis"]["length_stats"] is None
    assert body["sentiment"] is None
    assert body["trend"] == []
    assert body["keywords"] == {"positive": [], "negative": []}
    assert body["top_comments"] == {"positive": [], "negative": []}
    assert body["error"] is None
    assert body["placeholder"] is True
    assert body["video"]["bvid"] == BVID


def test_detail_completed_has_full_contract_fields(fast_client):
    client, _ = fast_client
    client.post("/api/videos", json={"bvid": BVID})
    body = _wait_for_status(client, BVID, {"completed"})

    assert set(body) == DETAIL_FIELDS

    # analysis
    assert body["analysis"]["status"] == "completed"
    assert body["analysis"]["progress"] == 100
    assert body["analysis"]["message"] == "分析完成"
    assert set(body["analysis"]["length_stats"]) == {"avg_chars", "max_chars"}

    # video
    video = body["video"]
    assert set(video) == {"bvid", "title", "owner", "cover_url", "pubdate", "crawled_at", "comment_total"}
    assert video["bvid"] == BVID
    assert video["title"] and video["owner"]
    assert video["pubdate"] and video["crawled_at"]
    assert video["comment_total"] > 0

    # sentiment：计数自洽且比例之和 ≈ 1
    sentiment = body["sentiment"]
    assert set(sentiment) == {
        "total",
        "positive",
        "negative",
        "neutral",
        "positive_ratio",
        "negative_ratio",
        "neutral_ratio",
    }
    assert sentiment["positive"] + sentiment["negative"] + sentiment["neutral"] == sentiment["total"]
    assert sentiment["total"] == video["comment_total"]
    ratio_sum = (
        sentiment["positive_ratio"]
        + sentiment["negative_ratio"]
        + sentiment["neutral_ratio"]
    )
    assert ratio_sum == pytest.approx(1.0, abs=0.01)

    # trend：按天分桶
    assert body["trend"] and isinstance(body["trend"], list)
    for point in body["trend"]:
        assert set(point) == {"bucket", "positive", "negative", "neutral"}
        assert len(point["bucket"]) == 10  # YYYY-MM-DD
        assert min(point["positive"], point["negative"], point["neutral"]) >= 0

    # keywords：词云数据（后端不出图）
    for side in ("positive", "negative"):
        assert body["keywords"][side], f"keywords.{side} 不应为空"
        for item in body["keywords"][side]:
            assert set(item) == {"word", "count", "weight"}
            assert item["count"] >= 0
            assert 0.0 <= item["weight"] <= 1.0

    # model：当前生效模型的指标
    assert set(body["model"]) == {"name", "accuracy", "precision", "recall", "f1"}
    assert body["model"]["name"]
    for key in ("accuracy", "precision", "recall", "f1"):
        assert 0.0 <= body["model"][key] <= 1.0

    # top_comments：每侧 ≤ 10 条
    for side, label in (("positive", "positive"), ("negative", "negative")):
        assert body["top_comments"][side], f"top_comments.{side} 不应为空"
        assert len(body["top_comments"][side]) <= 10
        for comment in body["top_comments"][side]:
            assert set(comment) == {"text", "like", "confidence", "label"}
            assert comment["label"] == label
            assert comment["like"] >= 0
            assert 0.0 <= comment["confidence"] <= 1.0

    assert body["error"] is None
    assert body["placeholder"] is True  # 诚实标注：占位数据

    # 列表项与详情一致（标题已回填）
    item = client.get("/api/videos").json()["videos"][0]
    assert item["status"] == "completed"
    assert item["progress"] == 100
    assert item["title"] == video["title"]
    assert item["comment_count"] == video["comment_total"]
    assert all(phase["status"] == "done" for phase in item["phases"])


# --------------------------------------------------------------------------- #
# 4. POST /api/videos/{bvid}/retry
# --------------------------------------------------------------------------- #
def test_retry_resets_task_to_queued(running_client):
    client, _ = running_client
    client.post("/api/videos", json={"bvid": BVID})
    _wait_for_status(client, BVID, {"crawling", "processing"})

    response = client.post(f"/api/videos/{BVID}/retry")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUBMIT_FIELDS
    assert body["bvid"] == BVID
    assert body["status"] == "queued"
    assert body["progress"] == 0

    detail = client.get(f"/api/videos/{BVID}/detail").json()
    assert detail["analysis"]["status"] in RUNNING_STATUSES
    assert detail["sentiment"] is None
    assert detail["error"] is None


def test_retry_failed_task_returns_to_queued(failing_client):
    client, _ = failing_client
    client.post("/api/videos", json={"bvid": BVID})
    failed = _wait_for_status(client, BVID, {"failed"})

    # failed 时 error 给出原因，其余数据字段为空
    assert failed["error"]
    assert failed["sentiment"] is None
    assert failed["trend"] == []
    assert failed["keywords"] == {"positive": [], "negative": []}
    assert any(phase["status"] == "failed" for phase in _failed_phases(client, BVID))

    retried = client.post(f"/api/videos/{BVID}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"

    detail = client.get(f"/api/videos/{BVID}/detail").json()
    assert detail["analysis"]["status"] in RUNNING_STATUSES
    assert detail["error"] is None


def _failed_phases(client: TestClient, bvid: str) -> list[dict]:
    """取列表里该任务的 phases（failed 分支应有一个 `failed`）。"""
    videos = client.get("/api/videos").json()["videos"]
    return next(item["phases"] for item in videos if item["bvid"] == bvid)


def test_retry_unknown_bvid_returns_404(running_client):
    client, _ = running_client
    response = client.post(f"/api/videos/{UNKNOWN_BVID}/retry")
    assert response.status_code == 404
    assert "detail" in response.json()


# --------------------------------------------------------------------------- #
# 5. DELETE /api/videos/{bvid}
# --------------------------------------------------------------------------- #
def test_delete_removes_task_and_detail(running_client):
    client, _ = running_client
    client.post("/api/videos", json={"bvid": BVID})

    response = client.delete(f"/api/videos/{BVID}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    assert client.get(f"/api/videos/{BVID}/detail").status_code == 404
    assert client.get("/api/videos").json() == {"total": 0, "videos": []}


def test_delete_unknown_bvid_returns_404(running_client):
    client, _ = running_client
    response = client.delete(f"/api/videos/{UNKNOWN_BVID}")
    assert response.status_code == 404
    assert "detail" in response.json()


# --------------------------------------------------------------------------- #
# 状态机推进：progress 单调不减、状态按序推进
# --------------------------------------------------------------------------- #
def test_state_machine_progress_is_monotonic_and_ordered(fast_client):
    client, _ = fast_client
    submitted = client.post("/api/videos", json={"bvid": BVID}).json()
    assert submitted["status"] == "queued" and submitted["progress"] == 0

    order = ["queued", "crawling", "processing", "completed"]
    history: list[tuple[str, int]] = [(submitted["status"], submitted["progress"])]
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        analysis = client.get(f"/api/videos/{BVID}/detail").json()["analysis"]
        pair = (analysis["status"], analysis["progress"])
        if history[-1] != pair:
            history.append(pair)
        if analysis["status"] == "completed":
            break
        time.sleep(0.005)

    assert history[-1][0] == "completed", f"状态机未走完：{history}"

    # progress 单调不减，且始终落在 0~100
    progresses = [progress for _, progress in history]
    assert progresses == sorted(progresses), f"progress 出现回退：{history}"
    assert progresses[0] == 0
    assert progresses[-1] == 100

    # 状态按 queued → crawling → processing → completed 顺序出现，不回头
    seen_statuses: list[str] = []
    for status, _ in history:
        if not seen_statuses or seen_statuses[-1] != status:
            seen_statuses.append(status)
    assert seen_statuses == order[: len(seen_statuses)], f"状态顺序异常：{seen_statuses}"
    assert seen_statuses == order  # 四个状态必须全部经历过

    # phases 名称与状态取值合法，完成后全 done
    detail = client.get(f"/api/videos/{BVID}/detail").json()
    assert detail["analysis"]["length_stats"] is not None
    item = client.get("/api/videos").json()["videos"][0]
    assert [phase["name"] for phase in item["phases"]] == list(PHASE_NAMES)
    assert all(phase["status"] == "done" for phase in item["phases"])


# --------------------------------------------------------------------------- #
# 既有 5 个接口必须保持不变
# --------------------------------------------------------------------------- #
def test_legacy_endpoints_still_work(running_client):
    client, _ = running_client
    assert client.get("/health").status_code == 200
    assert client.get("/api/models").status_code == 200
    predict = client.post("/api/predict", json={"text": "这个商品质量很好，非常满意。"})
    assert predict.status_code == 200 and predict.json()["label"] == "positive"


def test_openapi_still_lists_legacy_paths(running_client):
    client, _ = running_client
    paths = set(client.get("/openapi.json").json()["paths"])
    assert LEGACY_PATHS <= paths
    assert {
        "/api/videos",
        "/api/videos/{bvid}/detail",
        "/api/videos/{bvid}/retry",
        "/api/videos/{bvid}",
    } <= paths
