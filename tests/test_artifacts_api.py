"""`/api/artifacts` 系列接口契约测试（对照 `docs/API契约.md` 第四点五节）。

设计要点：

- **不真等**：用 0 秒阶段配置注入服务，上传后状态机在毫秒级跑完；
  状态机推进用一个 ``clean_seconds=0.6`` 的慢配置单独测，轮询断言 `progress` 单调不减；
- 用 `TestClient(app)` 作为上下文管理器，触发 lifespan（启动恢复 + 优雅关停），
  这样才能覆盖真实的后台线程行为；
- 断言严格按契约字段名与结构，改名 / 改结构立即可见；
- 覆盖：上传落盘与三张表行列数、分页边界、label 筛选、analysis 的 method 与 ratio、
  open 的成功与非法 stage、**目录穿越被拒**、删除、失败分支、左栏三段。
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.artifacts import service as artifacts_service
from src.artifacts.config import ArtifactsConfig
from src.artifacts.schemas import (
    ALLOWED_SUFFIXES,
    MAX_FILE_BYTES,
    METHOD_RULE_PLACEHOLDER,
    PROJECT_ID_RE,
)
from src.artifacts.service import ArtifactService
from src.inference.app import app

#: 项目对象字段（契约 4.5.1，一个不多一个不少）
PROJECT_FIELDS = {
    "id",
    "name",
    "created_at",
    "status",
    "progress",
    "message",
    "sources",
    "stages",
    "sentiment",
    "method",
    "error",
}

#: 阶段项字段（契约 4.5.1）
STAGE_FIELDS = {"key", "label", "rows", "columns"}

#: 逐条评论字段（契约 4.5.1）
COMMENT_FIELDS = {"index", "text", "label", "confidence", "probs"}

#: 演示数据：3 正 / 2 负 / 1 中性 + 1 条空白（排除）+ 1 条重复 comment_id（排除）
SAMPLE_ROWS = [
    ("p1", "这个商品质量很好，物流也快，非常满意。"),
    ("p2", "质量太差了，用了一天就坏了，非常失望。"),
    ("p3", "客服态度特别好，问题很快就解决了。"),
    ("p4", "发货太慢，客服不理人，差评。"),
    ("p5", "教程讲解清晰，看完就学会了。"),
    ("p6", "今天下午三点开组会。"),
    ("p7", "   "),
    ("p1", "这个商品质量很好，物流也快，非常满意。"),
]

#: 上面的期望计数（清洗后保留 6 条：3 正 2 负 1 中性；排除 2 条）
EXPECTED_INITIAL_ROWS = 8
EXPECTED_CLEAN_ROWS = 6
EXPECTED_EXCLUDED_ROWS = 2
EXPECTED_POSITIVE = 3
EXPECTED_NEGATIVE = 2
EXPECTED_NEUTRAL = 1


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def make_csv(rows=SAMPLE_ROWS, header=("comment_id", "text")) -> bytes:
    """把行数据编码成带 BOM 的 CSV（与前端上传的爬虫产物一致）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(header))
    for row in rows:
        writer.writerow(list(row))
    return buffer.getvalue().encode("utf-8-sig")


def upload(client: TestClient, data: bytes | None = None, *, filename="demo.csv", name=None):
    """上传一个文件，返回 `(response, 项目对象或 None)`。"""
    files = [("files", (filename, make_csv() if data is None else data, "text/csv"))]
    payload = {"name": name} if name is not None else None
    response = client.post("/api/artifacts/upload", files=files, data=payload)
    return response, (response.json() if response.status_code == 200 else None)


