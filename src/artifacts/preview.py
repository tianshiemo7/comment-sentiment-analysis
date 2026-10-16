"""只读解析项目产物 CSV：阶段行列数、分页表格、逐条评论。

设计要点：

- 统一按 ``utf-8-sig`` 读取（`src/preprocessing/dataset.py:save_records` 的落盘格式）；
- **流式读取 + 只保留当页**：大表分页不会把整表读进内存；
- ``offset`` 越界不报错，返回空 ``rows`` 但 ``total_rows`` 仍是真实行数（前端据此翻页）；
- 逐条评论把 ``clean.csv`` 与 ``sentiment.csv`` 按行序号对齐
  （`sentiment.csv` 的 ``index`` 就是 clean 表的行序号）。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .schemas import CSV_ENCODING

#: clean 表里原文列的优先级（契约：items[].text 取原文 text_raw）
TEXT_COLUMNS: tuple[str, ...] = ("text_raw", "text")


def count_rows_columns(path: str | Path) -> tuple[int, int]:
    """返回 ``(数据行数, 列数)``（不含表头）；文件不存在或为空返回 ``(0, 0)``。"""
    path = Path(path)
    if not path.is_file():
        return 0, 0
    with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.reader(handle)
        try:
            columns = next(reader)
        except StopIteration:
            return 0, 0
        rows = sum(1 for _ in reader)
    return rows, len(columns)


def read_table(path: str | Path, offset: int, limit: int) -> tuple[list[str], list[list[str]], int]:
    """读取 CSV 的一页，返回 ``(columns, rows, total_rows)``。

    - 行会按表头列数补齐 / 截断，保证与 ``columns`` 对齐（前端直接按列渲染）；
    - ``offset`` 超出总行数时返回空 ``rows``，``total_rows`` 仍为真实行数。
    """
    path = Path(path)
    if not path.is_file():
        return [], [], 0
    with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.reader(handle)
        try:
            columns = next(reader)
        except StopIteration:
            return [], [], 0
        width = len(columns)
        rows: list[list[str]] = []
        total = 0
        for raw in reader:
            if total >= offset and len(rows) < limit:
                cells = ["" if value is None else str(value) for value in raw]
                rows.append((cells + [""] * width)[:width])
            total += 1
    return columns, rows, total


def load_sentiment(path: str | Path) -> dict[int, dict[str, Any]]:
    """读取 `sentiment.csv`，返回 ``{行序号: {label, confidence, probs}}``。

    文件不存在或损坏时返回空字典（调用方据此返回空列表，而不是编造情感值）。
    """
    path = Path(path)
    if not path.is_file():
        return {}
    result: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                index = int(row.get("index") or "")
            except (TypeError, ValueError):
                continue
            label = (row.get("label") or "").strip()
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            probs: dict[str, float] = {}
            for name in ("prob_positive", "prob_negative"):
                try:
                    probs[name.removeprefix("prob_")] = float(row.get(name) or 0.0)
                except (TypeError, ValueError):
                    continue
            result[index] = {"label": label, "confidence": confidence, "probs": probs}
    return result


def build_comments(
    clean_path: str | Path,
    sentiment_path: str | Path,
    *,
    offset: int,
    limit: int,
    label: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """把 `clean.csv`（原文）与 `sentiment.csv`（占位情感）按行序号对齐后分页。

    Returns:
        ``(过滤后的总条数, 当页 items)``；没有情感记录的行会被跳过（绝不编造 label）。
    """
    clean_path = Path(clean_path)
    sentiments = load_sentiment(sentiment_path)
    if not clean_path.is_file() or not sentiments:
        return 0, []
    total = 0
    items: list[dict[str, Any]] = []
    with clean_path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            sentiment = sentiments.get(index)
            if sentiment is None:
                continue
            if label and sentiment["label"] != label:
                continue
            total += 1
            if total <= offset or len(items) >= limit:
                continue
            items.append(
                {
                    "index": index,
                    "text": text_of(row),
                    "label": sentiment["label"],
                    "confidence": sentiment["confidence"],
                    "probs": sentiment["probs"],
                }
            )
    return total, items


def text_of(row: dict[str, Any]) -> str:
    """按 ``text_raw`` → ``text`` 优先级取原文（缺失时返回空串）。"""
    for column in TEXT_COLUMNS:
        value = row.get(column)
        if isinstance(value, str) and value != "":
            return value
    return ""
