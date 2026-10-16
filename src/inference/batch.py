"""批量预测的文件解析与结果导出。

支持 ``.csv`` 与 ``.xlsx``；文本列自动识别（``text`` → ``comment`` → ``content``
→ ``评论`` → 第一列），首行视为表头。

约定：返回的文本列表按下标 0..n-1 与 ``results[].index`` 一一对应，
空文本行被跳过（因此 index 是「有效行序号」而非 Excel 行号）。

结果导出（改进 B，契约扩展由 Lead 锁定）：

- :func:`export_results` 把结果写成 ``outputs/predictions/batch_prediction_<时间戳>_<shortid>.csv``
  （UTF-8 with BOM，Excel 可直接打开中文；列 ``index,text,label,confidence``）；
- :func:`resolve_export_path` 是下载路由的**安全边界**：只接受 ``outputs/predictions/``
  下的纯 CSV 文件名，显式拒绝目录穿越（``/``、``\\``、``..``），并用
  ``resolve()`` 复核父目录，绝不把用户可控字符串直接拼接成路径。
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

CSV_SUFFIXES = (".csv", ".txt", ".tsv")
EXCEL_SUFFIXES = (".xlsx",)

#: 文本列候选名（按优先级，比较时不区分大小写）
TEXT_COLUMN_CANDIDATES = ("text", "comment", "content", "评论", "评论内容", "内容")

#: 单次上传上限 20 MB
MAX_BYTES = 20 * 1024 * 1024

PREDICTIONS_DIR = Path("outputs") / "predictions"

#: 导出文件下载路由前缀（与 app.py 的路由保持一致）
EXPORT_URL_PREFIX = "/api/predict/batch/download/"

#: 导出文件名前缀，完整形如 ``batch_prediction_20261016_135753_1a2b3c4d.csv``
EXPORT_FILENAME_PREFIX = "batch_prediction_"

#: 允许下载的 file_id 形态：纯 ASCII 文件名 + ``.csv`` 后缀（不含任何路径成分）
SAFE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.csv$")


class UnsupportedFileTypeError(ValueError):
    """上传了不支持的文件类型（应返回 HTTP 400）。"""


class EmptyBatchError(ValueError):
    """文件解析后没有任何有效评论文本（应返回 HTTP 400）。"""


class UnsafeExportPathError(ValueError):
    """导出 / 下载文件名不合法（疑似目录穿越），应返回 HTTP 400。"""


def detect_text_column(columns: Sequence[str]) -> str:
    """从表头推断文本列名；都不命中时返回第一列。"""
    normalized = {str(col).strip().lower(): str(col) for col in columns}
    for candidate in TEXT_COLUMN_CANDIDATES:
        if candidate.lower() in normalized:
            return normalized[candidate.lower()]
    if not columns:
        raise EmptyBatchError("文件表头为空，无法识别文本列")
    return str(columns[0])


def read_texts(filename: str, content: bytes) -> list[str]:
    """解析上传文件，返回评论文本列表。

    Args:
        filename: 上传时的文件名（用于判断后缀）。
        content: 文件字节内容。

    Returns:
        非空评论文本列表（保持文件中的出现顺序）。

    Raises:
        UnsupportedFileTypeError: 后缀不在 ``.csv`` / ``.xlsx`` 支持范围内。
        EmptyBatchError: 文件为空，或没有任何非空文本行。
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix in CSV_SUFFIXES:
        rows = _read_csv_rows(content)
    elif suffix in EXCEL_SUFFIXES:
        rows = _read_xlsx_rows(content)
    else:
        supported = "、".join(CSV_SUFFIXES + EXCEL_SUFFIXES)
        raise UnsupportedFileTypeError(
            f"不支持的文件类型 {suffix or '(无后缀)'}，请上传 {supported} 文件"
        )

    if not rows:
        raise EmptyBatchError("文件内容为空")

    header = [str(cell) if cell is not None else "" for cell in rows[0]]
    column = detect_text_column(header)
    try:
        column_index = header.index(column)
    except ValueError:  # pragma: no cover - detect_text_column 保证命中
        column_index = 0

    texts: list[str] = []
    for row in rows[1:]:
        if column_index >= len(row):
            continue
        value = row[column_index]
        text = "" if value is None else str(value).strip()
        if text:
            texts.append(text)

    if not texts:
        raise EmptyBatchError("未在文件中解析到任何非空评论文本（请确认表头含 text 列）")
    return texts


