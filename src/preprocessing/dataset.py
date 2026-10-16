"""统一的数据读写骨架（csv / xlsx）。

字段约定（见根 README 第六节）：评论文本列名统一为 ``text``，
标签列名为 ``label``（取值 ``positive`` / ``negative``，测试集可缺省）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEXT_FIELD = "text"
LABEL_FIELD = "label"

CSV_SUFFIXES = (".csv",)
EXCEL_SUFFIXES = (".xlsx", ".xls")


@dataclass
class Record:
    """一条评论样本。

    Attributes:
        text: 清洗后的评论文本；分词结果可放 ``tokens``。
        label: ``positive`` / ``negative``，无标签样本为 ``None``。
        tokens: 分词结果（空格连接），预处理产物使用。
        meta: 其它字段（如 ``id`` / ``source``）。
    """

    text: str
    label: str | None = None
    tokens: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def load_records(path: str | Path) -> list[Record]:
    """读取 csv / xlsx，返回记录列表。

    TODO(实现):
        - 按后缀分派：``.csv`` 用标准库 ``csv`` 或 pandas；``.xlsx`` 用 pandas + openpyxl；
        - 自动识别文本列：优先 ``text``，其次 ``comment`` / ``content``，兜底第一列；
        - 标签列 ``label`` 缺失时置 ``None``；标签值做 ``strip().lower()`` 归一化；
        - 跳过文本为空的行，并统计跳过数量打印提示。
    """
    raise NotImplementedError("TODO: 尚未实现数据读取")


def save_records(records: list[Record], path: str | Path) -> Path:
    """把记录写入 csv，返回写入路径。

    TODO(实现): 表头 ``text,label,tokens``（``tokens`` 为空时不写该列），
    编码 ``utf-8-sig``，自动创建父目录。
    """
    raise NotImplementedError("TODO: 尚未实现数据写出")


def detect_text_column(columns: list[str]) -> str:
    """从表头中推断文本列名。

    TODO(实现): 依次匹配 ``text`` / ``comment`` / ``content`` / ``评论``（不区分大小写），
    都不命中时返回第一列。
    """
    raise NotImplementedError("TODO: 尚未实现文本列识别")
