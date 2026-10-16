"""批量结果导出与受保护下载路由测试（改进 B）。

覆盖：

- 导出文件名形如 ``batch_prediction_<YYYYMMDD_HHMMSS>_<shortid>.csv``；
- CSV 为 UTF-8 with BOM，表头 ``index,text,label,confidence``；
- ``batch.ensure_safe_csv_name`` / ``resolve_export_path`` 拒绝目录穿越与非 CSV；
- HTTP 全链路：POST 批量预测 → ``export_url`` 非 null → GET 下载成功；
- 不存在的文件 404，穿越尝试 4xx。

导出目录由 ``tests/conftest.py`` 的 autouse fixture 重定向到临时目录，
因此本文件不会往 ``outputs/predictions/`` 里堆文件。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.inference import batch as batch_io
from src.inference.app import app

client = TestClient(app)

CSV_PAYLOAD = (
    "id,text\n"
    "1,这个商品质量很好，物流也快，非常满意。\n"
    "2,质量太差了，用了一天就坏了，非常失望。\n"
).encode("utf-8-sig")

BAD_FILE_IDS = [
    "",
    "   ",
    ".",
    "..",
    "../secret.csv",
    "../../etc/passwd",
    "..\\..\\windows\\win.ini",
    "/etc/passwd",
    "sub/dir/file.csv",
    "sub\\file.csv",
    "C:\\windows\\win.ini",
    "batch_prediction_20261016_135753_1a2b3c4d.csv.bak",
    ".gitkeep",
    ".csv",
    "script.csv\x00.txt",
]


# --------------------------------------------------------------------------- #
# 文件名与落盘
# --------------------------------------------------------------------------- #
def test_new_export_filename_is_timestamped_and_unique():
    fixed = batch_io.new_export_filename(datetime(2026, 10, 16, 13, 57, 53))
    assert fixed.startswith("batch_prediction_20261016_135753_")
    assert fixed.endswith(".csv")
    assert batch_io.ensure_safe_csv_name(fixed) == fixed
    assert batch_io.new_export_filename() != batch_io.new_export_filename()


def test_default_predictions_dir_is_outputs_predictions():
    from pathlib import Path

    assert Path("outputs") / "predictions" == Path("outputs/predictions")
    assert batch_io.PREDICTIONS_DIR.name == "predictions"
    assert batch_io.EXPORT_URL_PREFIX == "/api/predict/batch/download/"


def test_export_results_writes_bom_header_and_rows(tmp_path):
    path = batch_io.export_results(
        [
            {"index": 0, "text": "很好", "label": "positive", "confidence": 0.83},
            {"index": 1, "text": "太差了", "label": "negative", "confidence": 0.85},
        ],
        out_dir=tmp_path,
    )
    assert path.parent == tmp_path
    assert path.name.startswith("batch_prediction_") and path.suffix == ".csv"

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须是 UTF-8 with BOM"

    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert list(rows[0]) == ["index", "text", "label", "confidence"]
    assert [row["text"] for row in rows] == ["很好", "太差了"]
    assert [row["label"] for row in rows] == ["positive", "negative"]


def test_export_results_rejects_explicit_unsafe_filename(tmp_path):
    with pytest.raises(batch_io.UnsafeExportPathError):
        batch_io.export_results([], filename="../../evil.csv", out_dir=tmp_path)
    assert not (tmp_path.parent / "evil.csv").exists()


def test_export_url_for_points_at_download_route():
    name = "batch_prediction_20261016_135753_1a2b3c4d.csv"
    assert batch_io.export_url_for(name) == f"/api/predict/batch/download/{name}"


# --------------------------------------------------------------------------- #
# 安全边界（单元级）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", BAD_FILE_IDS)
def test_ensure_safe_csv_name_rejects_path_like_ids(bad):
    with pytest.raises(batch_io.UnsafeExportPathError):
        batch_io.ensure_safe_csv_name(bad)


@pytest.mark.parametrize("bad", BAD_FILE_IDS)
def test_resolve_export_path_rejects_path_like_ids(bad, tmp_path):
    with pytest.raises(batch_io.UnsafeExportPathError):
        batch_io.resolve_export_path(bad, out_dir=tmp_path)


def test_resolve_export_path_stays_inside_export_dir(tmp_path):
    name = "batch_prediction_20261016_135753_1a2b3c4d.csv"
    resolved = batch_io.resolve_export_path(name, out_dir=tmp_path)
    assert resolved.parent == tmp_path.resolve()
    assert resolved.name == name


def test_resolve_export_path_rejects_symlink_escape(tmp_path):
    """即使文件名合法，解析后父目录不在导出目录内也要拒绝。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.csv").write_text("x", encoding="utf-8")
    export_dir = tmp_path / "predictions"
    export_dir.mkdir()
    link = export_dir / "link.csv"
    try:
        link.symlink_to(outside / "leak.csv")
    except (OSError, NotImplementedError):  # pragma: no cover - Windows 无权限时跳过
        pytest.skip("当前环境不支持创建符号链接")
    with pytest.raises(batch_io.UnsafeExportPathError):
        batch_io.resolve_export_path("link.csv", out_dir=export_dir)


# --------------------------------------------------------------------------- #
# HTTP 全链路
# --------------------------------------------------------------------------- #
def _post_batch():
    return client.post(
        "/api/predict/batch",
        files={"file": ("comments.csv", CSV_PAYLOAD, "text/csv")},
    )


def test_batch_upload_then_download_roundtrip():
    response = _post_batch()
    assert response.status_code == 200

    body = response.json()
    assert body["total"] == 2
    export_url = body["export_url"]
    assert export_url is not None
    assert export_url.startswith("/api/predict/batch/download/")
    file_id = export_url.rsplit("/", 1)[-1]

    # 已落盘在受控导出目录内
    assert (batch_io.PREDICTIONS_DIR / file_id).is_file()

    download = client.get(export_url)
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    disposition = download.headers.get("content-disposition", "")
    assert "attachment" in disposition and file_id in disposition

    text = download.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "index,text,label,confidence"
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [row["text"] for row in rows] == [
        "这个商品质量很好，物流也快，非常满意。",
        "质量太差了，用了一天就坏了，非常失望。",
    ]
    assert [row["label"] for row in rows] == ["positive", "negative"]


def test_download_missing_file_returns_404():
    response = client.get(
        "/api/predict/batch/download/batch_prediction_20200101_000000_deadbeef.csv"
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/predict/batch/download/..%2f..%2fetc%2fpasswd",
        "/api/predict/batch/download/%2e%2e%2fsecret.csv",
        "/api/predict/batch/download/nested%2fsecret.csv",
        "/api/predict/batch/download/..%2foutputs%2fREADME.md",
    ],
)
def test_download_slash_traversal_is_rejected_with_4xx(path):
    response = client.get(path)
    assert 400 <= response.status_code < 500, (path, response.status_code)


@pytest.mark.parametrize(
    "file_id",
    ["..%5c..%5cwindows%5cwin.ini", "..%5coutputs%5cREADME.md", "sub%5cfile.csv"],
)
def test_download_backslash_traversal_hits_guard_and_returns_400(file_id):
    response = client.get(f"/api/predict/batch/download/{file_id}")
    assert response.status_code == 400, (file_id, response.status_code)


@pytest.mark.parametrize("file_id", [".gitkeep", "batch.csv.bak", "no_suffix"])
def test_download_non_csv_or_dotfile_returns_400(file_id):
    response = client.get(f"/api/predict/batch/download/{file_id}")
    assert response.status_code == 400, (file_id, response.status_code)
