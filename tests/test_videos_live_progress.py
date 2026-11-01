"""Progress must be observable while crawling/processing, before a stage returns."""
from types import SimpleNamespace

import pytest

import src.crawler
from src.videos import pipeline
from src.videos.config import PipelineConfig
from src.videos.errors import PipelineAborted
from src.videos.store import VideoStore

BVID = "BV1xx411c7mD"


def make_store(tmp_path):
    store = VideoStore(tmp_path / "videos")
    store.upsert_task({"bvid": BVID, "run_id": 1, "status": "queued", "progress": 0})
    return store


def test_real_pipeline_reports_inflight_counts_and_predicts_each_comment_once(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    rows = [{"text": f"讲解非常清晰，例子{i}很好！", "likes": i, "rpid": str(i)} for i in range(4)]
    inflight = []
    predictions = []
    snapshots = []

    def fetch(bvid, *, on_page, **kwargs):
        assert store.get_task(bvid)["status"] == "crawling"
        for page, count in [(1, 2), (2, 4)]:
            on_page(SimpleNamespace(bvid=bvid), page, 2, count)
            inflight.append(store.get_task(bvid))
        return rows

    def predict(text, model):
        predictions.append(text)
        assert store.get_task(BVID)["status"] == "processing"
        assert "正在分析情感" in store.get_task(BVID)["message"]
        return {"text": text, "label": "positive", "confidence": 0.9,
                "probs": {"positive": 0.9, "negative": 0.1}}

    monkeypatch.setattr(src.crawler, "fetch_video_comments", fetch)
    monkeypatch.setattr(pipeline, "_active_predictor", lambda: None)
    monkeypatch.setattr(pipeline, "_predict_one_text", predict)
    settings = {"account": {"mode": "anonymous"},
                "crawl": {"real_crawl": True, "max_pages": 3},
                "pipeline": {"auto_clean": True, "auto_analyze": True}}
    detail = pipeline.run_pipeline(store, BVID, PipelineConfig(0, 0, 0, 0),
                                   task_settings=settings, on_update=snapshots.append)
    assert [item["comment_count"] for item in inflight] == [2, 4]
    assert all(0 < item["progress"] < 40 for item in inflight)
    assert "1 页" in inflight[0]["message"] and "2 条" in inflight[0]["message"]
    assert any("正在清洗评论… 2/4 条" == task.get("message") for task in snapshots)
    assert any("正在分析情感… 2/4 条" == task.get("message") for task in snapshots)
    assert len(predictions) == len(rows)  # Aggregation reuses analyze.json predictions.
    assert detail["sentiment"]["positive"] == 4
    assert {item["text"] for item in detail["top_comments"]["positive"]} == {row["text"] for row in rows}
    values = [task["progress"] for task in snapshots]
    assert values == sorted(values)
    assert all(task["progress"] < 100 for task in snapshots if task["status"] != "completed")
    assert store.get_task(BVID)["status"] == "completed"
    assert store.get_task(BVID)["progress"] == 100


def test_clean_progress_counts_empty_and_duplicate_input_as_processed():
    seen = []
    records, stats = pipeline._clean_comment_records(
        [{"text": "不错！"}, {"text": ""}, {"text": "不错！"}],
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert len(records) == 1 and stats["dropped_empty"] == stats["dropped_duplicate"] == 1


def test_crawl_updates_cannot_overwrite_retried_task(tmp_path):
    store = make_store(tmp_path)
    callback = pipeline._crawl_reporter(store, BVID, 1, None, PipelineConfig().crawl_config(), None)
    store.update_task(BVID, 1, run_id=2, progress=0)
    callback(2, 40)
    assert store.get_task(BVID)["progress"] == 0
    assert store.get_task(BVID)["run_id"] == 2


def test_work_updates_abort_if_task_was_deleted(tmp_path):
    store = make_store(tmp_path)
    callback = pipeline._work_reporter(store, BVID, 1, None, 2, "正在分析情感", None)
    store.remove_task(BVID)
    with pytest.raises(PipelineAborted):
        callback(1, 3)
