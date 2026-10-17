"""公开数据集模块单元测试（全部离线，不访问真实站点）。

覆盖：

1. 注册表：内置 3+ 个公开中文情感语料、名称解析、DatasetSpec 校验；
2. 下载：本地 ``http.server`` 提供合成 CSV/ZIP，验证流式下载、SHA256
   成功/失败、404/空文件、已存在复用与 ``force`` 重下、URL/校验和覆盖、
   ``data/raw`` 临时文件不残留、来源登记（sources.json / 来源说明.md）；
3. 格式统一：``import_labeled_csv`` 的标签映射、去空、去重、非法标签
   严格失败/``drop_invalid`` 丢弃、UTF-8 BOM 输出；
4. CLI：``--help`` / 无参数 / ``--list`` / ``--download`` / ``--import-csv``。

测试只把本地 HTTP 服务的合成数据注册为临时 DatasetSpec，**真实注册表中的
URL 从未被请求**。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.crawler import datasets
from src.crawler.datasets import (
    DatasetError,
    DatasetSpec,
    count_records,
    download_dataset,
    get_dataset,
    import_labeled_csv,
    list_datasets,
    main,
    read_csv_header,
    sha256_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

TOY_CSV = "label,review\n1,很好\n0,太差\n".encode("utf-8")


# =========================================================================
# 测试基础设施：本地 HTTP 服务（离线）
# =========================================================================

@contextmanager
def local_file_server(files: dict[str, bytes]):
    """起一个临时本地 HTTP 服务，返回 (base_url, 请求路径记录, 文件字典)。

    ``files`` 可在测试中修改，用于验证 ``force`` 重新下载拿到新内容。
    """

    requests_log: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
            requests_log.append(self.path)
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: D401 - 静默访问日志
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests_log, files
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_spec(
    base_url: str,
    *,
    name: str = "测试数据集",
    path: str = "/toy.csv",
    sha256: str = "",
    filename: str = "toy.csv",
) -> DatasetSpec:
    return DatasetSpec(
        name=name,
        description="本地合成的测试语料（不访问外网）",
        url=f"{base_url}{path}",
        sha256=sha256,
        filename=filename,
        license="测试数据，仅用于单元测试",
        note="合成数据：列 label,review",
        homepage="http://127.0.0.1/",
        labeled=True,
        text_column="review",
        label_column="label",
    )


def _register(monkeypatch, spec: DatasetSpec, key: str = "toy") -> None:
    """把合成 DatasetSpec 临时注册进注册表（用例结束自动恢复）。"""

    monkeypatch.setitem(datasets.DATASETS, key, spec)


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


# =========================================================================
# 1. 注册表
# =========================================================================

def test_builtin_registry_has_public_chinese_datasets():
    specs = list_datasets()
    assert len(specs) >= 3
    names = " ".join(spec.name for spec in specs)
    for keyword in ("ChnSentiCorp", "外卖", "酒店"):
        assert keyword in names, f"注册表缺少公开语料：{keyword}"
    for spec in specs:
        assert spec.url.startswith("https://")
        assert spec.filename
        assert spec.license
        assert spec.description
        if spec.sha256:  # 允许留空（上游文件会更新），但填了就必须是合法 SHA256
            assert len(spec.sha256) == 64
            assert all(char in "0123456789abcdef" for char in spec.sha256)


def test_get_dataset_by_key_display_name_and_unknown():
    assert get_dataset("chnsenticorp").name == "ChnSentiCorp"
    assert get_dataset("CHNSENTICORP") is get_dataset("chnsenticorp")
    assert get_dataset("酒店评论数据集").filename == "hotel_reviews.csv"
    with pytest.raises(DatasetError, match="未知数据集"):
        get_dataset("不存在的语料")


def test_dataset_spec_normalizes_and_validates():
    with pytest.raises(DatasetError):
        DatasetSpec(name="x", description="d", url="http://example.com/a/b.csv", sha256="abc")
    with pytest.raises(DatasetError):
        DatasetSpec(name="  ", description="d", url="http://example.com/a.csv")
    with pytest.raises(DatasetError):
        DatasetSpec(name="x", description="d", url="")

    spec = DatasetSpec(name="x", description="d", url="http://example.com/a/b.csv")
    assert spec.filename == "b.csv"
    assert spec.format == "csv"
    assert spec.sha256 == ""
    upper = DatasetSpec(
        name="y", description="d", url="http://example.com/a.zip",
        sha256="A" * 64, filename="z.ZIP",
    )
    assert upper.sha256 == "a" * 64
    assert upper.format == "zip"


def test_register_dataset_rejects_duplicate_and_supports_overwrite():
    spec = DatasetSpec(name="临时数据集", description="d", url="http://example.com/x.csv")
    datasets.register_dataset(spec)
    try:
        with pytest.raises(DatasetError, match="已注册"):
            datasets.register_dataset(spec)
        replacement = DatasetSpec(
            name="临时数据集", description="d2", url="http://example.com/y.csv"
        )
        datasets.register_dataset(replacement, overwrite=True)
        assert get_dataset("临时数据集").description == "d2"
    finally:
        datasets.DATASETS.pop("临时数据集", None)


# =========================================================================
# 2. 下载与校验（本地 HTTP 服务）
# =========================================================================

def test_download_streams_verifies_and_registers_source(tmp_path, monkeypatch):
    sha = hashlib.sha256(TOY_CSV).hexdigest()
    with local_file_server({"/toy.csv": TOY_CSV}) as (base, requests_log, _files):
        _register(monkeypatch, _make_spec(base, sha256=sha))
        target = tmp_path / "raw"
        json_path = tmp_path / "sources.json"
        note_path = tmp_path / "来源说明.md"
        path = download_dataset(
            "toy",
            target_dir=target,
            source_json_path=json_path,
            source_note_path=note_path,
        )

    assert path == target / "toy.csv"
    assert path.read_bytes() == TOY_CSV
    assert requests_log == ["/toy.csv"]
    # 没有 .part 半成品残留
    assert {item.name for item in target.iterdir()} == {"toy.csv"}

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    record = payload[0]
    assert record["labeled"] is True
    assert record["count"] == 2
    assert sha in record["note"]
    assert "测试数据集" in note_path.read_text(encoding="utf-8")
    assert "已有标注" in note_path.read_text(encoding="utf-8")


def test_download_checksum_mismatch_fails_without_residue(tmp_path, monkeypatch):
    wrong_sha = "0" * 64
    with local_file_server({"/toy.csv": TOY_CSV}) as (base, _log, _files):
        _register(monkeypatch, _make_spec(base, sha256=wrong_sha))
        target = tmp_path / "raw"
        json_path = tmp_path / "sources.json"
        with pytest.raises(DatasetError, match="SHA256 校验失败"):
            download_dataset(
                "toy",
                target_dir=target,
                source_json_path=json_path,
                source_note_path=tmp_path / "note.md",
            )

    assert target.exists()
    assert list(target.iterdir()) == []  # 半成品被清理
    assert not json_path.exists()  # 校验失败不登记来源


def test_download_404_and_empty_file_fail_cleanly(tmp_path, monkeypatch):
    with local_file_server({"/empty.csv": b""}) as (base, _log, _files):
        _register(monkeypatch, _make_spec(base, path="/missing.csv"))
        target = tmp_path / "raw"
        with pytest.raises(DatasetError, match="下载失败"):
            download_dataset("toy", target_dir=target, write_source_note=False)
        assert list(target.iterdir()) == []

        _register(monkeypatch, _make_spec(base, path="/empty.csv", filename="empty.csv"))
        with pytest.raises(DatasetError, match="下载内容为空"):
            download_dataset("toy", target_dir=target, write_source_note=False)
        assert list(target.iterdir()) == []


def test_download_reuses_existing_file_and_force_redownloads(tmp_path, monkeypatch):
    files = {"/toy.csv": TOY_CSV}
    with local_file_server(files) as (base, requests_log, files_dict):
        sha = hashlib.sha256(TOY_CSV).hexdigest()
        _register(monkeypatch, _make_spec(base, sha256=sha))
        target = tmp_path / "raw"

        first = download_dataset("toy", target_dir=target, write_source_note=False)
        assert first.read_bytes() == TOY_CSV
        assert requests_log == ["/toy.csv"]

        # 已存在且校验通过：直接复用，不再发请求
        again = download_dataset("toy", target_dir=target, write_source_note=False)
        assert again == first
        assert requests_log == ["/toy.csv"]

        # 服务端内容更新 + 期望校验和更新：不 force 时应报告现有文件校验不符
        new_bytes = "label,review\n1,新内容\n".encode("utf-8")
        files_dict["/toy.csv"] = new_bytes
        new_sha = hashlib.sha256(new_bytes).hexdigest()
        _register(monkeypatch, _make_spec(base, sha256=new_sha))
        with pytest.raises(DatasetError, match="已存在的文件"):
            download_dataset("toy", target_dir=target, write_source_note=False)

        forced = download_dataset(
            "toy", target_dir=target, force=True, write_source_note=False
        )
        assert forced.read_bytes() == new_bytes
        assert requests_log == ["/toy.csv", "/toy.csv"]
        assert {item.name for item in target.iterdir()} == {"toy.csv"}


def test_download_url_and_sha256_overrides(tmp_path, monkeypatch):
    with local_file_server({"/override.csv": TOY_CSV}) as (base, requests_log, _files):
        # 注册表 URL 故意不可达，用参数覆盖为本地地址
        _register(monkeypatch, _make_spec(base, path="/unreachable.csv", filename="toy.csv"))
        path = download_dataset(
            "toy",
            target_dir=tmp_path / "raw",
            url=f"{base}/override.csv",
            sha256=hashlib.sha256(TOY_CSV).hexdigest(),
            write_source_note=False,
        )
    assert path.read_bytes() == TOY_CSV
    assert requests_log == ["/override.csv"]


def test_count_records_for_csv_zip_and_unknown(tmp_path):
    csv_path = _write_csv(
        tmp_path / "a.csv",
        ["text", "label"],
        [{"text": "好", "label": "1"}, {"text": "差", "label": "0"}],
    )
    assert count_records(csv_path) == 2

    zip_path = tmp_path / "a.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("train.csv", "text,label\n好,1\n")
        archive.writestr("test.csv", "text,label\n差,0\n")
        archive.writestr("__MACOSX/ignored", "x")
        archive.writestr("sub/", "")
    assert count_records(zip_path) == 2

    other = tmp_path / "a.bin"
    other.write_bytes(b"\x00\x01")
    assert count_records(other) == 0


# =========================================================================
# 3. 格式统一：import_labeled_csv
# =========================================================================

def test_import_labeled_csv_maps_labels_dedupes_and_writes_bom(tmp_path):
    source = _write_csv(
        tmp_path / "raw.csv",
        ["text", "label"],
        [
            {"text": " 很好吃 &amp; 便宜 ", "label": "1"},
            {"text": "太差了\r\n", "label": "negative"},
            {"text": "不错", "label": "正面"},
            {"text": "不行", "label": "NEG"},
            {"text": "好评", "label": "pos"},
            {"text": "质量一般", "label": ""},
            {"text": "很好吃 & 便宜", "label": "1"},  # 标准化后与第 1 条重复
            {"text": "   ", "label": "1"},
            {"text": "???", "label": "unknown"},
        ],
    )
    output = tmp_path / "unified.csv"
    report = import_labeled_csv(source, output, drop_invalid=True)

    assert report.total_rows == 9
    assert report.kept == 5
    assert report.empty_text == 1
    assert report.duplicates == 1
    assert report.invalid_label == 2
    assert report.labels == {"positive": 3, "negative": 2}
    assert report.source_sha256 == sha256_file(source)
    assert report.output_sha256 == sha256_file(output)

    assert output.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["text", "label"]
        rows = list(reader)
    assert len(rows) == 5
    assert {row["label"] for row in rows} == {"positive", "negative"}
    assert "很好吃 & 便宜" in [row["text"] for row in rows]


def test_import_invalid_labels_raise_by_default_and_write_nothing(tmp_path):
    source = _write_csv(
        tmp_path / "raw.csv",
        ["text", "label"],
        [{"text": "好", "label": "1"}, {"text": "一般", "label": "neutral"}],
    )
    output = tmp_path / "unified.csv"
    with pytest.raises(DatasetError, match="标签无法映射"):
        import_labeled_csv(source, output)
    assert not output.exists()


def test_import_custom_label_map_and_review_column(tmp_path):
    source = _write_csv(
        tmp_path / "chn.csv",
        ["label", "review"],
        [
            {"label": "2", "review": "服务很好"},
            {"label": "-1", "review": "房间太差"},
            {"label": "1", "review": "早餐丰富"},
        ],
    )
    output = tmp_path / "unified.csv"
    report = import_labeled_csv(
        source,
        output,
        text_col="review",
        label_map={"2": "positive", "-1": "negative"},
    )
    assert report.labels == {"positive": 2, "negative": 1}
    assert read_csv_header(output) == ["text", "label"]

    with pytest.raises(DatasetError, match="目标值只能是"):
        import_labeled_csv(
            source, tmp_path / "bad.csv", text_col="review",
            label_map={"2": "neutral"},
        )


def test_import_missing_columns_and_file_raise(tmp_path):
    source = _write_csv(
        tmp_path / "raw.csv", ["text", "label"], [{"text": "好", "label": "1"}]
    )
    with pytest.raises(DatasetError, match="缺少列"):
        import_labeled_csv(source, tmp_path / "out.csv", text_col="missing")
    with pytest.raises(DatasetError, match="缺少列"):
        import_labeled_csv(source, tmp_path / "out.csv", label_col="missing")
    with pytest.raises(DatasetError, match="文件不存在"):
        import_labeled_csv(tmp_path / "nope.csv", tmp_path / "out.csv")


# =========================================================================
# 4. CLI
# =========================================================================

def test_cli_help_no_args_and_list(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0

    assert main([]) == 0
    assert "--list-datasets" in capsys.readouterr().out

    assert main(["--list"]) == 0
    listing = capsys.readouterr().out
    assert "ChnSentiCorp" in listing
    assert "外卖" in listing
    assert "酒店" in listing


def test_cli_help_subprocess_exits_zero():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "src.crawler.datasets", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "--list-datasets" in proc.stdout or "--list" in proc.stdout


def test_cli_download_and_import_with_local_data(tmp_path, monkeypatch, capsys):
    with local_file_server({"/toy.csv": TOY_CSV}) as (base, _log, _files):
        _register(monkeypatch, _make_spec(base, sha256=hashlib.sha256(TOY_CSV).hexdigest()))
        target = tmp_path / "raw"
        json_path = tmp_path / "sources.json"
        note_path = tmp_path / "source.md"
        code = main(
            [
                "--download", "toy",
                "--target-dir", str(target),
                "--sources-json", str(json_path),
                "--source-note", str(note_path),
            ]
        )
        assert code == 0
        assert (target / "toy.csv").exists()
        captured = capsys.readouterr().out
        assert "已下载" in captured
        assert json_path.exists()

    source = _write_csv(
        tmp_path / "unified_src.csv",
        ["text", "label"],
        [{"text": "很好", "label": "1"}, {"text": "一般", "label": "?"}],
    )
    output = tmp_path / "unified.csv"
    code = main(
        ["--import-csv", str(source), "--output", str(output), "--drop-invalid"]
    )
    assert code == 0
    assert output.exists()
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "已统一" in capsys.readouterr().out


def test_cli_import_auto_detects_spec_columns(tmp_path, capsys):
    """按注册表文件名自动采用 review/label 列名，无需手写 --text-col。"""

    source = _write_csv(
        tmp_path / "ChnSentiCorp_htl_all.csv",
        ["label", "review"],
        [
            {"label": "1", "review": "房间干净，服务很好"},
            {"label": "0", "review": "隔音太差，不会再住"},
        ],
    )
    assert datasets.spec_for_file(source) is not None
    output = tmp_path / "unified.csv"
    assert main(["--import-csv", str(source), "--output", str(output)]) == 0
    rows = []
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["label"] for row in rows] == ["positive", "negative"]
    assert "已统一" in capsys.readouterr().out


def test_cli_download_unknown_dataset_returns_1(capsys):
    assert main(["--download", "不存在的语料"]) == 1
    assert "未知数据集" in capsys.readouterr().err


def test_dataset_spec_rejects_non_http_url_and_unsafe_filename():
    """下载 URL 仅允许 http(s)，filename 不能带目录穿越（Lead 集成审查补强）。"""

    with pytest.raises(datasets.DatasetError):
        datasets.DatasetSpec(name="x", description="x", url="file:///etc/passwd")
    with pytest.raises(datasets.DatasetError):
        datasets.DatasetSpec(name="x", description="x", url="ftp://example.test/a.csv")
    with pytest.raises(datasets.DatasetError):
        datasets.DatasetSpec(
            name="x", description="x", url="https://example.test/a.csv", filename="../../evil.csv"
        )
    with pytest.raises(datasets.DatasetError):
        datasets.DatasetSpec(
            name="x", description="x", url="https://example.test/a.csv", filename="sub/evil.csv"
        )


def test_open_url_rejects_file_scheme():
    """默认下载器不能把用户传入的 file:// 当成本地文件读取。"""

    with pytest.raises(datasets.DatasetError):
        datasets._open_url("file:///etc/passwd", timeout=1)


