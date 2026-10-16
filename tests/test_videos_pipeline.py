"""`src/videos` 服务与流水线测试（不经过 HTTP，确定性断言状态机与落盘恢复）。

覆盖：

- `pipeline.run_pipeline` 的状态顺序、`progress` 单调不减、phases 取值、占位 detail 完整性；
- 阶段时长可配置（0 秒配置即可瞬间跑完，不需要真等 30 秒）；
- `failed` 分支（`simulate_failure`）+ `error` 文案；
- 落盘与恢复：`index.json` / `<bvid>.json`、重启后把残留 `crawling` 任务重置为 `queued` 并跑完；
- 优雅退出：`shutdown()` 后线程停止、任务恢复成 `queued`（下次启动续跑）；
- 路径安全（bvid 正则）与 400 / 409 映射的业务异常。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.videos import pipeline, placeholder
from src.videos.config import PipelineConfig
from src.videos.errors import (
    InvalidBvidError,
    PipelineAborted,
    PipelineFailure,
    VideoBusyError,
    VideoNotFoundError,
)
from src.videos.messages import MESSAGE_QUEUED
from src.videos.schemas import PHASE_NAMES, PHASE_STATUSES
from src.videos.service import VideoService
from src.videos.store import VideoStore, now_iso

BVID = "BV1xx411c7mD"
BVID_OTHER = "BV1Ab411c7mE"

#: 0 秒配置：状态机瞬间跑完，测试不需要真等
INSTANT = PipelineConfig(
    crawl_seconds=0.0, clean_seconds=0.0, sentiment_seconds=0.0, stats_seconds=0.0
)


def _valid_phase_shape(statuses: list[str]) -> bool:
    """phases 的合法形状：若干 `done` → 至多一个 `active`/`failed` → 若干 `pending`。"""
    if any(status not in PHASE_STATUSES for status in statuses):
        return False
    if statuses.count("active") > 1 or statuses.count("failed") > 1:
        return False

    core = [status for status in statuses if status != "pending"]
    done_count = 0
    while done_count < len(core) and core[done_count] == "done":
        done_count += 1
    tail = core[done_count:]
    if len(tail) > 1 or (tail and tail[0] not in ("active", "failed")):
        return False

    if "pending" in statuses:
        first_pending = statuses.index("pending")
        if any(status != "pending" for status in statuses[first_pending:]):
            return False
    return True


def _seed_task(store: VideoStore, bvid: str, *, run_id: int = 1, status: str = "queued") -> dict:
    """直接往存储里塞一条任务（模拟提交后的状态，`run_id` 为第 1 代）。"""
    task = {
        "bvid": bvid,
        "title": None,
        "owner": None,
        "cover_url": None,
        "status": status,
        "progress": 0,
        "message": MESSAGE_QUEUED,
        "phases": pipeline.initial_phases(),
        "comment_count": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "error": None,
        "run_id": run_id,
        "seq": run_id,
    }
    store.upsert_task(task)
    return task


# --------------------------------------------------------------------------- #
# 流水线：状态顺序、进度单调、phases、占位 detail
# --------------------------------------------------------------------------- #
def test_run_pipeline_orders_states_and_keeps_progress_monotonic(tmp_path):
    store = VideoStore(tmp_path / "videos")
    _seed_task(store, BVID)

    snapshots: list[dict] = []
    detail = pipeline.run_pipeline(
        store, BVID, INSTANT, on_update=lambda task: snapshots.append(task)
    )

    # 状态首次出现的顺序必须是 queued → crawling → processing → completed
    # （种子任务是 queued，`on_update` 从进入 crawling 阶段开始回调）
    order: list[str] = []
    for task in snapshots:
        if not order or order[-1] != task["status"]:
            order.append(task["status"])
    assert order == ["crawling", "processing", "completed"], order

    # progress 单调不减、落在 0~100、最终 100
    progresses = [task["progress"] for task in snapshots]
    assert progresses == sorted(progresses), progresses
    assert all(0 <= value <= 100 for value in progresses)
    assert progresses[-1] == 100

    # 阶段边界：采集评论结束于 40，随后进入 processing
    crawling_progress = [t["progress"] for t in snapshots if t["status"] == "crawling"]
    assert crawling_progress and max(crawling_progress) == 40
    processing_progress = [t["progress"] for t in snapshots if t["status"] == "processing"]
    assert processing_progress and min(processing_progress) >= 40

    # phases：名称固定四个，状态取值合法且形状只能是「若干 done + 至多一个 active + 若干 pending」
    for task in snapshots:
        phases = task["phases"]
        assert [phase["name"] for phase in phases] == list(PHASE_NAMES)
        assert all(phase["status"] in PHASE_STATUSES for phase in phases)
        assert _valid_phase_shape([phase["status"] for phase in phases]), phases
    assert all(phase["status"] == "done" for phase in snapshots[-1]["phases"])

    # comment_count 在采集阶段朝最终值增长，完成时等于评论总数
    final_task = store.get_task(BVID)
    assert final_task is not None
    assert final_task["status"] == "completed"
    assert final_task["progress"] == 100
    assert final_task["comment_count"] == detail["video"]["comment_total"] > 0
    assert final_task["error"] is None
    assert final_task["title"] == detail["video"]["title"]


def test_placeholder_detail_is_structurally_complete_and_deterministic(tmp_path):
    store = VideoStore(tmp_path / "videos")
    _seed_task(store, BVID)
    detail = pipeline.run_pipeline(store, BVID, INSTANT)

    assert set(detail) == {
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
    assert detail["placeholder"] is True  # 诚实标注占位
    assert detail["analysis"]["status"] == "completed"
    assert detail["analysis"]["progress"] == 100
    assert detail["error"] is None

    sentiment = detail["sentiment"]
    assert sentiment["positive"] + sentiment["negative"] + sentiment["neutral"] == sentiment["total"]
    assert sum(
        sentiment[key] for key in ("positive_ratio", "negative_ratio", "neutral_ratio")
    ) == pytest.approx(1.0, abs=0.01)

    assert detail["trend"] and all(
        set(point) == {"bucket", "positive", "negative", "neutral"} for point in detail["trend"]
    )
    assert detail["keywords"]["positive"] and detail["keywords"]["negative"]
    assert detail["top_comments"]["positive"] and detail["top_comments"]["negative"]
    assert set(detail["model"]) == {"name", "accuracy", "precision", "recall", "f1"}

    # 同一 BV 号 + 同一完成时间的结果必须完全一致（确定性伪随机，不依赖全局随机状态）
    fixed = datetime(2026, 10, 16, 14, 30, 0, tzinfo=timezone(timedelta(hours=8)))
    first = placeholder.build_detail(BVID, finished_at=fixed)
    second = placeholder.build_detail(BVID, finished_at=fixed)
    assert first == second

    # 不同 BV 号的数据不应雷同
    other = placeholder.build_detail(BVID_OTHER, finished_at=fixed)
    assert other != first


def test_failure_marks_failed_with_reason_and_keeps_progress(tmp_path):
    store = VideoStore(tmp_path / "videos")
    _seed_task(store, BVID)
    config = PipelineConfig(0.0, 0.0, 0.0, 0.0, simulate_failure=True)

    with pytest.raises(PipelineFailure):
        pipeline.run_pipeline(store, BVID, config)

    task = store.get_task(BVID)
    assert task is not None
    assert task["status"] == "failed"
    assert task["error"] and "模拟" in task["error"]
    assert task["message"].startswith("分析失败")
    # 失败发生在「情感推理」阶段起点（采集 40 + 清洗 20），进度不回退
    assert task["progress"] == 60
    failed_phase = next(phase for phase in task["phases"] if phase["status"] == "failed")
    assert failed_phase["name"] == "情感推理"


def test_run_pipeline_aborts_when_stop_event_is_set(tmp_path):
    store = VideoStore(tmp_path / "videos")
    _seed_task(store, BVID)
    stop_event = threading.Event()
    stop_event.set()

    with pytest.raises(PipelineAborted):
        pipeline.run_pipeline(store, BVID, INSTANT, stop_event=stop_event)


def test_run_pipeline_aborts_when_task_is_deleted(tmp_path):
    store = VideoStore(tmp_path / "videos")
    with pytest.raises(PipelineAborted):
        pipeline.run_pipeline(store, BVID, INSTANT)


def test_stage_durations_are_configurable():
    config = PipelineConfig(
        crawl_seconds=0.4, clean_seconds=0.2, sentiment_seconds=0.2, stats_seconds=0.2
    )
    assert config.total_seconds == pytest.approx(1.0)
    assert PipelineConfig().total_seconds == pytest.approx(30.0)  # 默认与前端 mock 一致

    with pytest.raises(ValueError):
        PipelineConfig(crawl_seconds=-1)
    with pytest.raises(ValueError):
        PipelineConfig(steps_per_phase=0)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("VIDEO_CRAWL_SECONDS", "0.5")
    monkeypatch.setenv("VIDEO_STEPS_PER_PHASE", "2")
    monkeypatch.setenv("VIDEO_SIMULATE_FAILURE", "1")
    monkeypatch.setenv("VIDEO_STATS_SECONDS", "不是数字")  # 非法值回退默认

    config = PipelineConfig.from_env()
    assert config.crawl_seconds == 0.5
    assert config.steps_per_phase == 2
    assert config.simulate_failure is True
    assert config.stats_seconds == 4.0


# --------------------------------------------------------------------------- #
# 存储：落盘、恢复、路径安全
# --------------------------------------------------------------------------- #
def test_store_persists_index_and_detail_and_reloads(tmp_path):
    root = tmp_path / "videos"
    store = VideoStore(root)
    _seed_task(store, BVID)
    pipeline.run_pipeline(store, BVID, INSTANT)

    index_path = root / "index.json"
    detail_path = root / f"{BVID}.json"
    assert index_path.is_file() and detail_path.is_file()

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert [task["bvid"] for task in payload["videos"]] == [BVID]
    assert payload["videos"][0]["status"] == "completed"

    reloaded = VideoStore(root)
    reloaded.load()
    assert reloaded.get_task(BVID)["status"] == "completed"
    assert reloaded.read_detail(BVID)["video"]["bvid"] == BVID

    # 删除会同时清掉 detail 文件
    assert reloaded.remove_task(BVID) is True
    assert not detail_path.exists()
    assert reloaded.list_tasks() == []


def test_store_rejects_path_traversal_bvid(tmp_path):
    store = VideoStore(tmp_path / "videos")
    for unsafe in ("../../etc/passwd", "BV1xx411c7mD/../x", "BV1xx411c7mD\\x", "not-a-bvid", ""):
        assert store.get_task(unsafe) is None
        assert store.read_detail(unsafe) is None
        assert store.remove_task(unsafe) is False
        with pytest.raises(ValueError):
            store.detail_path(unsafe)


def test_store_tolerates_corrupted_index(tmp_path):
    root = tmp_path / "videos"
    root.mkdir(parents=True)
    (root / "index.json").write_text("{ 这不是合法 JSON", encoding="utf-8")

    store = VideoStore(root)
    store.load()  # 不应抛异常
    assert store.list_tasks() == []


def test_start_recovers_stale_running_task(tmp_path):
    """启动恢复：index.json 里残留的 `crawling` 任务被重置为 queued 并重新跑完。"""
    root = tmp_path / "videos"
    store = VideoStore(root)
    _seed_task(store, BVID, status="crawling")
    stale = store.get_task(BVID)
    store.update_task(BVID, stale["run_id"], progress=17)

    service = VideoService(
        root=root,
        config=PipelineConfig(
            crawl_seconds=0.05, clean_seconds=0.05, sentiment_seconds=0.05, stats_seconds=0.05
        ),
    )
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
        assert task["progress"] == 100
        # 恢复时进度被重置（不是从 17 继续），重新走完整条流水线
        assert service.detail(BVID)["analysis"]["status"] == "completed"
    finally:
        service.shutdown()


def test_shutdown_is_graceful_and_resets_running_task(tmp_path):
    """优雅退出：shutdown 后线程停止、任务恢复成 queued，新进程接着跑完。"""
    root = tmp_path / "videos"
    service = VideoService(
        root=root,
        config=PipelineConfig(
            crawl_seconds=30.0, clean_seconds=1.0, sentiment_seconds=1.0, stats_seconds=1.0
        ),
    )
    try:
        service.start()
        service.submit(BVID)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            task = service.store.get_task(BVID)
            if task and task["status"] == "crawling":
                break
            time.sleep(0.01)
        assert service.store.get_task(BVID)["status"] == "crawling"

        started = time.monotonic()
        service.shutdown()
        assert time.monotonic() - started < 3.0, "shutdown 不应等整个阶段跑完"
        assert service.running is False
        assert service.store.get_task(BVID)["status"] == "queued"

        # 新进程（同目录、短时长）启动后应自动续跑完成
        restarted = VideoService(
            root=root,
            config=PipelineConfig(
                crawl_seconds=0.05, clean_seconds=0.05, sentiment_seconds=0.05, stats_seconds=0.05
            ),
        )
        try:
            restarted.start()
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                task = restarted.store.get_task(BVID)
                if task and task["status"] == "completed":
                    break
                time.sleep(0.01)
            assert restarted.store.get_task(BVID)["status"] == "completed"
        finally:
            restarted.shutdown()
    finally:
        service.shutdown()


# --------------------------------------------------------------------------- #
# service：400 / 409 / 404 与 retry / delete
# --------------------------------------------------------------------------- #
def test_service_submit_validates_bvid(tmp_path):
    service = VideoService(root=tmp_path / "videos", config=INSTANT)
    try:
        with pytest.raises(InvalidBvidError):
            service.submit("BV123")
        with pytest.raises(InvalidBvidError):
            service.submit("")
    finally:
        service.shutdown()


def test_service_rejects_duplicate_while_running(tmp_path):
    service = VideoService(
        root=tmp_path / "videos", config=PipelineConfig(crawl_seconds=30.0)
    )
    try:
        service.start()
        service.submit(BVID)
        with pytest.raises(VideoBusyError):
            service.submit(BVID)
    finally:
        service.shutdown()


def test_service_retry_and_delete_unknown_bvid(tmp_path):
    service = VideoService(root=tmp_path / "videos", config=INSTANT)
    try:
        service.start()
        with pytest.raises(VideoNotFoundError):
            service.retry(BVID)
        with pytest.raises(VideoNotFoundError):
            service.delete(BVID)
        with pytest.raises(VideoNotFoundError):
            service.detail(BVID)

        service.submit(BVID)
        assert service.delete(BVID) is True
        with pytest.raises(VideoNotFoundError):
            service.detail(BVID)
    finally:
        service.shutdown()


def test_service_list_sorted_by_created_at_desc(tmp_path):
    service = VideoService(
        root=tmp_path / "videos", config=PipelineConfig(crawl_seconds=30.0)
    )
    try:
        service.start()
        service.submit(BVID)
        service.submit(BVID_OTHER)
        videos = service.list_videos()["videos"]
        assert [item["bvid"] for item in videos] == [BVID_OTHER, BVID]
    finally:
        service.shutdown()


def test_retry_during_run_eventually_reprocesses(tmp_path):
    """运行期间 retry：旧那一轮（旧 run_id）放弃，任务最终仍被跑完。"""
    service = VideoService(
        root=tmp_path / "videos",
        config=PipelineConfig(
            crawl_seconds=0.3, clean_seconds=0.1, sentiment_seconds=0.1, stats_seconds=0.1
        ),
    )
    try:
        service.start()
        service.submit(BVID)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            task = service.store.get_task(BVID)
            if task and task["progress"] >= 10:
                break
            time.sleep(0.005)

        service.retry(BVID)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            task = service.store.get_task(BVID)
            if task and task["status"] == "completed":
                break
            time.sleep(0.01)
        assert service.store.get_task(BVID)["status"] == "completed"
    finally:
        service.shutdown()
