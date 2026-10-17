"""标注工作流（确定性抽样导出 + 标注合并）单元测试（全部离线）。

覆盖：

1. 抽样：同 seed 完全可复现、按 ``video_id``/``bvid`` 分层且名额按组规模分配、
   ``sample_size`` 超过候选数时取全部、输出列与 UTF-8 BOM；
2. 排除：已标注、重复文本、空文本、重复 id，manifest 排除统计准确；
3. 合并：合法标签接受、非法/空/缺失标签、重复 id、冲突 id、样本列保留、
   输出 manifest（accepted / excluded / 正负计数 / 输入哈希）；
4. CLI：``--help``（子进程退出码 0）、无子命令打印帮助、export/merge 子命令、
   非严格模式与失败返回码。

所有用例只读写 ``tmp_path`` 下的临时文件，不访问网络、不写仓库 ``data/``。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.crawler.labeling import (
    SAMPLE_COLUMNS,
    LabelingError,
    export_labeling_sample,
    main,
    merge_labels,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# =========================================================================
# 测试数据与工具
# =========================================================================

def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_header(path: Path) -> list[str]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def _read_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _make_comments_csv(tmp_path: Path, group_sizes: list[tuple[str, int]] | None = None) -> Path:
    """生成 B 站风格的原始评论 CSV：bvid + rpid + text（无 label）。"""

    group_sizes = group_sizes or [("BV_A", 30), ("BV_B", 20), ("BV_C", 10)]
    rows: list[dict[str, str]] = []
    index = 0
    for group, size in group_sizes:
        for _ in range(size):
            rows.append(
                {
                    "bvid": group,
                    "rpid": f"r{index:03d}",
                    "text": f"第{index}条评论内容各不相同，用于分层抽样测试。",
                    "video_title": "演示视频",
                }
            )
            index += 1
    return _write_csv(
        tmp_path / "comments.csv", ["bvid", "rpid", "text", "video_title"], rows
    )


def _write_labels(path: Path, pairs: list[tuple[str, str]]) -> Path:
    return _write_csv(
        path, ["id", "label"], [{"id": record_id, "label": label} for record_id, label in pairs]
    )


def _export(tmp_path: Path, source: Path, sample_size: int, seed: int = 42):
    output = tmp_path / f"sample_{sample_size}_{seed}.csv"
    result = export_labeling_sample(source, output, sample_size=sample_size, seed=seed)
    return output, result


# =========================================================================
# 1. 抽样：确定性与分层
# =========================================================================

def test_export_is_deterministic_per_seed(tmp_path):
    source = _make_comments_csv(tmp_path)
    out_a, first = _export(tmp_path, source, 6, seed=42)
    out_b, second = _export(tmp_path, source, 6, seed=42)

    assert first.sampled == 6 == second.sampled
    # 同输入 + 同 seed：CSV 字节完全一致
    assert out_a.read_bytes() == out_b.read_bytes()
    assert first.manifest["sampled"] == second.manifest["sampled"]
    assert [row["id"] for row in _read_csv(out_a)] == [
        row["id"] for row in _read_csv(out_b)
    ]
    # 换 seed：结果应不同（同实现下确定性）
    out_c, _third = _export(tmp_path, source, 6, seed=7)
    assert [row["id"] for row in _read_csv(out_c)] != [
        row["id"] for row in _read_csv(out_a)
    ]


def test_export_manifest_records_inputs_seed_and_hashes(tmp_path):
    source = _make_comments_csv(tmp_path)
    output, result = _export(tmp_path, source, 6, seed=42)
    manifest = _read_manifest(result.manifest_path)

    assert manifest == result.manifest
    assert manifest["kind"] == "labeling_sample_export"
    assert manifest["input_rows"] == 60
    assert manifest["eligible_rows"] == 60
    assert manifest["sample_size_requested"] == 6
    assert manifest["sampled"] == 6
    assert manifest["seed"] == 42
    assert manifest["group_column"] == "bvid"
    assert manifest["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest["excluded"] == {
        "already_labeled": 0,
        "duplicate_text": 0,
        "empty_text": 0,
        "duplicate_id": 0,
    }
    # 默认 manifest 与输出 CSV 同目录同主名
    assert Path(result.manifest_path) == output.with_name(f"{output.stem}.manifest.json")


def test_export_stratifies_by_group_proportionally(tmp_path):
    source = _make_comments_csv(tmp_path)  # 30 / 20 / 10
    output, result = _export(tmp_path, source, 6)
    manifest = result.manifest

    assert manifest["sampled_by_group"] == {"BV_A": 3, "BV_B": 2, "BV_C": 1}
    rows = _read_csv(output)
    assert len(rows) == 6
    counts = {"BV_A": 0, "BV_B": 0, "BV_C": 0}
    for row in rows:
        counts[row["group"]] += 1
    assert counts == {"BV_A": 3, "BV_B": 2, "BV_C": 1}
    # 最大余数法：小样本时也一样确定（分组列全部列出，含 0 条便于审计）
    _out2, second = _export(tmp_path, source, 2)
    assert second.manifest["sampled_by_group"] == {"BV_A": 1, "BV_B": 1, "BV_C": 0}


def test_export_larger_than_population_returns_all_eligible(tmp_path):
    source = _make_comments_csv(tmp_path)
    output, result = _export(tmp_path, source, 1000)
    assert result.sampled == 60
    assert len(_read_csv(output)) == 60
    assert result.manifest["sample_size_requested"] == 1000


def test_export_output_columns_bom_and_values(tmp_path):
    source = _make_comments_csv(tmp_path)
    output, result = _export(tmp_path, source, 4)

    assert _read_header(output) == list(SAMPLE_COLUMNS)
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    rows = _read_csv(output)
    assert len(rows) == 4
    source_rows = {row["rpid"]: row for row in _read_csv(source)}
    for row in rows:
        assert row["label"] == ""
        assert row["text"]
        assert row["video_id"] == row["group"]
        assert row["id"] in source_rows
        assert source_rows[row["id"]]["bvid"] == row["group"]


def test_export_supports_video_id_group_and_unknown_group(tmp_path):
    video_id_source = _write_csv(
        tmp_path / "video_id.csv",
        ["video_id", "text"],
        [
            {"video_id": "v1", "text": "第一条内容"},
            {"video_id": "v1", "text": "第二条内容"},
            {"video_id": "v2", "text": "第三条内容"},
        ],
    )
    _out, result = _export(tmp_path, video_id_source, 3)
    assert result.manifest["group_column"] == "video_id"
    assert result.manifest["sampled_by_group"] == {"v1": 2, "v2": 1}

    group_source = _write_csv(
        tmp_path / "group.csv",
        ["group", "text"],
        [{"group": "g1", "text": "内容一"}, {"group": "g2", "text": "内容二"}],
    )
    output, result = _export(tmp_path, group_source, 2)
    assert result.manifest["group_column"] == "group"
    assert {row["group"] for row in _read_csv(output)} == {"g1", "g2"}

    plain_source = _write_csv(
        tmp_path / "plain.csv",
        ["rpid", "text"],
        [{"rpid": "1", "text": "没有分组列的内容"}],
    )
    output, result = _export(tmp_path, plain_source, 1)
    assert result.manifest["group_column"] is None
    row = _read_csv(output)[0]
    assert row["group"] == "unknown"
    assert row["video_id"] == ""
    assert row["id"] == "1"


def test_export_excludes_labeled_duplicates_and_empty_text(tmp_path):
    source = _write_csv(
        tmp_path / "mixed.csv",
        ["bvid", "rpid", "text", "label"],
        [
            {"bvid": "BV1", "rpid": "r1", "text": "重复 文本", "label": ""},
            {"bvid": "BV1", "rpid": "r2", "text": "第二条正常评论", "label": ""},
            {"bvid": "BV2", "rpid": "r3", "text": "第三条正常评论", "label": ""},
            {"bvid": "BV2", "rpid": "r4", "text": "第四条正常评论", "label": ""},
            {"bvid": "BV1", "rpid": "r5", "text": "已标注正面", "label": "positive"},
            {"bvid": "BV2", "rpid": "r6", "text": "已标注负面", "label": "negative"},
            {"bvid": "BV2", "rpid": "r7", "text": "空白标签也不算已标注", "label": "  "},
            {"bvid": "BV1", "rpid": "r8", "text": "重复文本", "label": ""},
            {"bvid": "BV1", "rpid": "r9", "text": "   ", "label": ""},
        ],
    )
    output, result = _export(tmp_path, source, 100)
    manifest = result.manifest

    assert manifest["excluded"] == {
        "already_labeled": 2,
        "duplicate_text": 1,
        "empty_text": 1,
        "duplicate_id": 0,
    }
    assert manifest["eligible_rows"] == 5
    assert result.sampled == 5
    sampled_texts = [row["text"] for row in _read_csv(output)]
    assert "重复 文本" in sampled_texts
    assert "已标注正面" not in sampled_texts

    # 明确包含已标注行时，这些行进入候选
    included = export_labeling_sample(
        source,
        tmp_path / "include_labeled.csv",
        sample_size=100,
        exclude_labeled=False,
    )
    assert included.manifest["excluded"]["already_labeled"] == 0
    assert included.manifest["eligible_rows"] == 7


def test_export_duplicate_id_is_counted(tmp_path):
    source = _write_csv(
        tmp_path / "dup_id.csv",
        ["bvid", "rpid", "text"],
        [
            {"bvid": "BV1", "rpid": "same", "text": "第一条不同文本"},
            {"bvid": "BV1", "rpid": "same", "text": "第二条不同文本"},
        ],
    )
    _output, result = _export(tmp_path, source, 10)
    assert result.manifest["excluded"]["duplicate_id"] == 1
    assert result.sampled == 1


def test_export_errors_on_bad_input(tmp_path):
    with pytest.raises(LabelingError, match="文件不存在"):
        export_labeling_sample(tmp_path / "nope.csv", tmp_path / "out.csv", 10)

    source = _write_csv(
        tmp_path / "raw.csv", ["title"], [{"title": "没有文本列"}]
    )
    with pytest.raises(LabelingError, match="缺少文本列"):
        export_labeling_sample(source, tmp_path / "out.csv", 10)

    valid = _make_comments_csv(tmp_path)
    with pytest.raises(LabelingError, match="sample_size"):
        export_labeling_sample(valid, tmp_path / "out.csv", 0)

    all_empty = _write_csv(
        tmp_path / "empty.csv", ["text"], [{"text": ""}, {"text": "   "}]
    )
    with pytest.raises(LabelingError, match="没有可抽样"):
        export_labeling_sample(all_empty, tmp_path / "out.csv", 1)


# =========================================================================
# 2. 合并：合法/非法/重复/冲突
# =========================================================================

def _sample_with_ids(tmp_path: Path, size: int = 4):
    source = _make_comments_csv(tmp_path)
    sample_csv, _result = _export(tmp_path, source, size, seed=42)
    ids = [row["id"] for row in _read_csv(sample_csv)]
    return sample_csv, ids


@pytest.mark.parametrize("size", [2, 4, 6])
def test_merge_accepts_valid_labels(tmp_path, size):
    sample_csv, ids = _sample_with_ids(tmp_path, size)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [
            (record_id, "positive" if index % 2 == 0 else "negative")
            for index, record_id in enumerate(ids)
        ],
    )
    output = tmp_path / "merged.csv"
    result = merge_labels(sample_csv, labels_csv, output)

    assert result.accepted == size
    assert result.label_counts == {
        "positive": len(ids[0::2]),
        "negative": len(ids[1::2]),
    }
    assert sum(result.excluded.values()) == 0
    rows = _read_csv(output)
    assert [row["id"] for row in rows] == ids  # 保留样本顺序
    assert all(row["label"] in {"positive", "negative"} for row in rows)
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    manifest = _read_manifest(result.manifest_path)
    assert manifest["kind"] == "labeling_merge"
    assert manifest["strict"] is True
    assert manifest["accepted"] == size
    assert manifest["labels_rows"] == size
    assert manifest["sample_sha256"] == hashlib.sha256(sample_csv.read_bytes()).hexdigest()
    assert manifest["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest["label_counts"] == result.label_counts


def test_merge_strict_rejects_invalid_label_and_writes_nothing(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 3)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [(ids[0], "positive"), (ids[1], "neutral"), (ids[2], "negative")],
    )
    output = tmp_path / "merged.csv"
    with pytest.raises(LabelingError, match="校验未通过"):
        merge_labels(sample_csv, labels_csv, output)
    assert not output.exists()
    assert not output.with_name("merged.manifest.json").exists()


def test_merge_lenient_skips_invalid_label(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 3)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [(ids[0], "Positive"), (ids[1], "neutral"), (ids[2], "NEGATIVE")],
    )
    output = tmp_path / "merged.csv"
    result = merge_labels(sample_csv, labels_csv, output, strict=False)

    assert result.accepted == 2
    assert result.excluded["invalid_label"] == 1
    assert result.label_counts == {"positive": 1, "negative": 1}
    assert [row["id"] for row in _read_csv(output)] == [ids[0], ids[2]]
    manifest = _read_manifest(result.manifest_path)
    assert manifest["strict"] is False
    assert manifest["excluded"]["invalid_label"] == 1


def test_merge_strict_rejects_missing_labels_and_lenient_reports_them(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 5)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [(record_id, "positive") for record_id in ids[:3]],
    )
    output = tmp_path / "merged.csv"

    with pytest.raises(LabelingError, match="missing_label"):
        merge_labels(sample_csv, labels_csv, output)
    assert not output.exists()

    result = merge_labels(sample_csv, labels_csv, output, strict=False)
    assert result.accepted == 3
    assert result.excluded["missing_label"] == 2
    assert [row["id"] for row in _read_csv(output)] == ids[:3]


def test_merge_duplicate_ids_strict_vs_lenient(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 4)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [
            (ids[0], "positive"),
            (ids[0], "positive"),
            (ids[1], "negative"),
            (ids[2], "negative"),
            (ids[3], "positive"),
        ],
    )
    output = tmp_path / "merged.csv"

    with pytest.raises(LabelingError, match="duplicate_id"):
        merge_labels(sample_csv, labels_csv, output)

    result = merge_labels(sample_csv, labels_csv, output, strict=False)
    assert result.accepted == 4  # 同标签重复 id 保留第一条，不影响最终标签
    assert result.excluded["duplicate_id"] == 1


def test_merge_conflicting_ids_are_voided(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 4)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [
            (ids[0], "positive"),
            (ids[1], "positive"),
            (ids[1], "negative"),  # 冲突：两票作废
            (ids[2], "negative"),
            (ids[3], "positive"),
        ],
    )
    output = tmp_path / "merged.csv"

    with pytest.raises(LabelingError, match="冲突|校验未通过"):
        merge_labels(sample_csv, labels_csv, output)

    result = merge_labels(sample_csv, labels_csv, output, strict=False)
    assert result.accepted == 3
    assert result.excluded["conflicting_label"] == 1
    accepted_ids = [row["id"] for row in _read_csv(output)]
    assert ids[1] not in accepted_ids
    assert accepted_ids == [ids[0], ids[2], ids[3]]


def test_merge_empty_label_and_empty_id_are_reported(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 3)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [("", "positive"), (ids[0], ""), (ids[1], "positive"), (ids[2], "negative")],
    )
    output = tmp_path / "merged.csv"

    with pytest.raises(LabelingError, match="校验未通过"):
        merge_labels(sample_csv, labels_csv, output)

    result = merge_labels(sample_csv, labels_csv, output, strict=False)
    assert result.excluded["empty_id"] == 1
    assert result.excluded["empty_label"] == 1
    assert result.accepted == 2
    assert [row["id"] for row in _read_csv(output)] == [ids[1], ids[2]]


def test_merge_appends_label_column_and_preserves_sample_columns(tmp_path):
    sample_csv = _write_csv(
        tmp_path / "sample.csv",
        ["id", "text", "video_id"],
        [
            {"id": "a1", "text": "第一条评论", "video_id": "v1"},
            {"id": "a2", "text": "第二条评论", "video_id": "v2"},
        ],
    )
    labels_csv = _write_labels(
        tmp_path / "labels.csv", [("a1", "positive"), ("a2", "negative")]
    )
    output = tmp_path / "merged.csv"
    result = merge_labels(sample_csv, labels_csv, output)

    assert _read_header(output) == ["id", "text", "video_id", "label"]
    assert result.accepted == 2
    rows = _read_csv(output)
    assert rows[0]["text"] == "第一条评论"
    assert rows[0]["video_id"] == "v1"


def test_merge_errors_on_missing_files_and_columns(tmp_path):
    sample_csv, ids = _sample_with_ids(tmp_path, 2)
    labels_csv = _write_labels(
        tmp_path / "labels.csv", [(record_id, "positive") for record_id in ids]
    )

    with pytest.raises(LabelingError, match="文件不存在"):
        merge_labels(tmp_path / "nope.csv", labels_csv, tmp_path / "merged.csv")

    no_id_sample = _write_csv(
        tmp_path / "no_id.csv", ["text"], [{"text": "没有 id 列"}]
    )
    with pytest.raises(LabelingError, match="缺少 id 列"):
        merge_labels(no_id_sample, labels_csv, tmp_path / "merged.csv")

    no_label_labels = _write_csv(
        tmp_path / "no_label.csv", ["id"], [{"id": ids[0]}]
    )
    with pytest.raises(LabelingError, match="缺少 id 或标签列"):
        merge_labels(sample_csv, no_label_labels, tmp_path / "merged.csv")

    empty_sample = _write_csv(tmp_path / "empty_sample.csv", ["id", "text"], [])
    with pytest.raises(LabelingError, match="没有数据行"):
        merge_labels(empty_sample, labels_csv, tmp_path / "merged.csv", strict=False)


# =========================================================================
# 3. CLI
# =========================================================================

def test_cli_help_and_no_subcommand(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0

    assert main([]) == 0
    captured = capsys.readouterr()
    assert "export" in captured.out
    assert "merge" in captured.out


def test_cli_help_subprocess_exits_zero():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "src.crawler.labeling", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "export" in proc.stdout and "merge" in proc.stdout


def test_cli_export_then_merge(tmp_path, capsys):
    source = _make_comments_csv(tmp_path)
    sample = tmp_path / "sample.csv"
    code = main(
        [
            "export",
            "--input", str(source),
            "--output", str(sample),
            "--sample-size", "4",
            "--seed", "7",
        ]
    )
    assert code == 0
    assert sample.exists()
    assert (tmp_path / "sample.manifest.json").exists()
    exported = capsys.readouterr().out
    assert "已导出" in exported

    ids = [row["id"] for row in _read_csv(sample)]
    labels = _write_labels(
        tmp_path / "labels.csv",
        [
            (record_id, "positive" if index == 0 else "negative")
            for index, record_id in enumerate(ids)
        ],
    )
    merged = tmp_path / "merged.csv"
    code = main(
        ["merge", "--sample", str(sample), "--labels", str(labels), "--output", str(merged)]
    )
    assert code == 0
    assert merged.exists()
    assert "已合并" in capsys.readouterr().out
    assert len(_read_csv(merged)) == 4


def test_cli_merge_strict_failure_returns_1_and_lenient_flag(tmp_path, capsys):
    sample_csv, ids = _sample_with_ids(tmp_path, 3)
    labels_csv = _write_labels(
        tmp_path / "labels.csv",
        [(ids[0], "positive"), (ids[1], "neutral")],
    )
    output = tmp_path / "merged.csv"

    assert (
        main(
            [
                "merge",
                "--sample", str(sample_csv),
                "--labels", str(labels_csv),
                "--output", str(output),
            ]
        )
        == 1
    )
    assert "失败" in capsys.readouterr().err
    assert not output.exists()

    assert (
        main(
            [
                "merge", "--sample", str(sample_csv), "--labels", str(labels_csv),
                "--output", str(output), "--no-strict",
            ]
        )
        == 0
    )
    assert output.exists()
    assert len(_read_csv(output)) == 1
