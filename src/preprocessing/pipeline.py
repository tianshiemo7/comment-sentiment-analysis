"""Offline preparation: audit, deduplicate, split, train-only vocabulary, encode."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from .dataset import Record, load_records, save_records
from .processor import LABEL_MAP, PIPELINE_VERSION, TextPreprocessor, save_encoded_split
from .split import DEFAULT_RATIOS, DEFAULT_SEED, resolve_group_key, split_dataset, validate_split_integrity
from .stats import dataset_stats
from .vocab import build_vocab


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_payload(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _prepare_records(records, processor, input_hash, *, require_labels=True):
    candidates, excluded = [], []
    def reject(record, reason):
        excluded.append(replace(record, meta={**record.meta, "status": "excluded", "exclude_reason": reason}))

    for record in records:
        text, tokens = processor.transform(record.text)
        meta = {**record.meta, "text_raw": record.text}
        meta["comment_id"] = str(meta.get("comment_id") or meta.get("id") or f"import-{input_hash[:12]}-{meta['input_row']}")
        meta["text_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        prepared = Record(text, record.label, " ".join(tokens), meta)
        if meta.get("invalid_text_type"):
            reject(prepared, "invalid_text_type")
        elif not text:
            reject(prepared, "empty_after_cleaning")
        elif require_labels and not record.label:
            reject(prepared, "missing_label")
        elif require_labels and record.label not in LABEL_MAP:
            reject(prepared, record.label if record.label in {"neutral", "uncertain", "irrelevant"} else "unsupported_label")
        elif not tokens:
            reject(prepared, "empty_after_tokenization")
        else:
            candidates.append(prepared)

    text_labels, id_signatures = defaultdict(set), defaultdict(set)
    for record in candidates:
        text_labels[record.text].add(record.label)
        id_signatures[record.meta["comment_id"]].add((record.text, record.label))
    seen_texts, seen_ids, accepted = set(), set(), []
    for record in candidates:
        comment_id = record.meta["comment_id"]
        if len(text_labels[record.text]) > 1:
            reject(record, "conflicting_text_labels")
        elif len(id_signatures[comment_id]) > 1:
            reject(record, "conflicting_comment_id")
        elif comment_id in seen_ids:
            reject(record, "duplicate_comment_id")
        elif record.text in seen_texts:
            reject(record, "duplicate_text")
        else:
            accepted.append(record)
            seen_texts.add(record.text)
            seen_ids.add(comment_id)
    return accepted, excluded


def detect_processing_mode(records: list[Record]) -> str:
    """No labels means pending predictions, not an automatically labelled dataset."""
    labelled = [record.label is not None for record in records if record.text.strip()]
    if not any(labelled):
        return "clean"
    if not all(labelled):
        raise ValueError("CSV 混有有标签和无标签评论，请显式指定 --mode clean 保留待分析评论，或 --mode train 制备标注训练集")
    return "train"


def prepare_comments(
    input_path: str | Path, *, output_root: str | Path = ".", processor: TextPreprocessor | None = None,
    text_column: str | None = None, sheet_name: str | None = None, overwrite: bool = False,
) -> dict:
    """Clean a crawler CSV for the next sentiment-prediction stage, without fitting.

    No labels are invented; no training split, vocabulary or model IDs are built.
    The downstream predictor should use text_raw with its own trained resources.
    """
    input_path, root = Path(input_path).resolve(), Path(output_root).resolve()
    processor = processor or TextPreprocessor.from_resources()
    input_hash = _hash_file(input_path)
    records = load_records(input_path, text_column=text_column, sheet_name=sheet_name, keep_empty=True)
    accepted, excluded = _prepare_records(records, processor, input_hash, require_labels=False)
    if not accepted:
        reasons = dict(Counter(record.meta["exclude_reason"] for record in excluded))
        raise ValueError(f"没有有效的待分析评论：{reasons}")
    for record in accepted:
        # Retain any upstream annotation as metadata; never present it as a prediction.
        if record.label is not None:
            record.meta["provided_label"] = record.label
        record.label = None
        record.meta["status"] = "ok"
    stats = dataset_stats([record.tokens.split() for record in accepted],
                          max_len=processor.max_len, out_path=None)
    stats.update({"mode": "clean", "has_sentiment_labels": False, "input_rows": len(records),
                  "excluded_rows": len(excluded), "pipeline_version": PIPELINE_VERSION,
                  "exclusion_reasons": dict(sorted(Counter(record.meta["exclude_reason"] for record in excluded).items())),
                  "next_step": "sentiment_prediction", "vocab_fitted": False,
                  "video_count": len({str(record.meta["video_id"]) for record in accepted
                                      if record.meta.get("video_id") not in (None, "")})})
    processed = root / "data" / "processed"
    stats_path = root / "outputs" / "stats" / "comments_stats.json"
    manifest_path = processed / "comments_manifest.json"
    targets = [processed / name for name in ("comments_cleaned.csv", "comments_excluded.csv", "comments_preprocessor.json")]
    targets += [stats_path, manifest_path]
    if input_path in targets:
        raise ValueError("输入文件不能与处理产物路径相同")
    existing = [str(path.relative_to(root)) for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"输出文件已存在：{existing}；更换 --output-root 或显式指定 --overwrite")
    save_records(accepted, processed / "comments_cleaned.csv")
    save_records(excluded, processed / "comments_excluded.csv")
    processor.save(processed / "comments_preprocessor.json")
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"mode": "clean", "pipeline_version": PIPELINE_VERSION, "input_sha256": input_hash,
              "preprocessor_sha256": _hash_payload(processor.to_dict()), "text_column": text_column,
              "sheet_name": sheet_name,
              "records_sha256": _hash_payload([(record.meta["comment_id"], record.meta["text_hash"]) for record in accepted])}
    manifest = {**config, "dataset_version": _hash_payload(config)[:16], "input_file": input_path.name,
                "vocab_fitted": False, "next_step": "sentiment_prediction",
                "files": {str(path.relative_to(root)).replace("\\", "/"): _hash_file(path)
                          for path in targets if path != manifest_path}}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stats": stats, "manifest": manifest, "output_root": str(root)}


def _course_checks(splits, ratios, min_train, min_test):
    errors = []
    total = sum(map(len, splits.values()))
    if len(splits["train"]) < min_train:
        errors.append(f"训练集 {len(splits['train'])} 条，少于 {min_train} 条")
    if len(splits["test"]) < min_test:
        errors.append(f"测试集 {len(splits['test'])} 条，少于 {min_test} 条")
    for (name, records), ratio in zip(splits.items(), ratios):
        labels = Counter(record.label for record in records)
        if not records or set(labels) != set(LABEL_MAP):
            errors.append(f"{name} 未覆盖正负两个类别")
        elif not .45 <= labels["positive"] / len(records) <= .55:
            errors.append(f"{name} 正面占比不在项目建议的 45% 至 55% 范围内")
        if total and abs(len(records) / total - ratio) > .05:
            errors.append(f"{name} 条数比例偏离目标超过 5 个百分点")
    return errors


def prepare_dataset(
    input_path: str | Path, *, output_root: str | Path = ".", processor: TextPreprocessor | None = None,
    text_column: str | None = None, sheet_name: str | None = None,
    seed: int = DEFAULT_SEED, ratios: tuple = DEFAULT_RATIOS, group_key: str | None = "auto",
    min_freq: int = 3, max_vocab_size: int | None = 30000,
    allow_small_dataset: bool = False, overwrite: bool = False,
) -> dict:
    input_path, root = Path(input_path).resolve(), Path(output_root).resolve()
    processor = processor or TextPreprocessor.from_resources()
    # Read first, but publish no model-facing artifacts until all hard checks pass.
    input_hash = _hash_file(input_path)
    records = load_records(input_path, text_column=text_column, sheet_name=sheet_name, keep_empty=True)
    accepted, excluded = _prepare_records(records, processor, input_hash)
    if not accepted:
        reasons = dict(Counter(record.meta["exclude_reason"] for record in excluded))
        raise ValueError(f"没有可用于正负情感分类的有效标注样本：{reasons}")
    effective_group = resolve_group_key(accepted, group_key)
    splits = split_dataset(accepted, ratios, seed=seed, group_key=group_key)
    validate_split_integrity(splits, group_key=effective_group)
    if not splits["train"]:
        raise ValueError("训练集为空，请增加样本或分组数量")
    checks = _course_checks(splits, ratios, 8000, 2000)
    if checks and not allow_small_dataset:
        raise ValueError("数据验收未通过：" + "；".join(checks) + "。仅调试可显式使用 --allow-small-dataset")

    for name, items in splits.items():
        for record in items:
            record.meta["split"] = name
    train_tokens = [record.tokens.split() for record in splits["train"]]
    vocab = build_vocab(train_tokens, min_freq=min_freq, max_size=max_vocab_size)
    if len(vocab) == 2 and not allow_small_dataset:
        raise ValueError("训练词表没有满足 min_freq 的普通词，请检查数据和分词结果")
    warnings = []
    if effective_group is None:
        warnings.append("未提供 video_id，采用按标签分层划分；不能据此声称跨视频独立评估")
    if len(vocab) == 2:
        warnings.append("小样本词表只有 PAD/UNK，不能用作有效情感模型实验")
    stats = dataset_stats([record.tokens.split() for record in accepted],
                          [record.label for record in accepted], vocab, max_len=processor.max_len, out_path=None)
    stats.update({
        "pipeline_version": PIPELINE_VERSION, "input_rows": len(records), "excluded_rows": len(excluded),
        "exclusion_reasons": dict(sorted(Counter(record.meta["exclude_reason"] for record in excluded).items())),
        "seed": seed, "target_ratios": list(ratios), "group_key": effective_group,
        "split_method": "grouped_stratified" if effective_group else "stratified",
        "course_requirements_met": not checks, "course_check_issues": checks,
        "demo_mode": allow_small_dataset, "warnings": warnings,
        "splits": {
            name: dataset_stats([record.tokens.split() for record in items], [record.label for record in items],
                                vocab, max_len=processor.max_len, out_path=None)
            | {"groups": len({str(record.meta[effective_group]) for record in items}) if effective_group else None}
            for name, items in splits.items()
        },
    })
    processed = root / "data" / "processed"
    split_dir = root / "data" / "splits"
    stats_dir = root / "outputs" / "stats"
    targets = [processed / name for name in ("cleaned.csv", "excluded.csv", "vocab.json", "preprocessor.json", "label_map.json", "split_manifest.csv", "manifest.json")]
    targets += [split_dir / f"{name}.{suffix}" for name in splits for suffix in ("csv", "npz")]
    targets += [stats_dir / "dataset_stats.json"]
    if input_path in targets:
        raise ValueError("输入文件不能与处理产物路径相同")
    existing = [str(path.relative_to(root)) for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"输出文件已存在：{existing}；更换 --output-root 或显式指定 --overwrite")
    # Fail before publishing CSV/resources if the model-array dependency is absent.
    import numpy  # noqa: F401

    save_records(accepted, processed / "cleaned.csv")
    save_records(excluded, processed / "excluded.csv")
    vocab.save(processed / "vocab.json")
    processor.save(processed / "preprocessor.json")
    (processed / "label_map.json").write_text(json.dumps(LABEL_MAP, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, items in splits.items():
        save_records(items, split_dir / f"{name}.csv")
        save_encoded_split(items, processor, vocab, split_dir / f"{name}.npz")
    # Minimal lineage manifest, without comment contents.
    manifests = [Record("", record.label, meta={key: value for key, value in record.meta.items()
                 if key in {"comment_id", "video_id", "input_row", "input_file", "split", "text_hash"}})
                 for record in accepted]
    save_records(manifests, processed / "split_manifest.csv")
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "dataset_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"input_sha256": input_hash, "preprocessor_sha256": _hash_payload(processor.to_dict()),
              "seed": seed, "ratios": list(ratios), "group_key": effective_group,
              "min_freq": min_freq, "max_vocab_size": max_vocab_size, "pipeline_version": PIPELINE_VERSION,
              "text_column": text_column, "sheet_name": sheet_name,
              "split_sha256": _hash_payload({name: [(record.meta["comment_id"], record.meta["text_hash"], record.label)
                                                    for record in items] for name, items in splits.items()})}
    manifest = {**config, "dataset_version": _hash_payload(config)[:16], "input_file": input_path.name,
                "label_map": LABEL_MAP, "vocab_fit_split": "train",
                "python_dependency_versions": {
                    package: importlib.metadata.version(package)
                    for package in (["numpy", "jieba"] if processor.lang == "zh" else ["numpy"])
                },
                "files": {str(path.relative_to(root)).replace("\\", "/"): _hash_file(path)
                          for path in targets if path.name != "manifest.json"}}
    (processed / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stats": stats, "manifest": manifest, "output_root": str(root)}
