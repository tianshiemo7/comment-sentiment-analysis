"""Reproduce labelled training inputs; keep unlabelled Bilibili comments separate.

Run from the repository root: python -m scripts.prepare_training_data
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.crawler.datasets import DatasetError, download_dataset, get_dataset
from src.preprocessing import prepare_dataset
from src.preprocessing.dataset import Record, save_records


CORPUS_COMMIT = "a0b781d72158bde242c0626c9e354d2b328865ff"
SOURCES = (
    ("waimai_10k", "waimai_10k/waimai_10k.csv", "b8b5c11c00b51578d35937fca56e03a126b45381f1ea4b22893237cb377a26fe"),
    ("chnsenticorp", "ChnSentiCorp_htl_all/ChnSentiCorp_htl_all.csv", "1d414b9c65caf3a836abdb4efded621968502ad3338e1a9d885ae131c86083f0"),
)


def prepare_training_data(output_root: str | Path = ".", *, overwrite: bool = False) -> dict:
    root = Path(output_root).resolve()
    raw_dir = root / "data/raw/training_corpus"
    combined = raw_dir / "comments_labeled.csv"
    report_path = root / "outputs/stats/training_data_preparation.json"
    if not overwrite and any(path.exists() for path in (combined, report_path, root / "data/processed/manifest.json")):
        raise FileExistsError("训练数据产物已存在，请更换 --output-root 或指定 --overwrite")
    source_reports, records = [], []
    for name, relative, sha256 in SOURCES:
        url = f"https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/{CORPUS_COMMIT}/datasets/{relative}"
        path = download_dataset(name, target_dir=raw_dir, url=url, sha256=sha256,
                                source_json_path=raw_dir / "sources.json", source_note_path=raw_dir / "sources.md")
        spec = get_dataset(name)
        count = 0
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {spec.text_column, spec.label_column} <= set(reader.fieldnames or []):
                raise ValueError(f"{name} 缺少约定的评论/标签列")
            for row_number, row in enumerate(reader, 2):
                # Preserve every original row, including duplicates/conflicts, for
                # the preprocessing audit. Never deduplicate before label checks.
                records.append(Record(row[spec.text_column] or "", row[spec.label_column], meta={
                    "comment_id": f"{name}-{row_number}", "source": name, "source_row": row_number}))
                count += 1
        source_reports.append({"name": name, "url": url, "sha256": sha256, "rows": count,
                               "homepage": spec.homepage, "license_note": spec.license})
    save_records(records, combined)
    result = prepare_dataset(combined, output_root=root, seed=42, min_freq=3, overwrite=overwrite)
    report = {"prepared_at": datetime.now(timezone.utc).isoformat(), "source_commit": CORPUS_COMMIT,
              "sources": source_reports, "annotation": "upstream_existing_labels",
              "domain": "hotel_and_takeaway_reviews", "bilibili_annotations": False,
              "sampling": "all_rows_then_clean_audit_and_stratified_split",
              "dataset_version": result["manifest"]["dataset_version"], "stats": result["stats"],
              "limitation": "规模和类别检查通过不代表哔哩哔哩领域效果；仍需独立人工标注的 B 站测试集评估。"}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="固定公开语料版本，生成可审计的正负二分类训练数据")
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = prepare_training_data(args.output_root, overwrite=args.overwrite)
    except (DatasetError, OSError, ValueError, ImportError) as exc:
        print(f"训练数据制备失败：{exc}", file=sys.stderr)
        return 1
    stats = report["stats"]
    print(json.dumps({"dataset_version": report["dataset_version"], "sources": report["sources"],
                      "accepted": stats["total"], "excluded": stats["excluded_rows"],
                      "splits": {name: item["total"] for name, item in stats["splits"].items()},
                      "course_requirements_met": stats["course_requirements_met"],
                      "report": str(Path(args.output_root).resolve() / "outputs/stats/training_data_preparation.json")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
