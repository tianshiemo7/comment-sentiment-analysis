"""人工标注工作流：确定性抽样导出 + 标注合并校验 + manifest 审计。

课程要求「自采数据有标注规范与抽样一致性说明」（``docs/验收检查清单.md``），
并且训练语料要达到 ≥ 8000 / ≥ 2000 条。爬虫产出的 B 站评论**没有情感标签**，
需要人工标注。本模块把这一步拆成两个可复现的动作：

1. :func:`export_labeling_sample`：从爬虫 CSV（无标签）里**确定性地**抽取
   标注候选，按 ``video_id`` / ``bvid`` 分层，排除已标注、重复文本与空文本，
   输出 ``id,text,group,video_id,label``（``label`` 留空）并写 manifest JSON
   （总数、抽样数、seed、排除原因统计、输入哈希），保证「换台机器同 seed
   抽到同一批样本」；
2. :func:`merge_labels`：把人工标注结果（``id,label``）合并回样本，校验
   标签取值（只允许 ``positive`` / ``negative``）、重复 id、缺失/空标签、
   同一 id 冲突，输出最终训练 CSV 与 manifest（accepted / excluded 统计、
   正负计数）。``strict=False`` 时跳过非法行并如实记录，便于边标边查。

命令行入口（``python -m src.crawler.labeling``）::

    # 导出 500 条候选，按视频分层，同 seed 可复现
    python -m src.crawler.labeling export \
        --input data/raw/BV1UB4y1W7Xu_comments.csv \
        --output data/labeling/sample.csv --sample-size 500 --seed 42

    # 标注完成后合并（严格校验，任何问题都会失败）
    python -m src.crawler.labeling merge \
        --sample data/labeling/sample.csv --labels data/labeling/labels.csv \
        --output data/raw/comments_labeled.csv

    # 边标边查：允许部分未标完，非法行写入 manifest 的 excluded 统计
    python -m src.crawler.labeling merge --no-strict ...

所有函数只依赖标准库，输出 CSV 为 UTF-8 BOM（Excel 可直接打开），文本用
:func:`src.crawler.normalize.normalize_text` 标准化，与爬虫保持一致。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import CrawlerError
from .normalize import normalize_text, text_signature, utc_iso

logger = logging.getLogger("labeling")

#: 导出样本的固定列顺序；``label`` 留空等人工填写
SAMPLE_COLUMNS = ("id", "text", "group", "video_id", "label")

#: 合法情感标签（与 ``data/README.md``、``src/preprocessing`` 的约定一致）
LABELS = ("positive", "negative")

GROUP_COLUMN_ALIASES = ("video_id", "bvid", "group")
ID_COLUMN_ALIASES = ("id", "comment_id", "rpid")
LABEL_COLUMN_ALIASES = ("label", "sentiment", "polarity")

#: 排除原因（manifest 中始终包含这些键，0 也保留，便于对比各次运行）
EXPORT_EXCLUDE_REASONS = (
    "already_labeled",
    "duplicate_text",
    "empty_text",
    "duplicate_id",
)
MERGE_EXCLUDE_REASONS = (
    "missing_label",
    "empty_label",
    "invalid_label",
    "empty_id",
    "duplicate_id",
    "conflicting_label",
    "duplicate_sample_id",
)


class LabelingError(CrawlerError):
    """标注抽样/合并过程中的可解释失败（参数非法、文件缺失、校验不通过等）。"""


# =========================================================================
# 通用小工具
# =========================================================================

def _sha256_file(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_csv(path: str | Path, *, what: str = "CSV") -> tuple[list[str], list[dict[str, str]]]:
    target = Path(path)
    if not target.exists():
        raise LabelingError(f"{what} 文件不存在：{target}")
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise LabelingError(f"读取 {what} 失败：{target}（{exc}）") from exc
    if not header:
        raise LabelingError(f"{what} 没有表头或为空：{target}")
    return header, rows


def _write_csv(path: str | Path, columns: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> Path:
    """原子写入 UTF-8 BOM 的 CSV（先临时文件再替换，避免半成品）。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(columns)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover - 防御式
                pass
    return target


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """原子写入 UTF-8 JSON（manifest）。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=str(target.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover - 防御式
                pass
    return target


def _default_manifest_path(output_csv: str | Path) -> Path:
    output = Path(output_csv)
    return output.with_name(f"{output.stem}.manifest.json")


def _pick_column(
    header: Iterable[str],
    preferred: str | None,
    aliases: Iterable[str],
    what: str,
) -> str | None:
    """按「显式列名 → 别名」顺序在表头中挑列；找不到返回 None。"""

    columns = list(header)
    if preferred and preferred in columns:
        return preferred
    for alias in aliases:
        if alias in columns:
            return alias
    return None


def _allocate_groups(group_sizes: Mapping[str, int], target: int) -> dict[str, int]:
    """按组规模比例分配抽样名额（最大余数法，纯整数、确定性）。

    分配结果之和严格等于 ``min(target, sum(group_sizes))``；组越大多分，
    余数相同时按组名字典序，保证不同机器结果一致。
    """

    total = sum(group_sizes.values())
    if target <= 0 or total <= 0:
        return {group: 0 for group in group_sizes}
    if target >= total:
        return dict(group_sizes)
    allocations: dict[str, int] = {}
    remainders: dict[str, int] = {}
    for group, size in group_sizes.items():
        base, remainder = divmod(size * target, total)
        allocations[group] = base
        remainders[group] = remainder
    leftover = target - sum(allocations.values())
    ranked = sorted(group_sizes, key=lambda group: (-remainders[group], group))
    for group in ranked[:leftover]:
        allocations[group] += 1
    return allocations


# =========================================================================
# 1. 导出标注候选
# =========================================================================

@dataclass
class ExportResult:
    """:func:`export_labeling_sample` 的返回值（manifest 同时落盘）。"""

    input_csv: str
    output_csv: str
    manifest_path: str
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def sampled(self) -> int:
        return int(self.manifest.get("sampled", 0))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.manifest)


def export_labeling_sample(
    input_csv: str | Path,
    output_csv: str | Path,
    sample_size: int,
    seed: int = 42,
    text_column: str = "text",
    group_column: str = "video_id",
    exclude_labeled: bool = True,
    exclude_duplicate_text: bool = True,
    *,
    label_column: str = "label",
    manifest_path: str | Path | None = None,
) -> ExportResult:
    """确定性地导出人工标注候选样本（按分组分层）。

    Args:
        input_csv: 输入 CSV（爬虫输出或其它无标签表格）。
        output_csv: 输出 CSV，列固定为 ``id,text,group,video_id,label``。
        sample_size: 期望抽样条数；超过候选总数时取全部。
        seed: 随机种子；同输入 + 同 seed 的输出完全一致。
        text_column: 文本列名（缺失时报错）。
        group_column: 分层列名，默认 ``video_id``；不存在时依次回退到
            ``video_id`` / ``bvid`` / ``group``，都没有则视为单组。
        exclude_labeled: 是否排除已带标签的行（``label_column`` 非空）。
        exclude_duplicate_text: 是否按文本签名去重。
        label_column: 已标注判断所用列名（不存在时不做已标注排除）。
        manifest_path: manifest 输出路径，默认 ``<output 同目录>/<stem>.manifest.json``。

    Returns:
        :class:`ExportResult`（含 manifest 内容与路径）。

    Raises:
        LabelingError: 参数非法、文件/列缺失、没有可抽样数据。
    """

    if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size <= 0:
        raise LabelingError(f"sample_size 必须为正整数，收到 {sample_size!r}")

    header, rows = _read_csv(input_csv, what="输入 CSV")
    text_col = _pick_column(header, text_column, (text_column,), "text")
    if text_col is None:
        raise LabelingError(
            f"{input_csv} 缺少文本列 {text_column!r}；实际表头：{header}"
        )
    resolved_group_col = _pick_column(header, group_column, GROUP_COLUMN_ALIASES, "group")
    has_label_column = label_column in header
    id_col = _pick_column(header, "id", ID_COLUMN_ALIASES, "id")

    excluded: dict[str, int] = {reason: 0 for reason in EXPORT_EXCLUDE_REASONS}
    seen_texts: set[str] = set()
    used_ids: set[str] = set()
    candidates: list[dict[str, Any]] = []
    group_sizes: dict[str, int] = {}

    for index, row in enumerate(rows):
        text = normalize_text(row.get(text_col))
        if not text:
            excluded["empty_text"] += 1
            continue
        if exclude_labeled and has_label_column:
            if str(row.get(label_column) or "").strip():
                excluded["already_labeled"] += 1
                continue
        if exclude_duplicate_text:
            signature = text_signature(text)
            if signature in seen_texts:
                excluded["duplicate_text"] += 1
                continue
        else:
            signature = text_signature(text)

        group = str(row.get(resolved_group_col) or "").strip() if resolved_group_col else ""
        if not group:
            group = "unknown"
        video_id = ""
        for column in ("video_id", "bvid"):
            if column in header:
                video_id = str(row.get(column) or "").strip()
                if video_id:
                    break
        if not video_id:
            video_id = group if group != "unknown" else ""

        if id_col and str(row.get(id_col) or "").strip():
            record_id = str(row.get(id_col)).strip()
        else:
            # 没有 id 列时用「分组 + 文本 + 原始行号」生成稳定短 id；
            # 带行号是为了在 --keep-duplicates 时同文本仍能得到不同 id。
            record_id = hashlib.sha1(
                f"{group}\x00{text}\x00{index}".encode("utf-8")
            ).hexdigest()[:16]
        if record_id in used_ids:
            excluded["duplicate_id"] += 1
            continue
        used_ids.add(record_id)
        seen_texts.add(signature)

        record = {
            "id": record_id,
            "text": text,
            "group": group,
            "video_id": video_id,
            "label": "",
        }
        candidates.append(record)
        group_sizes[group] = group_sizes.get(group, 0) + 1

    if not candidates:
        raise LabelingError(f"{input_csv} 没有可抽样的有效评论（全部被过滤）")

    target = min(sample_size, len(candidates))
    allocations = _allocate_groups(group_sizes, target)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in candidates:
        grouped.setdefault(record["group"], []).append(record)
    sampled: list[dict[str, Any]] = []
    sampled_by_group: dict[str, int] = {}
    for group in sorted(grouped):
        rng = random.Random(f"{seed}\x00{group}")
        pool = list(grouped[group])
        rng.shuffle(pool)
        take = min(allocations.get(group, 0), len(pool))
        sampled.extend(pool[:take])
        sampled_by_group[group] = take
    sampled.sort(key=lambda record: (record["group"], record["id"]))

    output = _write_csv(output_csv, SAMPLE_COLUMNS, sampled)
    resolved_manifest = (
        Path(manifest_path) if manifest_path is not None else _default_manifest_path(output)
    )
    manifest: dict[str, Any] = {
        "kind": "labeling_sample_export",
        "schema_version": 1,
        "created_at": utc_iso(),
        "input_csv": str(Path(input_csv)),
        "input_sha256": _sha256_file(input_csv),
        "input_rows": len(rows),
        "eligible_rows": len(candidates),
        "sample_size_requested": sample_size,
        "sampled": len(sampled),
        "seed": seed,
        "group_column": resolved_group_col,
        "text_column": text_col,
        "exclude_labeled": bool(exclude_labeled),
        "exclude_duplicate_text": bool(exclude_duplicate_text),
        "excluded": excluded,
        "group_counts": {group: group_sizes[group] for group in sorted(group_sizes)},
        "sampled_by_group": {group: sampled_by_group[group] for group in sorted(sampled_by_group)},
        "output_csv": str(output),
        "output_sha256": _sha256_file(output),
        "columns": list(SAMPLE_COLUMNS),
        "note": (
            "同输入 + 同 seed 抽样结果完全一致；group 分层按组规模比例分配名额，"
            "余数相同按组名字典序。label 列留空，等待人工标注后用 "
            "python -m src.crawler.labeling merge 合并。"
        ),
    }
    _write_json(resolved_manifest, manifest)
    logger.info(
        "已导出标注样本：%s → %s（抽样 %d / 候选 %d，seed=%d）",
        input_csv,
        output,
        len(sampled),
        len(candidates),
        seed,
    )
    return ExportResult(
        input_csv=str(Path(input_csv)),
        output_csv=str(output),
        manifest_path=str(resolved_manifest),
        manifest=manifest,
    )


# =========================================================================
# 2. 合并人工标注
# =========================================================================

@dataclass
class MergeResult:
    """:func:`merge_labels` 的返回值（manifest 同时落盘）。"""

    sample_csv: str
    labels_csv: str
    output_csv: str
    manifest_path: str
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> int:
        return int(self.manifest.get("accepted", 0))

    @property
    def excluded(self) -> dict[str, int]:
        return dict(self.manifest.get("excluded", {}))

    @property
    def label_counts(self) -> dict[str, int]:
        return dict(self.manifest.get("label_counts", {}))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.manifest)


def merge_labels(
    sample_csv: str | Path,
    labels_csv: str | Path,
    output_csv: str | Path,
    id_column: str = "id",
    label_column: str = "label",
    *,
    strict: bool = True,
    manifest_path: str | Path | None = None,
) -> MergeResult:
    """把人工标注结果合并回样本，输出最终训练 CSV。

    校验规则（``strict=True`` 时任何一条都会抛 :class:`LabelingError`）：

    - 标签只允许 ``positive`` / ``negative``（大小写不敏感），空标签记
      ``empty_label``，其它值记 ``invalid_label``；
    - 标注文件内同一 id 出现多次：同标签记 ``duplicate_id``，不同标签记
      ``conflicting_label``（该 id 两票作废，不猜哪条对）；
    - 样本里没有对应标注记 ``missing_label``；
    - 标注行 id 为空记 ``empty_id``。

    Args:
        sample_csv: 导出的样本 CSV（含 ``id`` 与文本等列）。
        labels_csv: 人工标注 CSV（至少含 id 与标签两列）。
        output_csv: 输出 CSV：样本原列 + 填充后的 ``label``。
        id_column: 标注文件的 id 列；找不到时回退 ``id`` / ``comment_id`` / ``rpid``。
        label_column: 标注文件的标签列；找不到时回退 ``label`` / ``sentiment`` / ``polarity``。
        strict: True = 要求全部样本都有合法标注且无冲突，否则失败；False = 跳过
            非法/缺失行并写入 manifest 的 excluded 统计（可边标边查）。
        manifest_path: manifest 输出路径，默认与输出 CSV 同目录同主名。

    Returns:
        :class:`MergeResult`（含 accepted / excluded / label_counts）。

    Raises:
        LabelingError: 严格模式下存在任何校验问题，或文件/列缺失、无可合并数据。
    """

    sample_header, sample_rows = _read_csv(sample_csv, what="样本 CSV")
    resolved_sample_id = _pick_column(sample_header, id_column, ID_COLUMN_ALIASES, "id")
    if resolved_sample_id is None:
        raise LabelingError(
            f"{sample_csv} 缺少 id 列（期望 {id_column!r} 或别名 {ID_COLUMN_ALIASES}）；"
            f"实际表头：{sample_header}"
        )

    labels_header, labels_rows = _read_csv(labels_csv, what="标注 CSV")
    resolved_label_id = _pick_column(labels_header, id_column, ID_COLUMN_ALIASES, "id")
    resolved_label = _pick_column(labels_header, label_column, LABEL_COLUMN_ALIASES, "label")
    if resolved_label_id is None or resolved_label is None:
        raise LabelingError(
            f"{labels_csv} 缺少 id 或标签列（期望 {id_column!r} / {label_column!r}）；"
            f"实际表头：{labels_header}"
        )

    excluded: dict[str, int] = {reason: 0 for reason in MERGE_EXCLUDE_REASONS}
    label_by_id: dict[str, str] = {}
    invalid_ids: set[str] = set()
    empty_label_ids: set[str] = set()
    conflict_ids: set[str] = set()
    problems: list[str] = []

    for row in labels_rows:
        record_id = str(row.get(resolved_label_id) or "").strip()
        if not record_id:
            excluded["empty_id"] += 1
            if len(problems) < 5:
                problems.append("标注行 id 为空")
            continue
        raw_label = str(row.get(resolved_label) or "").strip()
        if not raw_label:
            excluded["empty_label"] += 1
            empty_label_ids.add(record_id)
            if len(problems) < 5:
                problems.append(f"id={record_id} 标签为空")
            continue
        label = raw_label.casefold()
        if label not in LABELS:
            excluded["invalid_label"] += 1
            invalid_ids.add(record_id)
            if len(problems) < 5:
                problems.append(f"id={record_id} 标签非法：{raw_label!r}")
            continue
        if record_id in label_by_id:
            if label_by_id[record_id] != label:
                conflict_ids.add(record_id)
                excluded["conflicting_label"] += 1
                if len(problems) < 5:
                    problems.append(
                        f"id={record_id} 标签冲突：{label_by_id[record_id]!r} vs {label!r}"
                    )
            else:
                excluded["duplicate_id"] += 1
                if len(problems) < 5:
                    problems.append(f"id={record_id} 在标注文件中重复")
            continue
        label_by_id[record_id] = label
        # 同一 id 若先出现空/非法标签、后又补上合法标签，以合法行为准
        invalid_ids.discard(record_id)
        empty_label_ids.discard(record_id)

    # 冲突/非法标签的 id 作废（不猜哪条对），并从可用标签里移除
    for broken_id in conflict_ids | invalid_ids | empty_label_ids:
        label_by_id.pop(broken_id, None)

    output_columns = list(sample_header)
    if "label" not in output_columns:
        output_columns.append("label")
    accepted_rows: list[dict[str, Any]] = []
    accepted_ids: set[str] = set()
    label_counts: dict[str, int] = {label: 0 for label in LABELS}

    for row in sample_rows:
        record_id = str(row.get(resolved_sample_id) or "").strip()
        if not record_id:
            excluded["missing_label"] += 1
            continue
        if record_id in accepted_ids:
            excluded["duplicate_sample_id"] += 1
            continue
        label = label_by_id.get(record_id)
        if label is None:
            if record_id in conflict_ids or record_id in invalid_ids:
                pass  # 已在标注扫描阶段计数，避免重复统计
            elif record_id in empty_label_ids:
                pass
            else:
                excluded["missing_label"] += 1
            continue
        accepted_ids.add(record_id)
        label_counts[label] += 1
        merged = dict(row)
        merged["label"] = label
        accepted_rows.append(merged)

    total_excluded = sum(excluded.values())
    if strict and (total_excluded or len(accepted_rows) != len(sample_rows)):
        detail = "；".join(problems) if problems else "存在未标注样本"
        summary = "、".join(f"{reason}={count}" for reason, count in excluded.items() if count)
        raise LabelingError(
            f"标注合并校验未通过（strict=True）：共 {len(sample_rows)} 条样本，"
            f"仅 {len(accepted_rows)} 条可接受；排除统计：{summary or '无'}。"
            f"示例问题：{detail}。如确认可以跳过非法行，请用 strict=False / --no-strict。"
        )
    if not sample_rows:
        raise LabelingError(f"样本文件没有数据行：{sample_csv}")

    output = _write_csv(output_csv, output_columns, accepted_rows)
    resolved_manifest = (
        Path(manifest_path) if manifest_path is not None else _default_manifest_path(output)
    )
    manifest: dict[str, Any] = {
        "kind": "labeling_merge",
        "schema_version": 1,
        "created_at": utc_iso(),
        "strict": bool(strict),
        "sample_csv": str(Path(sample_csv)),
        "sample_sha256": _sha256_file(sample_csv),
        "sample_rows": len(sample_rows),
        "labels_csv": str(Path(labels_csv)),
        "labels_sha256": _sha256_file(labels_csv),
        "labels_rows": len(labels_rows),
        "id_column": resolved_label_id,
        "label_column": resolved_label,
        "accepted": len(accepted_rows),
        "excluded": excluded,
        "excluded_total": total_excluded,
        "label_counts": label_counts,
        "output_csv": str(output),
        "output_sha256": _sha256_file(output),
        "columns": output_columns,
        "note": (
            "label 仅允许 positive / negative；同一 id 冲突时两票作废（不猜哪条对）；"
            "strict=False 时非法/缺失行只记录在 excluded，不写入输出。"
        ),
    }
    _write_json(resolved_manifest, manifest)
    logger.info(
        "已合并标注：%s + %s → %s（接受 %d / 样本 %d，正 %d，负 %d）",
        sample_csv,
        labels_csv,
        output,
        len(accepted_rows),
        len(sample_rows),
        label_counts["positive"],
        label_counts["negative"],
    )
    return MergeResult(
        sample_csv=str(Path(sample_csv)),
        labels_csv=str(Path(labels_csv)),
        output_csv=str(output),
        manifest_path=str(resolved_manifest),
        manifest=manifest,
    )


# =========================================================================
# 命令行入口
# =========================================================================

def _add_manifest_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--manifest", default=None, help="manifest JSON 输出路径（默认与输出 CSV 同目录）"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.crawler.labeling",
        description="人工标注工作流：确定性抽样导出候选样本，合并标注并校验（输出 manifest）。",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{export,merge}")

    export_parser = subparsers.add_parser(
        "export", help="从无标签 CSV 确定性抽样导出标注候选（按视频分层）"
    )
    export_parser.add_argument("--input", required=True, help="输入 CSV（爬虫输出）")
    export_parser.add_argument("--output", required=True, help="输出样本 CSV")
    export_parser.add_argument("--sample-size", type=int, required=True, help="抽样条数")
    export_parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    export_parser.add_argument("--text-column", default="text", help="文本列名（默认 text）")
    export_parser.add_argument(
        "--group-column", default=None, help="分层列名（默认自动识别 video_id / bvid / group）"
    )
    export_parser.add_argument("--label-column", default="label", help="已标注判断列名")
    export_parser.add_argument(
        "--include-labeled", action="store_true", help="不排除已带标签的行"
    )
    export_parser.add_argument(
        "--keep-duplicates", action="store_true", help="不按文本去重"
    )
    _add_manifest_argument(export_parser)
    export_parser.add_argument("--json", action="store_true", help="输出机器可读 manifest")

    merge_parser = subparsers.add_parser("merge", help="合并人工标注结果并校验")
    merge_parser.add_argument("--sample", required=True, help="样本 CSV（export 的输出）")
    merge_parser.add_argument("--labels", required=True, help="人工标注 CSV（id,label）")
    merge_parser.add_argument("--output", required=True, help="输出训练 CSV")
    merge_parser.add_argument("--id-column", default="id", help="标注文件 id 列名")
    merge_parser.add_argument("--label-column", default="label", help="标注文件标签列名")
    merge_parser.add_argument(
        "--no-strict", dest="strict", action="store_false",
        help="跳过非法/缺失行（默认 strict：任何问题都失败）",
    )
    merge_parser.set_defaults(strict=True)
    _add_manifest_argument(merge_parser)
    merge_parser.add_argument("--json", action="store_true", help="输出机器可读 manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "export":
            result = export_labeling_sample(
                args.input,
                args.output,
                args.sample_size,
                seed=args.seed,
                text_column=args.text_column,
                group_column=args.group_column or "video_id",
                exclude_labeled=not args.include_labeled,
                exclude_duplicate_text=not args.keep_duplicates,
                label_column=args.label_column,
                manifest_path=args.manifest,
            )
            if args.json:
                print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
            else:
                excluded = "、".join(
                    f"{reason}={count}"
                    for reason, count in result.manifest["excluded"].items()
                    if count
                )
                print(f"[labeling] 已导出：{result.output_csv}")
                print(
                    f"[labeling] 抽样 {result.sampled} / 候选 "
                    f"{result.manifest['eligible_rows']}（seed={args.seed}）"
                )
                print(f"[labeling] 排除：{excluded or '无'}")
                print(f"[labeling] manifest：{result.manifest_path}")
                print("[labeling] 下一步：人工填写 label 列（positive/negative），再运行 merge。")
            return 0

        result = merge_labels(
            args.sample,
            args.labels,
            args.output,
            id_column=args.id_column,
            label_column=args.label_column,
            strict=args.strict,
            manifest_path=args.manifest,
        )
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            excluded = "、".join(
                f"{reason}={count}" for reason, count in result.excluded.items() if count
            )
            print(f"[labeling] 已合并：{result.output_csv}")
            print(
                f"[labeling] 接受 {result.accepted} / 样本 "
                f"{result.manifest['sample_rows']}；正 {result.label_counts['positive']}，"
                f"负 {result.label_counts['negative']}"
            )
            print(f"[labeling] 排除：{excluded or '无'}")
            print(f"[labeling] manifest：{result.manifest_path}")
        return 0
    except LabelingError as exc:
        print(f"[labeling] 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
