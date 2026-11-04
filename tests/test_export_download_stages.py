"""导出端点：数据项目三阶段 CSV、视频任务 crawl/clean CSV。

契约见 `docs/superpowers/specs/2026-11-04-export-and-wordcloud-design.md` 3.1 / 3.2：

- ``GET /api/artifacts/{id}/download?stage=initial|clean|excluded`` 原样返回落盘 CSV；
- ``GET /api/videos/{bvid}/download?stage=crawl|clean`` 由阶段 JSON 实时转 CSV；
- 统一 UTF-8 BOM、``Content-Disposition: attachment``、``Cache-Control: no-store``；
- ``stage`` / ``bvid`` / 项目 id 全部白名单或既有校验，不接受任何路径。
"""

from __future__ import annotations

import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

from src.artifacts import service as artifacts_service
from src.artifacts.config import ArtifactsConfig
from src.artifacts.service import ArtifactService
from src.inference.app import app
from src.videos import service as video_service
from src.videos import store as video_store

#: 测试用视频 BV 号（形态合法）
BVID = "BV1xx411c7mD"


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return TestClient(app)


def _write_stage(store: video_store.VideoStore, bvid: str, stage: str, records: list[dict]) -> None:
    store.write_stage(
        bvid, stage, {"bvid": bvid, "mode": "real", "comment_total": len(records), "records": records}
    )


def _new_task(bvid: str, *, status: str = "completed", seq: int = 1) -> dict:
    """构造一条任务索引记录（只为导出接口服务，不需要后台线程）。"""
    return {
        "bvid": bvid,
        "title": "t",
        "status": status,
        "progress": 100 if status == "completed" else 0,
        "message": "分析完成",
        "comment_count": 2,
        "created_at": "2026-11-04T10:00:00+08:00",
        "updated_at": "2026-11-04T10:00:00+08:00",
        "run_id": 1,
        "seq": seq,
    }


@pytest.fixture
def video_task(tmp_path):
    """一个已完成任务，crawl / clean 阶段产物都已落盘。"""
    service = video_service.VideoService(root=tmp_path / "videos")
    video_service.set_service(service)
    # 用服务自己的 store 写任务（VideoService.store 是独立实例，没有 load 索引，
    # 另起一个 VideoStore 写 index.json 它读不到）。
    store = service.store
    store.upsert_task(_new_task(BVID))
    record = {
        "bvid": BVID,
        "aid": "1",
        "video_title": "标题",
        "rpid": "9",
        "level": 1,
        "root_rpid": "",
        "parent_rpid": "",
        "text": "清洗后文本",
        "likes": 3,
        "reply_count": 1,
        "ctime_utc": "2026-11-01T00:00:00+00:00",
        "collected_at_utc": "2026-11-02T00:00:00+00:00",
    }
    _write_stage(store, BVID, "crawl", [record])
    _write_stage(store, BVID, "clean", [{**record, "raw_text": "清洗前原文"}])
    yield BVID
    service.shutdown()
    video_service.set_service(None)


def _read_csv(response) -> tuple[list[str], list[dict]]:
    """断言响应是附件 CSV 并返回 (表头, 数据行)。"""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    text = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    return text.splitlines()[0].split(","), rows


def _make_csv(rows: list[tuple[str, str]]) -> bytes:
    """把 ``[(comment_id, text)]`` 编码为带 BOM 的上传 CSV。"""
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
# 视频任务导出
# --------------------------------------------------------------------------- #
def test_video_crawl_export_has_crawler_columns(client, video_task):
    header, rows = _read_csv(client.get(f"/api/videos/{video_task}/download?stage=crawl"))
    assert header == [
        "bvid",
        "aid",
        "video_title",
        "rpid",
        "level",
        "root_rpid",
        "parent_rpid",
        "text",
        "likes",
        "reply_count",
        "ctime_utc",
        "collected_at_utc",
    ]
    assert len(rows) == 1
    assert rows[0]["text"] == "清洗后文本"