def upload_ok(client: TestClient, data: bytes | None = None, **kwargs) -> dict:
    """上传并断言 200，返回项目对象。"""
    response, _ = upload(client, data, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def wait_for(client: TestClient, project_id: str, statuses=("completed",), timeout=15.0) -> dict:
    """轮询项目直到进入目标状态（返回最后一次项目对象）。"""
    deadline = time.monotonic() + timeout
    body: dict = {}
    history: list[int] = []
    while time.monotonic() < deadline:
        response = client.get(f"/api/artifacts/{project_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        history.append(body["progress"])
        if body["status"] in statuses:
            body["_progress_history"] = history
            return body
        time.sleep(0.02)
    pytest.fail(f"项目未在 {timeout}s 内进入 {statuses}：{body}")


def project_dir(service: ArtifactService, project_id: str) -> Path:
    """项目落盘目录（测试里直接读文件用）。"""
    return service.store.project_dir(project_id)


@pytest.fixture
def artifacts_client(tmp_path):
    """0 秒阶段配置：上传后立刻能跑到 completed。"""
    service = ArtifactService(
        root=tmp_path / "artifacts-fast",
        config=ArtifactsConfig(clean_seconds=0.0, sentiment_seconds=0.0, stats_seconds=0.0),
    )
    artifacts_service.set_service(service)
    with TestClient(app) as client:
        yield client, service


@pytest.fixture
def slow_artifacts_client(tmp_path):
    """慢一点的清洗阶段：用于断言状态机推进与「处理中」形态。"""
    service = ArtifactService(
        root=tmp_path / "artifacts-slow",
        config=ArtifactsConfig(clean_seconds=0.6, sentiment_seconds=0.2, stats_seconds=0.2),
    )
    artifacts_service.set_service(service)
    with TestClient(app) as client:
        yield client, service


# --------------------------------------------------------------------------- #
# 上传与落盘
# --------------------------------------------------------------------------- #
def test_upload_returns_queued_project_with_contract_fields(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client, name="演示项目")

    assert set(project) == PROJECT_FIELDS
    assert PROJECT_ID_RE.match(project["id"])
    assert project["name"] == "演示项目"
    assert project["status"] in {"queued", "processing", "completed"}
    assert 0 <= project["progress"] <= 100
    assert project["method"] == METHOD_RULE_PLACEHOLDER
    assert project["error"] is None

    done = wait_for(client, project["id"])
    assert done["status"] == "completed"
    assert done["progress"] == 100
    assert done["message"] == "处理完成"


def test_upload_default_name_and_sources(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    assert project["name"] == "未自定义数据"
    assert len(project["sources"]) == 1
    source = project["sources"][0]
    assert set(source) == {"filename", "rows", "size"}
    assert source["filename"] == "demo.csv"
    assert source["rows"] == EXPECTED_INITIAL_ROWS
    assert source["size"] == len(make_csv())


def test_upload_writes_all_artifact_files(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    directory = project_dir(service, project["id"])

    for name in ("upload.json", "project.json", "initial.csv", "clean.csv", "excluded.csv", "sentiment.csv", "analysis.json"):
        assert (directory / name).is_file(), f"缺少产物 {name}"

    # 上传元数据（契约 4.5.1：原始文件名、大小、sha256、上传时间、显示名）
    upload_meta = json.loads((directory / "upload.json").read_text(encoding="utf-8"))
    assert set(upload_meta) == {"project_id", "name", "uploaded_at", "files"}
    assert upload_meta["project_id"] == project["id"]
    assert upload_meta["files"][0]["filename"] == "demo.csv"
    assert set(upload_meta["files"][0]) == {"filename", "size", "sha256", "rows"}
    assert len(upload_meta["files"][0]["sha256"]) == 64

    # 统一落盘为 UTF-8 BOM 的 CSV
    assert (directory / "initial.csv").read_bytes()[:3] == b"\xef\xbb\xbf"
    assert (directory / "clean.csv").read_bytes()[:3] == b"\xef\xbb\xbf"
    assert (directory / "sentiment.csv").read_bytes()[:3] == b"\xef\xbb\xbf"


def test_stage_row_and_column_counts(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    done = wait_for(client, project["id"])

    stages = done["stages"]
    assert [item["key"] for item in stages] == ["initial", "clean", "excluded"]
    assert [item["label"] for item in stages] == ["初始数据", "筛选后结果", "排除记录"]
    for item in stages:
        assert set(item) == STAGE_FIELDS

    counts = {item["key"]: item for item in stages}
    assert counts["initial"]["rows"] == EXPECTED_INITIAL_ROWS
    assert counts["clean"]["rows"] == EXPECTED_CLEAN_ROWS
    assert counts["excluded"]["rows"] == EXPECTED_EXCLUDED_ROWS

    # columns 必须等于该表真实列数（不含表头）
    directory = project_dir(service, project["id"])
    for key in ("initial", "clean", "excluded"):
        with (directory / f"{key}.csv").open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle))
        assert counts[key]["columns"] == len(header) > 0

    # excluded 表必须有排除原因列（契约 4.5.1）
    with (directory / "excluded.csv").open(encoding="utf-8-sig", newline="") as handle:
        excluded_header = next(csv.reader(handle))
    assert "exclude_reason" in excluded_header


def test_stages_endpoint_matches_project_object(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    response = client.get(f"/api/artifacts/{project['id']}/stages")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stages"}
    assert [item["key"] for item in body["stages"]] == ["initial", "clean", "excluded"]

    assert client.get("/api/artifacts/p_20000101_0000_abcdef/stages").status_code == 404


def test_sentiment_csv_is_rule_placeholder(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    path = project_dir(service, project["id"]) / "sentiment.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == EXPECTED_CLEAN_ROWS
    assert {row["method"] for row in rows} == {METHOD_RULE_PLACEHOLDER}
    assert {row["label"] for row in rows} <= {"positive", "negative", "neutral"}
    for row in rows:
        assert 0.0 <= float(row["confidence"]) <= 1.0
        assert 0.0 <= float(row["prob_positive"]) <= 1.0
        assert 0.0 <= float(row["prob_negative"]) <= 1.0


def test_multi_file_upload_merges_rows(tmp_path, artifacts_client):
    client, service = artifacts_client
    first = make_csv([("a1", "这个商品质量很好。"), ("a2", "质量太差了。")])
    second = make_csv([("b1", "客服态度特别好。")])
    response = client.post(
        "/api/artifacts/upload",
        files=[
            ("files", ("one.csv", first, "text/csv")),
            ("files", ("two.csv", second, "text/csv")),
        ],
    )
    assert response.status_code == 200, response.text
    project = response.json()
    assert [source["filename"] for source in project["sources"]] == ["one.csv", "two.csv"]
    done = wait_for(client, project["id"])
    assert done["stages"][0]["rows"] == 3
    assert done["sentiment"]["total"] == 3


def test_upload_only_keeps_basename(artifacts_client, tmp_path):
    """上传文件名只取 basename，绝不用于拼接路径。"""
    client, service = artifacts_client
    project = upload_ok(client, filename="../../evil.csv")
    assert project["sources"][0]["filename"] == "evil.csv"

    project2 = upload_ok(client, filename="..\\..\\windows\\evil2.csv")
    assert project2["sources"][0]["filename"] == "evil2.csv"

    wait_for(client, project["id"])
    directory = project_dir(service, project["id"])
    # 项目目录里只有固定产物名，没有任何用户文件名
    assert {path.name for path in directory.iterdir()} == {
        "upload.json", "project.json", "initial.csv", "clean.csv",
        "excluded.csv", "sentiment.csv", "analysis.json",
    }
    # 根目录下没有被写出去的文件
    assert {path.name for path in service.store.root.iterdir() if path.is_file()} <= {"solo_comments.json"}


# --------------------------------------------------------------------------- #
# 上传错误分支
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("filename,payload", [
    ("bad.exe", b"a,b\n1,2\n"),
    ("bad.json", b"{}"),
])
def test_upload_rejects_unsupported_suffix(artifacts_client, filename, payload):
    client, _ = artifacts_client
    response, _ = upload(client, payload, filename=filename)
    assert response.status_code == 400
    assert "不支持的文件格式" in response.json()["detail"]


def test_upload_rejects_empty_file(artifacts_client):
    client, _ = artifacts_client
    response, _ = upload(client, b"", filename="empty.csv")
    assert response.status_code == 400
    assert "为空" in response.json()["detail"]


def test_upload_rejects_oversized_file(artifacts_client):
    client, _ = artifacts_client
    response, _ = upload(client, b"a" * (MAX_FILE_BYTES + 1), filename="big.csv")
    assert response.status_code == 413


def test_upload_rejects_file_without_text_column(artifacts_client):
    client, _ = artifacts_client
    payload = "n1,n2\n1,2\n".encode("utf-8-sig")
    response, _ = upload(client, payload, filename="numbers.csv")
    assert response.status_code == 400
    assert "评论" in response.json()["detail"] or "文本列" in response.json()["detail"]


def test_upload_rejects_empty_body(artifacts_client):
    client, _ = artifacts_client
    response = client.post("/api/artifacts/upload")
    assert response.status_code == 422  # multipart 必填 files 缺失


def test_uploaded_suffixes_are_the_contract_ones():
    assert set(ALLOWED_SUFFIXES) == {".csv", ".txt", ".tsv", ".xlsx"}


# --------------------------------------------------------------------------- #
# 表格分页
# --------------------------------------------------------------------------- #
def test_table_returns_columns_rows_and_contract_keys(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    response = client.get(f"/api/artifacts/{project['id']}/table?stage=initial")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stage", "columns", "rows", "total_rows", "offset", "limit", "encoding"}
    assert body["stage"] == "initial"
    assert body["total_rows"] == EXPECTED_INITIAL_ROWS
    assert body["offset"] == 0
    assert body["limit"] == 100
    assert body["encoding"] == "utf-8-sig"
    assert len(body["rows"]) == EXPECTED_INITIAL_ROWS
    assert all(len(row) == len(body["columns"]) for row in body["rows"])


@pytest.mark.parametrize("stage", ["initial", "clean", "excluded"])
def test_table_each_stage(artifacts_client, stage):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    body = client.get(f"/api/artifacts/{project['id']}/table?stage={stage}").json()
    assert body["stage"] == stage
    expected = {
        "initial": EXPECTED_INITIAL_ROWS,
        "clean": EXPECTED_CLEAN_ROWS,
        "excluded": EXPECTED_EXCLUDED_ROWS,
    }[stage]
    assert body["total_rows"] == expected


def test_table_pagination_boundaries(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    path = f"/api/artifacts/{project['id']}/table"

    # 正常的中间页
    body = client.get(f"{path}?stage=initial&offset=1&limit=2").json()
    assert body["offset"] == 1 and body["limit"] == 2
    assert len(body["rows"]) == 2
    assert body["total_rows"] == EXPECTED_INITIAL_ROWS

    # offset 越界：不报错，返回空 rows，total_rows 仍为真实行数
    body = client.get(f"{path}?stage=initial&offset=9999&limit=10").json()
    assert body["rows"] == []
    assert body["total_rows"] == EXPECTED_INITIAL_ROWS

    # offset 正好等于总行数
    body = client.get(f"{path}?stage=initial&offset={EXPECTED_INITIAL_ROWS}&limit=10").json()
    assert body["rows"] == []

    # limit 恰好等于真实行数
    body = client.get(f"{path}?stage=initial&limit={EXPECTED_INITIAL_ROWS}").json()
    assert len(body["rows"]) == EXPECTED_INITIAL_ROWS

    # limit 超过上限 500 → 400
    response = client.get(f"{path}?stage=initial&limit=501")
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]

    # limit / offset 非法 → 422（由 Query 的 ge 约束拦下）
    assert client.get(f"{path}?stage=initial&limit=0").status_code == 422
    assert client.get(f"{path}?stage=initial&offset=-1").status_code == 422

    # stage 非法（含目录穿越尝试）→ 400
    for bad in ("bogus", "../clean", "clean.csv", "clean/../../etc/passwd"):
        assert client.get(f"{path}?stage={bad}").status_code == 400


def test_table_unknown_project_is_404(artifacts_client):
    client, _ = artifacts_client
    assert client.get("/api/artifacts/p_20000101_0000_abcdef/table?stage=clean").status_code == 404


# --------------------------------------------------------------------------- #
# 逐条评论
# --------------------------------------------------------------------------- #
def test_comments_contract_and_pagination(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    path = f"/api/artifacts/{project['id']}/comments"

    body = client.get(path).json()
    assert set(body) == {"total", "offset", "limit", "items"}
    assert body["total"] == EXPECTED_CLEAN_ROWS
    assert body["limit"] == 50
    assert len(body["items"]) == EXPECTED_CLEAN_ROWS
    for item in body["items"]:
        assert set(item) == COMMENT_FIELDS
        assert item["label"] in {"positive", "negative", "neutral"}
        assert 0.0 <= item["confidence"] <= 1.0
        assert set(item["probs"]) <= {"positive", "negative"}

    # items[].text 取原文
    assert body["items"][0]["text"] == SAMPLE_ROWS[0][1]
    assert [item["index"] for item in body["items"]] == list(range(EXPECTED_CLEAN_ROWS))

    # 分页
    page = client.get(f"{path}?offset=2&limit=2").json()
    assert len(page["items"]) == 2
    assert [item["index"] for item in page["items"]] == [2, 3]

    # offset 越界
    overflow = client.get(f"{path}?offset=99&limit=10").json()
    assert overflow["items"] == []
    assert overflow["total"] == EXPECTED_CLEAN_ROWS

    # limit 越界
    assert client.get(f"{path}?limit=501").status_code == 400
    assert client.get(f"{path}?limit=0").status_code == 422


@pytest.mark.parametrize("label,expected", [
    ("positive", EXPECTED_POSITIVE),
    ("negative", EXPECTED_NEGATIVE),
    ("neutral", EXPECTED_NEUTRAL),
])
def test_comments_label_filter(artifacts_client, label, expected):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    body = client.get(f"/api/artifacts/{project['id']}/comments?label={label}").json()
    assert body["total"] == expected
    assert len(body["items"]) == expected
    assert {item["label"] for item in body["items"]} == {label}


def test_comments_label_filter_matches_analysis_counts(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    analysis = client.get(f"/api/artifacts/{project['id']}/analysis").json()
    for label in ("positive", "negative", "neutral"):
        body = client.get(f"/api/artifacts/{project['id']}/comments?label={label}&limit=500").json()
        assert body["total"] == analysis["sentiment"][label]


def test_comments_rejects_bad_label(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    response = client.get(f"/api/artifacts/{project['id']}/comments?label=angry")
    assert response.status_code == 400
    assert "label" in response.json()["detail"]


def test_comments_unknown_project_is_404(artifacts_client):
    client, _ = artifacts_client
    assert client.get("/api/artifacts/p_20000101_0000_abcdef/comments").status_code == 404


# --------------------------------------------------------------------------- #
# 聚合分析
# --------------------------------------------------------------------------- #
def test_analysis_contract(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    response = client.get(f"/api/artifacts/{project['id']}/analysis")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"method", "total", "sentiment", "keywords", "length", "sample"}
    assert body["method"] == METHOD_RULE_PLACEHOLDER  # 诚实标注：规则占位，不是训练模型
    assert body["total"] == EXPECTED_CLEAN_ROWS

    sentiment = body["sentiment"]
    assert set(sentiment) == {
        "positive", "negative", "neutral",
        "positive_ratio", "negative_ratio", "neutral_ratio",
    }
    assert sentiment["positive"] == EXPECTED_POSITIVE
    assert sentiment["negative"] == EXPECTED_NEGATIVE
    assert sentiment["neutral"] == EXPECTED_NEUTRAL
    assert sentiment["positive"] + sentiment["negative"] + sentiment["neutral"] == body["total"]
    total_ratio = sentiment["positive_ratio"] + sentiment["negative_ratio"] + sentiment["neutral_ratio"]
    assert total_ratio == pytest.approx(1.0, abs=1e-3)

    keywords = body["keywords"]
    assert set(keywords) == {"positive", "negative"}
    for label in ("positive", "negative"):
        assert keywords[label], f"{label} 关键词不应为空"
        for item in keywords[label]:
            assert set(item) == {"word", "count", "weight"}
            assert item["count"] >= 1
            assert 0.0 < item["weight"] <= 1.0

    length = body["length"]
    assert set(length) == {"mean", "median", "p90", "max"}
    assert length["mean"] > 0
    assert length["max"] >= length["p90"] >= 0

    assert 1 <= len(body["sample"]) <= 10
    for item in body["sample"]:
        assert set(item) == {"index", "text", "label", "confidence"}
    assert body["sample"][0]["index"] == 0


def test_analysis_unknown_project_is_404(artifacts_client):
    client, _ = artifacts_client
    assert client.get("/api/artifacts/p_20000101_0000_abcdef/analysis").status_code == 404


def test_analysis_and_table_are_empty_while_processing(slow_artifacts_client):
    """处理中：接口不报错，返回空数据（不编造结果）。"""
    client, _ = slow_artifacts_client
    project = upload_ok(client)
    assert project["status"] in {"queued", "processing"}

    analysis = client.get(f"/api/artifacts/{project['id']}/analysis").json()
    assert analysis["method"] == METHOD_RULE_PLACEHOLDER
    assert analysis["total"] == 0
    assert analysis["sample"] == []
    assert sum(analysis["sentiment"][key] for key in ("positive_ratio", "negative_ratio", "neutral_ratio")) == 0.0

    comments = client.get(f"/api/artifacts/{project['id']}/comments").json()
    assert comments["total"] == 0 and comments["items"] == []

    wait_for(client, project["id"])


# --------------------------------------------------------------------------- #
# 只读取路径 `/path`（无副作用：绝不调用 opener）
# --------------------------------------------------------------------------- #
def test_path_returns_location_without_opening(artifacts_client, artifact_opener_recorder):
    """「复制文件地址」用它拿路径：返回结构正确，且**绝不触发 opener**。"""
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    response = client.get(f"/api/artifacts/{project['id']}/path?stage=clean")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"stage", "path", "exists"}
    assert body["stage"] == "clean"
    assert body["exists"] is True
    assert body["path"].endswith("clean.csv")
    assert Path(body["path"]).is_file()
    assert Path(body["path"]).is_relative_to(service.store.root)

    # 无副作用：一次都没调用 opener（对比 /open 会调用）
    assert artifact_opener_recorder == []
    client.post(f"/api/artifacts/{project['id']}/open", json={"stage": "clean"})
    assert artifact_opener_recorder == [body["path"]]


def test_path_defaults_to_clean_stage(artifacts_client, artifact_opener_recorder):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    body = client.get(f"/api/artifacts/{project['id']}/path").json()
    assert body["stage"] == "clean"
    assert body["path"].endswith("clean.csv")
    assert artifact_opener_recorder == []


@pytest.mark.parametrize("stage", ["initial", "clean", "excluded"])
def test_path_each_stage(artifacts_client, stage):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    body = client.get(f"/api/artifacts/{project['id']}/path?stage={stage}").json()
    assert body["stage"] == stage
    assert body["path"].endswith(f"{stage}.csv")
    assert body["exists"] is True


def test_path_missing_file_reports_exists_false(artifacts_client, artifact_opener_recorder):
    """文件不存在不是错误：返回 200 + exists=false（与 /open 的 400 区分开）。"""
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    (project_dir(service, project["id"]) / "excluded.csv").unlink()

    response = client.get(f"/api/artifacts/{project['id']}/path?stage=excluded")
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["path"].endswith("excluded.csv")
    assert artifact_opener_recorder == []


@pytest.mark.parametrize("stage", ["bogus", "../clean", "../../etc/passwd", "clean.csv", "clean/../project.json"])
def test_path_rejects_illegal_stage(artifacts_client, artifact_opener_recorder, stage):
    """stage 只接受固定枚举：任何路径成分都被 400 拒绝，且不会调用 opener。"""
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    response = client.get(f"/api/artifacts/{project['id']}/path?stage={stage}")
    assert response.status_code == 400, response.text
    assert artifact_opener_recorder == []


def test_path_unknown_project_is_404(artifacts_client, artifact_opener_recorder):
    client, _ = artifacts_client
    assert client.get("/api/artifacts/p_20000101_0000_abcdef/path").status_code == 404
    assert client.get("/api/artifacts/p_20000101_0000_abcdef/path?stage=clean").status_code == 404
    assert artifact_opener_recorder == []


# --------------------------------------------------------------------------- #
# 本机打开 CSV（注入假 opener，绝不真的弹 Excel）
# --------------------------------------------------------------------------- #
def test_open_csv_success(artifacts_client, artifact_opener_recorder):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    response = client.post(f"/api/artifacts/{project['id']}/open", json={"stage": "clean"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"opened", "path"}
    assert body["opened"] is True
    assert body["path"].endswith("clean.csv")
    assert Path(body["path"]).is_file()
    assert artifact_opener_recorder == [body["path"]]
    # 路径必须落在 data/uploads/（测试里是临时目录）之内
    assert Path(body["path"]).is_relative_to(service.store.root)


def test_open_csv_can_omit_body(artifacts_client, artifact_opener_recorder):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    response = client.post(f"/api/artifacts/{project['id']}/open")
    assert response.status_code == 200
    assert response.json()["path"].endswith("clean.csv")


@pytest.mark.parametrize("stage", ["bogus", "../clean", "../../etc/passwd", "clean.csv", "clean/../project.json"])
def test_open_csv_rejects_illegal_stage(artifacts_client, artifact_opener_recorder, stage):
    """stage 只接受固定枚举：任何路径成分都被 400 拒绝，且不会调用 opener。"""
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    response = client.post(f"/api/artifacts/{project['id']}/open", json={"stage": stage})
    assert response.status_code == 400, response.text
    assert artifact_opener_recorder == []


def test_open_csv_unknown_project_is_404(artifacts_client, artifact_opener_recorder):
    client, _ = artifacts_client
    response = client.post("/api/artifacts/p_20000101_0000_abcdef/open", json={"stage": "clean"})
    assert response.status_code == 404
    assert artifact_opener_recorder == []


def test_open_csv_missing_stage_file_is_400(artifacts_client, artifact_opener_recorder):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    (project_dir(service, project["id"]) / "excluded.csv").unlink()
    response = client.post(f"/api/artifacts/{project['id']}/open", json={"stage": "excluded"})
    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]
    assert artifact_opener_recorder == []


def test_open_resolver_blocks_outside_roots_and_non_csv(tmp_path, monkeypatch):
    """路径复核的两个硬条件：已在允许根之内 + 扩展名必须是 .csv。"""
    from src.artifacts.errors import ArtifactValidationError
    from src.artifacts.store import ArtifactStore

    store = ArtifactStore(tmp_path / "uploads")
    project_id = "p_20000101_0000_abcdef"
    store.create_project_dir(project_id)
    csv_path = store.project_dir(project_id) / "clean.csv"
    csv_path.write_text("text\nx\n", encoding="utf-8")

    # 正常情况：解析出来的就是项目里的 clean.csv
    assert store.resolve_open_path(project_id, "clean") == csv_path.resolve()

    # stage 只接受固定枚举：任何路径成分都被拒
    for bad in ("../clean", "clean.csv", "clean/../clean"):
        with pytest.raises(ArtifactValidationError):
            store.resolve_open_path(project_id, bad)

    # 允许根之外 → 拒绝（防御性：即使项目目录被替换成别处的路径）
    outside_csv = tmp_path / "outside.csv"
    outside_csv.write_text("text\nx\n", encoding="utf-8")
    monkeypatch.setattr(store, "stage_path", lambda pid, stage: outside_csv)
    with pytest.raises(ArtifactValidationError, match="data/uploads"):
        store.resolve_open_path(project_id, "clean")

    # 非 .csv 扩展名 → 拒绝
    json_path = store.project_dir(project_id) / "project.json"
    json_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "stage_path", lambda pid, stage: json_path)
    with pytest.raises(ArtifactValidationError, match="只允许打开"):
        store.resolve_open_path(project_id, "clean")


# --------------------------------------------------------------------------- #
# 目录穿越（project_id 与 stage 都不接受路径）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "..%2F..%2Fetc%2Fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "p_20000101_0000_abcdef%2F..%2F..%2Fetc",
    "not-a-project-id",
])
def test_project_id_traversal_is_rejected(artifacts_client, raw):
    client, _ = artifacts_client
    for suffix in ("", "/table?stage=clean", "/comments", "/analysis", "/stages"):
        response = client.get(f"/api/artifacts/{raw}{suffix}")
        assert response.status_code == 404, f"{raw}{suffix} -> {response.status_code}"
    assert client.delete(f"/api/artifacts/{raw}").status_code == 404
    assert client.post(f"/api/artifacts/{raw}/open", json={"stage": "clean"}).status_code == 404


# --------------------------------------------------------------------------- #
# 状态机 / 重新分析 / 失败分支
# --------------------------------------------------------------------------- #
def test_state_machine_progress_is_monotonic(slow_artifacts_client):
    client, _ = slow_artifacts_client
    project = upload_ok(client)
    done = wait_for(client, project["id"], timeout=20.0)
    history = done.pop("_progress_history")
    assert done["status"] == "completed"
    assert history == sorted(history)  # 进度单调不减
    assert history[0] >= 0 and history[-1] == 100
    assert any(0 < value < 100 for value in history)  # 确实经历过中间态


def test_analyze_reruns_and_resets_status(slow_artifacts_client):
    client, service = slow_artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"], timeout=20.0)
    assert client.get(f"/api/artifacts/{project['id']}/analysis").json()["total"] == EXPECTED_CLEAN_ROWS

    response = client.post(f"/api/artifacts/{project['id']}/analyze", json={"mode": "auto"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"queued", "processing"}
    assert body["progress"] < 100
    assert body["error"] is None
    assert body["sentiment"] is None

    done = wait_for(client, project["id"], timeout=20.0)
    assert done["status"] == "completed"
    assert done["stages"][0]["rows"] == EXPECTED_INITIAL_ROWS
    assert client.get(f"/api/artifacts/{project['id']}/analysis").json()["total"] == EXPECTED_CLEAN_ROWS


def test_analyze_accepts_empty_body_and_rejects_train_mode(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    assert client.post(f"/api/artifacts/{project['id']}/analyze").status_code == 200
    wait_for(client, project["id"])

    response = client.post(f"/api/artifacts/{project['id']}/analyze", json={"mode": "train"})
    assert response.status_code == 400
    assert "mode" in response.json()["detail"] or "模式" in response.json()["detail"]

    assert client.post("/api/artifacts/p_20000101_0000_abcdef/analyze").status_code == 404


def test_failed_pipeline_reports_error(artifacts_client):
    """所有文本清洗后为空 → 项目进入 failed 并给出原因（绝不卡在 processing）。"""
    client, _ = artifacts_client
    rows = [("x1", "   "), ("x2", "\t"), ("x3", "")]
    project = upload_ok(client, data=make_csv(rows))
    done = wait_for(client, project["id"], statuses=("failed", "completed"))
    assert done["status"] == "failed"
    assert done["error"]
    assert done["method"] == METHOD_RULE_PLACEHOLDER
    assert done["sentiment"] is None
    assert done["message"].startswith("处理失败")

    # 失败后表格 / 分析接口仍然可用（返回空数据），不报错
    assert client.get(f"/api/artifacts/{project['id']}/table?stage=clean").json()["total_rows"] == 0
    assert client.get(f"/api/artifacts/{project['id']}/analysis").json()["total"] == 0


def test_pipeline_startup_recovery(tmp_path):
    """服务重启：残留的 processing 项目被重置为 queued 并重新跑完。"""
    from src.artifacts import pipeline as artifacts_pipeline

    root = tmp_path / "artifacts-recovery"
    config = ArtifactsConfig(clean_seconds=0.4, sentiment_seconds=0.2, stats_seconds=0.2)
    service = ArtifactService(root=root, config=config)
    artifacts_service.set_service(service)
    project = service.create_project(
        [("demo.csv", make_csv())], name=None
    )
    service.shutdown()  # 模拟进程被杀：只停线程，不清理状态
    assert service.store.get_project(project["id"])["status"] in {"queued", "processing", "completed"}

    with TestClient(app) as client:  # lifespan 触发启动恢复
        done = wait_for(client, project["id"], timeout=20.0)
    assert done["status"] == "completed"
    assert done["stages"][1]["rows"] == EXPECTED_CLEAN_ROWS


# --------------------------------------------------------------------------- #
# 左栏三段 / 删除
# --------------------------------------------------------------------------- #
def test_list_groups_has_three_sections(artifacts_client):
    client, _ = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])

    response = client.get("/api/artifacts")
    assert response.status_code == 200
    groups = response.json()["groups"]
    assert [group["key"] for group in groups] == ["projects", "solo", "videos"]
    assert [group["label"] for group in groups] == ["数据项目", "单独评论", "视频任务"]
    for group in groups:
        assert set(group) == {"key", "label", "items"}

    projects = groups[0]["items"]
    assert [item["id"] for item in projects] == [project["id"]]
    assert set(projects[0]) == PROJECT_FIELDS
    assert groups[1]["items"] == []
    assert isinstance(groups[2]["items"], list)


def test_get_project_unknown_is_404(artifacts_client):
    client, _ = artifacts_client
    assert client.get("/api/artifacts/p_20000101_0000_abcdef").status_code == 404


def test_delete_project_removes_directory(artifacts_client):
    client, service = artifacts_client
    project = upload_ok(client)
    wait_for(client, project["id"])
    directory = project_dir(service, project["id"])
    assert directory.is_dir()

    response = client.delete(f"/api/artifacts/{project['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not directory.exists()

    assert client.get(f"/api/artifacts/{project['id']}").status_code == 404
    assert client.delete(f"/api/artifacts/{project['id']}").status_code == 404


def test_openapi_lists_artifact_paths(artifacts_client):
    client, _ = artifacts_client
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/api/artifacts",
        "/api/artifacts/upload",
        "/api/artifacts/{project_id}",
        "/api/artifacts/{project_id}/analyze",
        "/api/artifacts/{project_id}/stages",
        "/api/artifacts/{project_id}/table",
        "/api/artifacts/{project_id}/comments",
        "/api/artifacts/{project_id}/analysis",
        "/api/artifacts/{project_id}/path",
        "/api/artifacts/{project_id}/open",
        "/api/solo-comments",
        "/api/solo-comments/{comment_id}",
    } <= paths
