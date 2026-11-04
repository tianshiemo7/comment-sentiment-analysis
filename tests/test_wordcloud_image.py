"""词云词量与 Python 渲染词云图端点。

契约见 `docs/superpowers/specs/2026-11-04-export-and-wordcloud-design.md` 3.3 / 3.4：

- 视频任务每侧 60 个词、数据项目每侧 60 个词；
- ``GET /api/videos/{bvid}/wordcloud-image?tone=positive|negative`` 与
  ``GET /api/artifacts/{id}/wordcloud-image?tone=...`` 用 ``wordcloud`` 库按真实词频
  渲染 PNG，缓存到任务 / 项目目录，源文件 mtime 更新后重渲染；
- ``wordcloud`` 未安装 → 503（detail 带安装指引）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from src.artifacts import service as artifacts_service
from src.artifacts.config import ArtifactsConfig
from src.artifacts.service import ArtifactService
from src.inference.app import app
from src.videos import service as video_service

#: 测试用视频 BV 号（形态合法）
BVID = "BV1xx411c7mD"

#: 本机是否安装了 wordcloud（没有时跳过真实渲染用例，端点降级用例仍可跑）
WORDCLOUD_AVAILABLE = importlib.util.find_spec("wordcloud") is not None

#: 词云端点需要的充足关键词（>= 2 个）
VIDEO_KEYWORDS = {
    "keywords": {
        "positive": [
            {"word": "讲解", "count": 10, "weight": 1.0},
            {"word": "清晰", "count": 6, "weight": 0.6},
            {"word": "实用", "count": 3, "weight": 0.3},
        ],
        "negative": [],
    }
}


# --------------------------------------------------------------------------- #
# 词量（Task 1）
# --------------------------------------------------------------------------- #
def test_keyword_limits_are_sixty():
    """契约：视频任务每侧最多 60 个词，数据项目每侧最多 60 个词。"""
    from src.artifacts import pipeline
    from src.videos import placeholder

    assert placeholder.KEYWORDS_PER_SIDE == 60
    assert pipeline.KEYWORD_LIMIT == 60


def test_video_real_keywords_return_sixty_when_enough_words():
    """真实文本词频足够时，视频侧关键词返回满 60 个且按频次降序。"""
    from src.videos.placeholder import _keywords_from_texts

    # 用整词（ASCII）构造足量互不相同的 token：jieba 不会把它们拆开，
    # 每个词在文本里各出现 2 次，词频相同 → 排序稳定、可断言长度。
    words = [f"keyword{i}" for i in range(80)]
    text = " ".join(words * 2)
    picked = _keywords_from_texts([text])
    assert len(picked) == 60
    assert picked[0]["count"] >= picked[-1]["count"]


def test_artifact_keywords_return_sixty_when_enough_words():
    """数据项目侧词频足够时同样返回满 60 个（KEYWORD_LIMIT 契约）。"""
    from src.artifacts.pipeline import _build_keywords

    tokens = " ".join(f"keyword{i}" for i in range(80))
    rows = [{"tokens": tokens} for _ in range(2)]
    predictions = [{"label": "positive"}, {"label": "positive"}]
    result = _build_keywords(rows, predictions)
    assert len(result["positive"]) == 60
    assert result["positive"][0]["count"] >= result["positive"][-1]["count"]


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def video_task(tmp_path):
    """一个已完成任务（只有索引记录；词云图数据来自 detail 文件）。"""
    service = video_service.VideoService(root=tmp_path / "videos")
    video_service.set_service(service)
    service.store.upsert_task(
        {
            "bvid": BVID,
            "title": "t",
            "status": "completed",
            "progress": 100,
            "message": "分析完成",
            "comment_count": 2,
            "created_at": "2026-11-04T10:00:00+08:00",
            "updated_at": "2026-11-04T10:00:00+08:00",
            "run_id": 1,
            "seq": 1,
        }
    )
    yield BVID
    service.shutdown()
    video_service.set_service(None)


def _make_csv(rows: list[tuple[str, str]]) -> bytes:
    """把 ``[(comment_id, text)]`` 编码为带 BOM 的上传 CSV。"""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["comment_id", "text"])
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def _upload(client: TestClient) -> dict:
    """上传一份含正负样本的 CSV，返回项目对象。"""
    data = _make_csv(
        [
            ("p1", "这个商品质量很好，物流也快，非常满意。"),
            ("p2", "质量太差了，用了一天就坏了，非常失望。"),
            ("p3", "客服态度特别好，问题很快就解决了。"),
        ]
    )
    response = client.post(
        "/api/artifacts/upload", files={"files": ("demo.csv", data, "text/csv")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_completed(client: TestClient, project_id: str, timeout: float = 15.0) -> dict:
    """轮询项目直到 ``completed``（0 秒阶段配置下通常毫秒级完成）。"""
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/artifacts/{project_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] == "completed":
            return body
        time.sleep(0.02)
    pytest.fail(f"项目未在 {timeout}s 内完成：{body}")


@pytest.fixture
def artifacts_client(tmp_path):
    """0 秒阶段配置：上传后立刻能跑到 completed（与 test_artifacts_api.py 同款）。"""
    service = ArtifactService(
        root=tmp_path / "artifacts-fast",
        config=ArtifactsConfig(clean_seconds=0.0, sentiment_seconds=0.0, stats_seconds=0.0),
    )
    artifacts_service.set_service(service)
    with TestClient(app) as client:
        yield client, service


# --------------------------------------------------------------------------- #
# 渲染函数
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_render_frequencies_to_png(tmp_path):
    """渲染出合法 PNG，且不留 ``.tmp.png`` 半截文件（原子写盘）。"""
    from src.visualization.wordclouds import render_frequencies_to_png

    path = render_frequencies_to_png(
        {"讲解": 20, "清晰": 12, "实用": 8}, tmp_path / "a.png", tone="positive"
    )
    assert path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "a.tmp.png").exists() is False


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_render_frequencies_requires_enough_words(tmp_path):
    """有效词不足 2 个 → ``ValueError``（服务层据此返回 404）。"""
    from src.visualization.wordclouds import render_frequencies_to_png

    with pytest.raises(ValueError):
        render_frequencies_to_png({"讲解": 3}, tmp_path / "a.png")


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_render_same_path_concurrently(tmp_path):
    """10 线程对同一 out_path 冷缓存并发渲染：无异常、最终 PNG 完整可解码。

    回归 QA 发现的 M1：固定临时名 ``<stem>.tmp.png`` 在 Windows 上被并发线程
    同时写入 / 替换，抛 ``PermissionError``（WinError 32）导致接口 500。
    """
    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image

    from src.visualization.wordclouds import render_frequencies_to_png

    target = tmp_path / "wordcloud_positive.png"
    frequencies = {"讲解": 20, "清晰": 12, "实用": 8, "流畅": 6, "推荐": 4}
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            render_frequencies_to_png(frequencies, target, tone="positive")
        except BaseException as exc:  # noqa: BLE001 - 收集线程内异常用于断言
            errors.append(exc)

    for _ in range(2):  # 两轮：每轮先删目标，模拟冷缓存下的并发首渲染
        target.unlink(missing_ok=True)
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda _: worker(), range(10)))
        assert errors == [], errors
        assert target.is_file()
        assert target.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(target) as image:  # 完整解码，确认不是半截 / 损坏文件
            assert image.size == (960, 600)
            image.load()
    # 唯一临时文件都已清理：目录里只剩最终 PNG
    assert [path.name for path in tmp_path.iterdir()] == ["wordcloud_positive.png"]


def test_replace_with_retry_recovers_from_permission_error(tmp_path, monkeypatch):
    """Windows 目标被占用时 ``os.replace`` 有限重试，前两次失败后第 3 次成功。"""
    from src.visualization import wordclouds

    tmp = tmp_path / "x.abcdef.png"
    tmp.write_bytes(b"png-bytes")
    target = tmp_path / "x.png"
    real_replace = os.replace
    calls: list[int] = []

    def flaky_replace(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "文件被另一个进程占用")
        return real_replace(src, dst)

    monkeypatch.setattr(wordclouds.os, "replace", flaky_replace)
    wordclouds._replace_with_retry(tmp, target)
    assert target.read_bytes() == b"png-bytes"
    assert len(calls) == 3
    assert not tmp.exists()


# --------------------------------------------------------------------------- #
# 视频任务词云图
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_video_wordcloud_image_uses_cache(client, video_task):
    """首次按需渲染，第二次命中缓存（mtime 不变、字节相同）。"""
    store = video_service.get_service().store
    store.write_detail(video_task, VIDEO_KEYWORDS)
    first = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    assert first.content[:8] == b"\x89PNG\r\n\x1a\n"
    target = store.stage_dir(video_task).joinpath("wordcloud_positive.png")
    mtime = target.stat().st_mtime_ns
    second = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert second.status_code == 200
    assert second.content == first.content
    assert target.stat().st_mtime_ns == mtime


def test_video_wordcloud_image_validation(client, video_task):
    """tone 非白名单 → 400；任务不存在 → 404。"""
    assert client.get(f"/api/videos/{video_task}/wordcloud-image?tone=neutral").status_code == 400
    assert client.get("/api/videos/BV1xxxxxxxxx/wordcloud-image").status_code == 404


def test_video_wordcloud_image_requires_keywords(client, video_task):
    """该情感没有足够关键词（< 2 个）→ 404 中文提示。"""
    store = video_service.get_service().store
    store.write_detail(
        video_task,
        {"keywords": {"positive": [{"word": "讲解", "count": 10, "weight": 1.0}], "negative": []}},
    )
    response = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert response.status_code == 404
    assert "关键词" in response.json()["detail"]


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_video_wordcloud_image_download_is_attachment(client, video_task):
    """``download=true`` → ``Content-Disposition: attachment``，默认 inline。"""
    store = video_service.get_service().store
    store.write_detail(video_task, VIDEO_KEYWORDS)
    inline = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert inline.headers["content-disposition"].startswith("inline")
    attachment = client.get(
        f"/api/videos/{video_task}/wordcloud-image?tone=positive&download=true"
    )
    assert attachment.status_code == 200
    assert attachment.headers["content-disposition"].startswith("attachment")


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_video_wordcloud_image_rerenders_when_detail_is_newer(client, video_task):
    """detail 文件 mtime 晚于 PNG → 缓存失效重渲染。"""
    store = video_service.get_service().store
    store.write_detail(video_task, VIDEO_KEYWORDS)
    assert client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive").status_code == 200
    target = store.stage_dir(video_task).joinpath("wordcloud_positive.png")
    old_mtime = target.stat().st_mtime_ns
    # 把源文件 mtime 推到未来，模拟「重新分析后 detail 更新」
    source = store.detail_path(video_task)
    future = target.stat().st_mtime_ns + 10**10
    os.utime(source, ns=(future, future))
    assert client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive").status_code == 200
    assert target.stat().st_mtime_ns != old_mtime


def test_video_wordcloud_image_missing_wordcloud_returns_503(client, video_task, monkeypatch):
    """wordcloud 未安装 → 503，detail 带 ``pip install wordcloud`` 指引。"""
    store = video_service.get_service().store
    store.write_detail(video_task, VIDEO_KEYWORDS)
    monkeypatch.setitem(sys.modules, "wordcloud", None)
    response = client.get(f"/api/videos/{video_task}/wordcloud-image?tone=positive")
    assert response.status_code == 503
    assert "pip install wordcloud" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 数据项目词云图
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_artifact_wordcloud_image_uses_cache(artifacts_client):
    """数据项目按 ``analysis.json.keywords`` 渲染并缓存到项目目录。"""
    client, service = artifacts_client
    project = _upload(client)
    _wait_completed(client, project["id"])
    service.store.write_artifact_json(
        project["id"],
        "analysis.json",
        {"method": "rule_placeholder", "total": 6, "keywords": VIDEO_KEYWORDS["keywords"]},
    )
    first = client.get(f"/api/artifacts/{project['id']}/wordcloud-image?tone=positive")
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/png"
    assert first.content[:8] == b"\x89PNG\r\n\x1a\n"
    target = service.store.project_dir(project["id"]) / "wordcloud_positive.png"
    mtime = target.stat().st_mtime_ns
    second = client.get(f"/api/artifacts/{project['id']}/wordcloud-image?tone=positive")
    assert second.status_code == 200
    assert second.content == first.content
    assert target.stat().st_mtime_ns == mtime


def test_artifact_wordcloud_image_validation(artifacts_client):
    """tone 非白名单 → 400；项目不存在 → 404；没有足够关键词 → 404。"""
    client, service = artifacts_client
    project = _upload(client)
    assert (
        client.get(f"/api/artifacts/{project['id']}/wordcloud-image?tone=neutral").status_code
        == 400
    )
    assert (
        client.get("/api/artifacts/p_20260101_0000_zzzzzz/wordcloud-image").status_code == 404
    )
    _wait_completed(client, project["id"])
    service.store.write_artifact_json(
        project["id"], "analysis.json", {"keywords": {"positive": [], "negative": []}}
    )
    response = client.get(f"/api/artifacts/{project['id']}/wordcloud-image?tone=negative")
    assert response.status_code == 404
    assert "关键词" in response.json()["detail"]


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_artifact_wordcloud_image_download_is_attachment(artifacts_client):
    client, service = artifacts_client
    project = _upload(client)
    _wait_completed(client, project["id"])
    service.store.write_artifact_json(
        project["id"],
        "analysis.json",
        {"keywords": {"positive": [{"word": "好用", "count": 5}, {"word": "清晰", "count": 2}]}},
    )
    response = client.get(
        f"/api/artifacts/{project['id']}/wordcloud-image?tone=positive&download=true"
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


# --------------------------------------------------------------------------- #
# 旧任务词云兼容回填（词量 8 → 60 后已完成任务没有重新分析入口）
# --------------------------------------------------------------------------- #
#: 回填用词池：常用中文整词，jieba 分词后不会被拆碎、也不在停用词表里
BACKFILL_WORDS = (
    "讲解", "清晰", "实用", "满意", "好用", "推荐", "精彩", "流畅", "专业", "细致",
    "易懂", "到位", "丰富", "自然", "稳定", "有趣", "准确", "通俗", "干净", "完整",
)


def _keyword_items(words) -> list[dict]:
    """构造一份旧版 detail 的 keywords 列表（count 降序、weight 归一）。"""
    total = len(words)
    return [
        {"word": word, "count": total - index, "weight": round((total - index) / total, 3)}
        for index, word in enumerate(words)
    ]


def _write_old_task(service, bvid: str, *, keywords: dict, with_stages: bool) -> None:
    """写一个「已完成但 detail 是旧词量」的任务；可选写入 clean / analyze 阶段产物。"""
    store = service.store
    store.upsert_task(
        {
            "bvid": bvid,
            "title": "旧任务",
            "status": "completed",
            "progress": 100,
            "message": "分析完成",
            "comment_count": 40,
            "created_at": "2026-11-01T10:00:00+08:00",
            "updated_at": "2026-11-01T10:00:00+08:00",
            "run_id": 1,
            "seq": 1,
        }
    )
    store.write_detail(bvid, {"keywords": keywords, "placeholder": False})
    if not with_stages:
        return
    text = " ".join(BACKFILL_WORDS)
    records = [{"bvid": bvid, "rpid": str(index), "text": text} for index in range(40)]
    predictions = [
        {"label": "positive" if index % 2 == 0 else "negative", "confidence": 0.9}
        for index in range(40)
    ]
    store.write_stage(bvid, "clean", {"bvid": bvid, "records": records, "comment_total": 40})
    store.write_stage(bvid, "analyze", {"bvid": bvid, "predictions": predictions})


@pytest.mark.skipif(not WORDCLOUD_AVAILABLE, reason="wordcloud 未安装")
def test_detail_backfills_old_keywords_from_stage_products(client, tmp_path):
    """旧任务（每侧 3 词）+ clean / analyze 产物 → 详情词云按已落盘产物回填到新词量。

    回填只用历史产物（不重新推理），且**不写回** ``<bvid>.json``。
    """
    service = video_service.VideoService(root=tmp_path / "videos-backfill")
    video_service.set_service(service)
    bvid = "BV1backfill1"
    try:
        _write_old_task(
            service,
            bvid,
            keywords={
                "positive": _keyword_items(BACKFILL_WORDS[:3]),
                "negative": _keyword_items(BACKFILL_WORDS[3:6]),
            },
            with_stages=True,
        )
        detail = client.get(f"/api/videos/{bvid}/detail")
        assert detail.status_code == 200, detail.text
        keywords = detail.json()["keywords"]
        for tone in ("positive", "negative"):
            assert 3 < len(keywords[tone]) <= 60, tone
        # 只读：落盘 detail 仍是旧词量
        stored = service.store.read_detail(bvid) or {}
        assert len(stored["keywords"]["positive"]) == 3
        # 词云图端点走同一份回填词，能出 PNG
        image = client.get(f"/api/videos/{bvid}/wordcloud-image?tone=positive")
        assert image.status_code == 200, image.text
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        service.shutdown()
        video_service.set_service(None)


def test_detail_old_task_without_stages_keeps_original_keywords(client, tmp_path):
    """没有 clean / analyze 产物的旧任务（占位旧数据）原样返回，不额外计算、不报错。"""
    service = video_service.VideoService(root=tmp_path / "videos-legacy")
    video_service.set_service(service)
    bvid = "BV1legacy111"
    try:
        _write_old_task(
            service,
            bvid,
            keywords={
                "positive": _keyword_items(BACKFILL_WORDS[:8]),
                "negative": _keyword_items(BACKFILL_WORDS[8:16]),
            },
            with_stages=False,
        )
        detail = client.get(f"/api/videos/{bvid}/detail")
        assert detail.status_code == 200, detail.text
        keywords = detail.json()["keywords"]
        assert len(keywords["positive"]) == 8
        assert len(keywords["negative"]) == 8
    finally:
        service.shutdown()
        video_service.set_service(None)


def test_effective_keywords_memoized_by_stage_mtime(client, tmp_path, monkeypatch):
    """回填按阶段产物 mtime 记忆化：第二次调用不重新分词，mtime 变化后失效重算。"""
    from src.videos import placeholder

    service = video_service.VideoService(root=tmp_path / "videos-memo")
    video_service.set_service(service)
    bvid = "BV1memoize11"
    try:
        _write_old_task(
            service,
            bvid,
            keywords={
                "positive": _keyword_items(BACKFILL_WORDS[:3]),
                "negative": _keyword_items(BACKFILL_WORDS[3:6]),
            },
            with_stages=True,
        )
        calls: list[int] = []
        real_build = placeholder._build_real_keywords

        def counting(comments, predictions):
            calls.append(1)
            return real_build(comments, predictions)

        monkeypatch.setattr(placeholder, "_build_real_keywords", counting)
        stored = service.store.read_detail(bvid) or {}

        first = service._effective_keywords(bvid, stored)
        second = service._effective_keywords(bvid, stored)
        assert calls == [1], "第二次调用应命中进程内缓存，不再重新分词"
        assert first == second
        assert 3 < len(first["positive"]) <= 60

        # 阶段产物 mtime 变化（重新清洗 / 分析）→ 缓存失效，重算但结果一致
        clean_path = service.store.stage_path(bvid, "clean")
        future = clean_path.stat().st_mtime_ns + 10**10
        os.utime(clean_path, ns=(future, future))
        third = service._effective_keywords(bvid, stored)
        assert len(calls) == 2
        assert third == first
        # 只读：落盘 detail 仍是旧词量
        assert len((service.store.read_detail(bvid) or {})["keywords"]["positive"]) == 3
    finally:
        service.shutdown()
        video_service.set_service(None)