def test_video_clean_export_appends_raw_text(client, video_task):
    header, rows = _read_csv(client.get(f"/api/videos/{video_task}/download?stage=clean"))
    assert header[-1] == "raw_text"
    assert rows[0]["raw_text"] == "清洗前原文"


def test_video_export_rejects_bad_stage_and_unknown_task(client, video_task):
    assert client.get(f"/api/videos/{video_task}/download?stage=analyze").status_code == 400
    assert client.get("/api/videos/BV1xxxxxxxxx/download?stage=crawl").status_code == 404


def test_video_export_rejects_malformed_bvid(client, video_task):
    """bvid 形态非法 → 400（契约 3.2：不接受任意路径 / 文件名）。"""
    assert client.get("/api/videos/not-a-bvid/download?stage=crawl").status_code == 400


def test_video_export_stage_not_ready_returns_404(client, video_task):
    """任务存在但该阶段产物尚未生成 → 404（中文提示）。"""
    store = video_service.get_service().store
    other = "BV1yy411c7mE"
    store.upsert_task(_new_task(other, status="queued", seq=2))
    response = client.get(f"/api/videos/{other}/download?stage=crawl")
    assert response.status_code == 404
    assert "尚未" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 数据项目导出
# --------------------------------------------------------------------------- #
def test_artifact_export_returns_stage_csv(artifacts_client):
    client, service = artifacts_client
    project = _upload(client)
    response = client.get(f"/api/artifacts/{project['id']}/download?stage=initial")
    header, rows = _read_csv(response)
    assert "text" in header
    assert rows
    # 原样字节返回（含 BOM），不是重新序列化
    assert response.content == service.store.stage_path(project["id"], "initial").read_bytes()


def test_artifact_export_clean_and_excluded_after_completion(artifacts_client):
    client, service = artifacts_client
    project = _upload(client)
    _wait_completed(client, project["id"])
    directory = service.store.project_dir(project["id"])
    for stage in ("initial", "clean", "excluded"):
        response = client.get(f"/api/artifacts/{project['id']}/download?stage={stage}")
        header, _ = _read_csv(response)
        assert header, stage
        assert (directory / f"{stage}.csv").read_bytes()[:3] == b"\xef\xbb\xbf"
        assert response.content == (directory / f"{stage}.csv").read_bytes()


def test_artifact_export_rejects_bad_stage_and_unknown_project(artifacts_client):
    client, _ = artifacts_client
    project = _upload(client)
    assert client.get(f"/api/artifacts/{project['id']}/download?stage=bad").status_code == 400
    # 项目 id 形态非法 → 400（契约 3.1：不接受任意路径 / 文件名）
    assert client.get("/api/artifacts/not-a-project/download?stage=clean").status_code == 400
    # 形态合法但那不存在的项目 → 404
    assert client.get("/api/artifacts/p_20260101_0000_abcdef/download").status_code == 404


def test_artifact_export_stage_not_ready_returns_404(artifacts_client):
    """阶段文件不存在 → 404（中文提示），而不是 200 空文件或 500。"""
    client, service = artifacts_client
    project = _upload(client)
    _wait_completed(client, project["id"])
    (service.store.project_dir(project["id"]) / "excluded.csv").unlink()
    response = client.get(f"/api/artifacts/{project['id']}/download?stage=excluded")
    assert response.status_code == 404
    assert "尚未" in response.json()["detail"]


def test_attachment_headers_are_rfc5987_safe():
    """文件名头：ASCII 兜底名 + UTF-8 filename*，且禁止缓存与嗅探。"""
    from src.inference.downloads import attachment_headers

    headers = attachment_headers("演示项目_clean.csv")
    disposition = headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    # 头注入防护：换行 / 路径分隔符不得原样进入头部
    malicious = attachment_headers('a\r\nX-Evil: 1/../b.csv')
    assert "\r" not in malicious["Content-Disposition"]
    assert "\n" not in malicious["Content-Disposition"]
