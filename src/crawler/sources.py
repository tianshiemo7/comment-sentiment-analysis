"""数据来源登记：维护 ``data/raw/来源说明.md`` 与 ``data/raw/sources.json``。

课程验收要求「数据来源与采集时间已注明」（``docs/验收检查清单.md`` 第 16 项）。
本模块把每次采集登记成一张 Markdown 表格；同时用一份 JSON 作为**唯一事实来源**，
避免反复解析 Markdown，也避免手工编辑表格后无法合并。

- ``sources.json``：机器可读的登记表（列表），供爬虫合并、去重；
- ``来源说明.md``：由 JSON 渲染出的报告表格，供人阅读、随课程报告提交。

每次采集应调用 :func:`register_source`（先合并 JSON，再重写 Markdown），
不要直接覆盖 :func:`write_source_note`，否则会丢失历史采集记录。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

SOURCE_NOTE_PATH = Path("data") / "raw" / "来源说明.md"
SOURCES_JSON_PATH = Path("data") / "raw" / "sources.json"

#: 表格列顺序（与 README / 报告第 2 章对齐）
SOURCE_FIELDS = ("站点或数据集", "URL", "采集时间", "条数", "标注方式", "备注")

_HEADER = """# 数据来源说明

> 本文件由 `src/crawler/sources.py` 自动生成，记录每次采集/下载的站点、URL、
> 采集时间与条数，供课程报告「数据来源」章节引用。
>
> 采集遵循目标站点 `robots.txt` 与限速要求（默认两次请求间隔 ≥ 2.5 秒，带随机抖动），
> 仅用于课程设计/研究目的，不采集登录后受限内容，不对站点造成压力。
"""

_FOOTER = """
> 说明：
> - 「标注方式」为「已有标注」表示公开数据集自带标签；「自采 + 人工标注」表示
>   爬虫只负责采集原文，情感标签由后续人工标注流程补齐。
> - 采集遵循 robots.txt 并限速；每次请求的耗时、重试与风控恢复情况见
>   `scripts/bench_crawler.py` 生成的效率报告与根目录 README。
"""


@dataclass
class SourceRecord:
    """一条数据来源记录。"""

    name: str
    url: str
    collected_at: str
    count: int
    labeled: bool = True
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "SourceRecord":
        return cls(
            name=str(payload.get("name", "")),
            url=str(payload.get("url", "")),
            collected_at=str(payload.get("collected_at", "")),
            count=int(payload.get("count", 0) or 0),
            labeled=bool(payload.get("labeled", True)),
            note=str(payload.get("note", "")),
        )


def _escape_cell(value: object) -> str:
    """转义 Markdown 表格单元格：竖线、换行、反斜杠。"""

    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.strip()


def render_source_table(records: Iterable[SourceRecord]) -> str:
    """把来源记录渲染为 Markdown 表格（含表头与分隔行）。"""

    lines = [
        "| " + " | ".join(SOURCE_FIELDS) + " |",
        "| " + " | ".join("---" for _ in SOURCE_FIELDS) + " |",
    ]
    for record in records:
        labeled = "已有标注" if record.labeled else "自采 + 人工标注"
        cells = (
            record.name,
            record.url,
            record.collected_at,
            record.count,
            labeled,
            record.note,
        )
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def write_source_note(
    records: Iterable[SourceRecord],
    path: str | Path = SOURCE_NOTE_PATH,
) -> Path:
    """写入（覆盖）来源说明文件，返回写入路径。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = _HEADER + "\n" + render_source_table(list(records)) + _FOOTER
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def load_source_records(path: str | Path = SOURCES_JSON_PATH) -> list[SourceRecord]:
    """读取 JSON 登记表；文件不存在时返回空列表。"""

    source = Path(path)
    if not source.exists():
        return []
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"来源登记文件格式错误（应为 JSON 数组）：{source}")
    return [SourceRecord.from_dict(item) for item in payload if isinstance(item, dict)]


def save_source_records(
    records: Iterable[SourceRecord],
    path: str | Path = SOURCES_JSON_PATH,
) -> Path:
    """原子写入 JSON 登记表（先写临时文件再替换）。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [record.as_dict() for record in records]
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp.replace(target)
    return target


def register_source(
    record: SourceRecord,
    *,
    json_path: str | Path = SOURCES_JSON_PATH,
    note_path: str | Path = SOURCE_NOTE_PATH,
) -> Path:
    """合并一条来源记录并重写 Markdown，返回 Markdown 路径。

    去重键为 ``(name, url, collected_at)``：同一视频/页面在同一秒重复登记会覆盖，
    不同时间的多次采集会各保留一行，便于审计「何时采了多少」。
    """

    records = load_source_records(json_path)
    key = (record.name, record.url, record.collected_at)
    merged: list[SourceRecord] = []
    replaced = False
    for existing in records:
        if (existing.name, existing.url, existing.collected_at) == key:
            merged.append(record)
            replaced = True
        else:
            merged.append(existing)
    if not replaced:
        merged.append(record)
    save_source_records(merged, json_path)
    return write_source_note(merged, note_path)