def _read_csv_rows(content: bytes) -> list[list[str]]:
    """解析 CSV 字节；优先 utf-8-sig，失败时回退 gbk（兼容 Excel 导出的中文表）。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - 极少数全二进制文件
        raise EmptyBatchError("CSV 编码无法识别（支持 utf-8 / gbk）")

    reader = csv.reader(io.StringIO(text))
    return [row for row in reader if any(str(cell).strip() for cell in row)]


def _read_xlsx_rows(content: bytes) -> list[list[Any]]:
    """解析 xlsx 字节（依赖 openpyxl，缺失时给出安装提示）。"""
    try:
        from openpyxl import load_workbook  # noqa: PLC0415 - 函数内导入
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "解析 .xlsx 需要 openpyxl，请执行：python -m pip install openpyxl"
        ) from exc

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows: list[list[Any]] = []
        for row in sheet.iter_rows(values_only=True):
            if row is None:
                continue
            if any(cell is not None and str(cell).strip() for cell in row):
                rows.append(list(row))
        return rows
    finally:
        workbook.close()


def new_export_filename(now: datetime | None = None) -> str:
    """生成唯一导出文件名：``batch_prediction_<YYYYMMDD_HHMMSS>_<shortid>.csv``。

    Args:
        now: 时间戳（仅测试注入用，默认取当前时间）。

    Returns:
        纯文件名（不含任何目录），可直接落盘到 :data:`PREDICTIONS_DIR`。
    """
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"{EXPORT_FILENAME_PREFIX}{timestamp}_{short_id}.csv"


def ensure_safe_csv_name(file_id: str) -> str:
    """校验 ``file_id`` 是否是安全的纯 CSV 文件名，返回该文件名。

    这是下载路由的安全边界：只接受 ``outputs/predictions/`` 下的**裸文件名**，
    拒绝空串、空白包裹、``.``/``..``、任何路径分隔符（``/`` ``\\``）、空字符
    与不符合命名规则的字符串，从根上杜绝把用户输入拼进路径。

    Raises:
        UnsafeExportPathError: 名字不合法（下载路由应返回 HTTP 400）。
    """
    if not isinstance(file_id, str) or not file_id or file_id != file_id.strip():
        raise UnsafeExportPathError(f"非法的导出文件名：{file_id!r}")
    if (
        file_id in {".", ".."}
        or ".." in file_id
        or "/" in file_id
        or "\\" in file_id
        or "\x00" in file_id
        or Path(file_id).name != file_id
        or not SAFE_FILE_ID_RE.match(file_id)
    ):
        raise UnsafeExportPathError(
            f"非法的导出文件名：{file_id!r}"
            "（只允许 outputs/predictions/ 下的纯 CSV 文件名，不得包含路径分隔符或 ..）"
        )
    return file_id


def resolve_export_path(file_id: str, out_dir: str | Path | None = None) -> Path:
    """把下载请求的 ``file_id`` 解析为受信任的绝对路径（防目录穿越）。

    步骤：先 :func:`ensure_safe_csv_name` 卡住输入形态，再 ``resolve()`` 后复核
    父目录必须仍是 ``outputs/predictions/``，因此即使符号链接 / 别名等
    意外绕过前缀检查也会被拒。

    Args:
        file_id: 下载路由传入的文件名（不可信输入）。
        out_dir: 导出目录（默认 :data:`PREDICTIONS_DIR`；测试可注入临时目录）。

    Raises:
        UnsafeExportPathError: 文件名不合法或解析后不在导出目录内。
    """
    safe_name = ensure_safe_csv_name(file_id)
    root = Path(PREDICTIONS_DIR if out_dir is None else out_dir).resolve()
    candidate = (root / safe_name).resolve()
    if candidate.parent != root:
        raise UnsafeExportPathError(f"非法的导出路径：{file_id!r} 不在 {root} 目录内")
    return candidate


def export_url_for(filename: str) -> str:
    """由文件名拼出下载地址 ``/api/predict/batch/download/<file_id>``。"""
    return f"{EXPORT_URL_PREFIX}{ensure_safe_csv_name(filename)}"


def export_results(
    results: Sequence[dict],
    filename: str | None = None,
    out_dir: str | Path | None = None,
) -> Path:
    """把批量预测结果写成 CSV（``index,text,label,confidence``），返回文件路径。

    Args:
        results: :func:`src.inference.service.predict_batch` 的结果列表。
        filename: 目标文件名；``None`` 时用 :func:`new_export_filename` 生成
            形如 ``batch_prediction_20261016_135753_1a2b3c4d.csv`` 的唯一名字。
            无论是否显式传入都会经过 :func:`ensure_safe_csv_name` 校验。
        out_dir: 输出目录（默认 ``outputs/predictions/``，测试可注入临时目录）。

    Returns:
        写出的文件路径。

    Notes:
        UTF-8 with BOM（``utf-8-sig``），保证 Excel 双击打开不乱码。
    """
    out_dir_path = Path(PREDICTIONS_DIR if out_dir is None else out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    name = new_export_filename() if filename is None else ensure_safe_csv_name(filename)
    out_path = out_dir_path / name
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["index", "text", "label", "confidence"])
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return out_path
