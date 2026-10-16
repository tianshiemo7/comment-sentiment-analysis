"""Deterministic stratified or video-grouped splits, without vocabulary fitting."""
from __future__ import annotations

import math
import os
import random
from collections import Counter, defaultdict
from pathlib import Path

from .dataset import Record, save_records

DEFAULT_SEED = int(os.getenv("SEED", "42"))
DEFAULT_RATIOS = (0.7, 0.1, 0.2)
SPLITS_DIR = Path("data") / "splits"
SPLIT_FILES = {"train": "train.csv", "val": "val.csv", "test": "test.csv"}
SPLIT_NAMES = tuple(SPLIT_FILES)


def _validate_ratios(ratios) -> None:
    if len(ratios) != 3 or any(not math.isfinite(r) or r < 0 for r in ratios) or not math.isclose(sum(ratios), 1.0):
        raise ValueError("ratios 必须是和为 1 的三个非负有限数")


def _allocate_counts(total: int, ratios) -> list[int]:
    targets = [total * ratio for ratio in ratios]
    counts = [math.floor(value) for value in targets]
    for idx in sorted(range(3), key=lambda i: (-(targets[i] - counts[i]), i))[:total - sum(counts)]:
        counts[idx] += 1
    active = [i for i, ratio in enumerate(ratios) if ratio > 0]
    if total >= len(active):
        for idx in active:
            if counts[idx] == 0:
                donor = max(active, key=lambda i: (counts[i] - targets[i], counts[i]))
                if counts[donor] > 1:
                    counts[donor] -= 1
                    counts[idx] += 1
    return counts


def resolve_group_key(records: list[Record], group_key: str | None = "auto") -> str | None:
    if group_key in (None, "none"):
        return None
    key = "video_id" if group_key == "auto" else group_key
    present = [record.meta.get(key) not in (None, "") for record in records]
    if any(present) and not all(present):
        raise ValueError(f"分组字段 {key} 部分缺失，请补齐后划分，或明确使用 --group-by none")
    if all(present) and records:
        return key
    if group_key != "auto" and records:
        raise ValueError(f"缺少分组字段：{key}")
    return None


def _group_split(records, ratios, seed, key, stratify):
    groups = defaultdict(list)
    for record in records:
        groups[str(record.meta[key])].append(record)
    labels = sorted({record.label for record in records}, key=lambda label: str(label))
    totals = Counter(record.label for record in records)
    active = [i for i, ratio in enumerate(ratios) if ratio > 0]
    target_n = [len(records) * ratio for ratio in ratios]
    target_labels = [{label: totals[label] * ratio for label in labels} for ratio in ratios]
    best_score, best = math.inf, None
    # Search uses only group/label counts, never model validation or test scores.
    for attempt in range(24):
        rng = random.Random(seed + attempt)
        ordered = list(sorted(groups.items()))
        rng.shuffle(ordered)
        ordered.sort(key=lambda item: -len(item[1]))
        partitions = [[] for _ in range(3)]
        counts, label_counts = [0, 0, 0], [Counter() for _ in range(3)]
        for _, items in ordered:
            sizes = Counter(record.label for record in items)
            def cost(idx):
                before, after = counts[idx] - target_n[idx], counts[idx] + len(items) - target_n[idx]
                delta = (after * after - before * before) / max(target_n[idx], 1)
                if stratify:
                    delta += sum(
                        ((label_counts[idx][label] + sizes[label] - target_labels[idx][label]) ** 2
                         - (label_counts[idx][label] - target_labels[idx][label]) ** 2)
                        / max(target_labels[idx][label], 1) for label in labels
                    )
                return delta
            costs = {idx: cost(idx) for idx in active}
            minimum = min(costs.values())
            candidates = [idx for idx in active if math.isclose(costs[idx], minimum, abs_tol=1e-9)]
            chosen = rng.choice(candidates)
            partitions[chosen].extend(items)
            counts[chosen] += len(items)
            label_counts[chosen].update(sizes)
        score = sum((counts[i] - target_n[i]) ** 2 / max(target_n[i], 1) for i in active)
        if stratify:
            score += sum((label_counts[i][label] - target_labels[i][label]) ** 2
                         / max(target_labels[i][label], 1) for i in active for label in labels)
            score += 1e6 * sum(not label_counts[i][label] for i in active for label in labels)
        score += 1e6 * sum(not counts[i] for i in active)
        if score < best_score:
            best_score, best = score, partitions
    return dict(zip(SPLIT_NAMES, best))


def split_dataset(
    records: list[Record], ratios: tuple[float, float, float] = DEFAULT_RATIOS, *,
    seed: int = DEFAULT_SEED, stratify: bool = True, group_key: str | None = "auto",
) -> dict[str, list[Record]]:
    """Auto-group by video_id when present; otherwise use label-stratified splitting.

    Rows must already be cleaned and deduplicated. Grouped counts approximate
    the ratios; groups are never broken to fill an exact row quota.
    """
    _validate_ratios(ratios)
    if len({record.text for record in records}) != len(records):
        raise ValueError("存在重复文本，请先清洗去重再划分")
    key = resolve_group_key(records, group_key)
    if key:
        result = _group_split(records, ratios, seed, key, stratify)
    else:
        rng = random.Random(seed)
        strata = defaultdict(list)
        for record in records:
            strata[record.label if stratify else None].append(record)
        result = {name: [] for name in SPLIT_NAMES}
        for label in sorted(strata, key=lambda value: str(value)):
            items = list(strata[label])
            rng.shuffle(items)
            counts = _allocate_counts(len(items), ratios)
            start = 0
            for name, count in zip(SPLIT_NAMES, counts):
                result[name].extend(items[start:start + count])
                start += count
    rng = random.Random(seed)
    for records_in_split in result.values():
        rng.shuffle(records_in_split)
    validate_split_integrity(result, group_key=key)
    return result


def validate_split_integrity(splits: dict[str, list[Record]], *, group_key: str | None = None) -> None:
    seen = {"text": set(), "comment_id": set(), "group": set()}
    for records in splits.values():
        current = {
            "text": {record.text for record in records},
            "comment_id": {str(record.meta["comment_id"]) for record in records if record.meta.get("comment_id") not in (None, "")},
            "group": {str(record.meta[group_key]) for record in records} if group_key else set(),
        }
        for field, values in current.items():
            if seen[field] & values:
                raise ValueError(f"集合之间存在数据泄漏：{field}")
            seen[field].update(values)


def split_dataset_to_files(
    records: list[Record], *, ratios: tuple[float, float, float] = DEFAULT_RATIOS,
    seed: int = DEFAULT_SEED, stratify: bool = True, out_dir: str | Path = SPLITS_DIR,
    group_key: str | None = "auto",
) -> dict[str, Path]:
    splits = split_dataset(records, ratios, seed=seed, stratify=stratify, group_key=group_key)
    return {name: save_records(items, Path(out_dir) / SPLIT_FILES[name]) for name, items in splits.items()}