# =========================================================================
# 6. 合并多份已标注 CSV + 类别均衡
# =========================================================================

def test_merge_balance_labeled_csv_balances_dedupes_and_is_deterministic(tmp_path):
    first = _write_csv(
        tmp_path / "a.csv",
        ["text", "label"],
        [
            {"text": "好吃", "label": "positive"},
            {"text": "很快", "label": "positive"},
            {"text": "太差", "label": "negative"},
            {"text": "太差", "label": "negative"},  # 跨文件去重
            {"text": "很慢", "label": "negative"},
        ],
    )
    second = _write_csv(
        tmp_path / "b.csv",
        ["text", "label"],
        [
            {"text": "味道足", "label": "positive"},
            {"text": "不新鲜", "label": "negative"},
            {"text": "送错餐", "label": "negative"},
        ],
    )
    output = tmp_path / "balanced.csv"
    report = datasets.merge_balance_labeled_csv([first, second], output, seed=7)
    rows = list(csv.DictReader(output.open(encoding="utf-8-sig", newline="")))
    assert report.per_label == 3  # 正 3 / 负 5，按较小类别下采样
    assert report.kept == 6
    assert report.labels == {"positive": 3, "negative": 3}
    assert sum(1 for row in rows if row["label"] == "positive") == 3
    assert sum(1 for row in rows if row["label"] == "negative") == 3
    assert report.duplicates >= 1
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")

    again = tmp_path / "balanced2.csv"
    report2 = datasets.merge_balance_labeled_csv([first, second], again, seed=7)
    assert again.read_bytes() == output.read_bytes()
    assert report2.output_sha256 == report.output_sha256


def test_merge_balance_respects_max_per_label_and_rejects_invalid_labels(tmp_path):
    source = _write_csv(
        tmp_path / "a.csv",
        ["text", "label"],
        [{"text": f"好{item}", "label": "positive"} for item in range(5)]
        + [{"text": f"差{item}", "label": "negative"} for item in range(5)],
    )
    output = tmp_path / "balanced.csv"
    report = datasets.merge_balance_labeled_csv([source], output, max_per_label=2, seed=1)
    assert report.per_label == 2
    assert report.kept == 4

    bad = _write_csv(
        tmp_path / "bad.csv",
        ["text", "label"],
        [{"text": "还行", "label": "neutral"}],
    )
    with pytest.raises(DatasetError):
        datasets.merge_balance_labeled_csv([bad], tmp_path / "bad_out.csv")
