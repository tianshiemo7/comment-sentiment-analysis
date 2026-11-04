"""视频任务阶段产物（``crawl.json`` / ``clean.json``）→ CSV 字节。

设计要点：

- 阶段 JSON 是**唯一真相来源**，导出只读不写（不改落盘 JSON）；
- ``crawl`` 列直接复用爬虫 CSV 标准列 :data:`src.videos.crawler_adapter.STANDARD_FIELDS`，
  与「爬虫上传的原始文件」列名一致；``clean`` 在末尾追加 ``raw_text``（清洗前原文，
  ``text`` 为清洗后文本）；
- 输出统一 UTF-8 with BOM（Excel 直接打开不乱码），行列用 ``csv`` 标准库序列化；
- ``stage`` 只接受 :data:`STAGE_COLUMNS` 的白名单（调用方先校验，这里再兜底一次，
  **绝不接受任何路径 / 文件名**）。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping

from .crawler_adapter import STANDARD_FIELDS

#: 各阶段导出的列（契约锁定，顺序即 CSV 表头顺序）
STAGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "crawl": tuple(STANDARD_FIELDS),
    "clean": (*STANDARD_FIELDS, "raw_text"),
}

#: 各阶段导出文件名前缀：``collect_<bvid>.csv`` / ``clean_<bvid>.csv``
FILENAME_PREFIX: dict[str, str] = {"crawl": "collect", "clean": "clean"}


def _cell(value: object) -> str:
    """单元格转字符串：``None`` → 空串，dict / list → JSON，其余 ``str()``。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def stage_csv(bvid: str, stage: str, payload: Mapping) -> tuple[bytes, str]:
    """把阶段产物 ``records[]`` 写成 UTF-8 BOM CSV；返回 ``(字节, 文件名)``。

    Args:
        bvid: 已通过 ``^BV[0-9A-Za-z]{10}$`` 校验的 BV 号（只用于文件名）。
        stage: ``crawl`` / ``clean``（白名单，其它值抛 ``KeyError``）。
        payload: :meth:`src.videos.store.VideoStore.read_stage` 读出的阶段产物。

    Returns:
        ``(CSV 字节（带 BOM）, 文件名)``。
    """
    columns = STAGE_COLUMNS[stage]
    records = payload.get("records") if isinstance(payload, Mapping) else None
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for record in records or []:
        if isinstance(record, Mapping):
            writer.writerow({name: _cell(record.get(name)) for name in columns})
    filename = f"{FILENAME_PREFIX[stage]}_{bvid}.csv"
    return buffer.getvalue().encode("utf-8-sig"), filename


__all__ = ["FILENAME_PREFIX", "STAGE_COLUMNS", "stage_csv"]
