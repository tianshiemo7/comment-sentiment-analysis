"""数据来源登记：维护 `data/raw/来源说明.md`。

验收要求「数据来源与采集时间已注明」，本模块负责把每次采集（或公开数据集下载）
登记成一张 Markdown 表格，位于 `data/raw/来源说明.md`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE_NOTE_PATH = Path("data") / "raw" / "来源说明.md"

#: 表格列顺序（与 README / 报告第 2 章对齐）
SOURCE_FIELDS = ("站点或数据集", "URL", "采集时间", "条数", "标注方式", "备注")


@dataclass
class SourceRecord:
    """一条数据来源记录。"""

    name: str
    url: str
    collected_at: str
    count: int
    labeled: bool = True
    note: str = ""


def render_source_table(records: list[SourceRecord]) -> str:
    """把来源记录渲染为 Markdown 表格文本（含表头与分隔行）。

    TODO(实现): 按 :data:`SOURCE_FIELDS` 生成 `| 列 | ... |` 表格，
    标注方式列根据 ``labeled`` 输出「已有标注」或「自采 + 人工标注」。
    """
    raise NotImplementedError("TODO: 尚未实现来源表格渲染")


def write_source_note(records: list[SourceRecord], path: str | Path = SOURCE_NOTE_PATH) -> Path:
    """写入（覆盖）来源说明文件，返回写入路径。

    TODO(实现): 文件头写 ``# 数据来源说明``，正文为 :func:`render_source_table` 的输出，
    末尾补充一句「采集遵循 robots.txt 并限速」的说明；自动创建父目录。
    """
    raise NotImplementedError("TODO: 尚未实现来源说明写入")
