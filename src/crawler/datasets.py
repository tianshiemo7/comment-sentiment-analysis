"""公开带标签中文情感语料：注册表、下载校验与格式统一。

课程硬指标要求训练语料 ≥ 8000 条、测试 ≥ 2000 条且正负基本均衡
（见 ``data/README.md``）。本模块负责其中「公开数据集」这一路：

- 用:data:`DATASETS` 登记公开中文情感语料（ChnSentiCorp / 外卖评论 /
  酒店评论 / 购物评论等），:func:`list_datasets` 供人查看；
- :func:`download_dataset` 流式下载到 ``data/raw/datasets/``，做 SHA256
  校验、原子改名，并用 :func:`src.crawler.sources.register_source`
  把「数据集 / URL / 采集时间 / 条数 / 标注方式」登记进来源说明，
  满足验收清单「数据来源与采集时间已注明」；
- :func:`import_labeled_csv` 把不同列名、不同标签写法的公开 CSV 统一成
  ``text,label``（UTF-8 BOM，Excel 可直接打开），下游
  ``python -m src.preprocessing`` 不用改。

设计要点
--------
- **只用标准库**：下载走 ``urllib.request`` 流式读取，不依赖 requests，
  因此本模块与测试都可以完全离线（测试用本地 ``http.server``）；
- **原子落盘**：先写 ``*.part`` 临时文件 → 校验 SHA256 → ``os.replace``
  改名。任何失败（网络、空文件、校验不符）都会删除临时文件，不留半成品；
- **校验和策略**：公开仓库的 ``master`` 分支文件会被上游更新，硬编码
  SHA256 会让功能随上游变动而失效，因此内置注册表默认不固定校验和
  （下载后把实际 SHA256 写进来源备注，便于事后审计）。需要严格复现时，
  用 ``--sha256`` 传期望值，或直接修改注册表；校验不符**一定失败**；
- **离线测试**：测试全部使用本地 HTTP 服务或临时文件，绝不访问真实站点。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import random
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from . import sources
from .errors import CrawlerError
from .normalize import normalize_text, text_signature, utc_iso
from .sources import SourceRecord
from .transport import DEFAULT_USER_AGENT

logger = logging.getLogger("dataset_fetcher")

ROOT = Path(__file__).resolve().parents[2]
#: 默认下载目录（被 ``.gitignore`` 的 ``data/raw/*`` 规则排除，不入库）
DEFAULT_TARGET_DIR = ROOT / "data" / "raw" / "datasets"
DEFAULT_TIMEOUT = float(os.getenv("DATASET_TIMEOUT", "60"))
CHUNK_SIZE = 1 << 16

#: 统一输出列：``text,label`` 是 ``src/preprocessing`` 训练模式识别的列名
OUTPUT_COLUMNS = ("text", "label")
LABELS = ("positive", "negative")

#: 常见「文本列 / 标签列」别名，供 CLI 自动识别（函数本身保持显式列名）
TEXT_COLUMN_ALIASES = ("text", "review", "content", "comment", "评论", "评价")
LABEL_COLUMN_ALIASES = ("label", "sentiment", "polarity", "标签", "情感")


class DatasetError(CrawlerError):
    """公开数据集下载/读取/格式转换失败。"""


@dataclass(frozen=True)
class DatasetSpec:
    """一个公开数据集的可下载描述。

    Args:
        name: 唯一名称（注册表键的显示名，也用于 ``--dataset`` 选择）。
        description: 一句话说明（语种/场景/规模），供 ``--list-datasets`` 展示。
        url: 公开下载地址（HTTP(S)）。
        sha256: 期望的 SHA256 小写十六进制；空字符串表示「不固定校验和」。
        filename: 落盘文件名（不同数据集即使内容相同也应使用不同文件名）。
        license: 许可/使用限制说明（必须原样写进来源备注）。
        note: 字段与标签约定等备注。
        homepage: 数据集发布页（便于复核许可与更新）。
        labeled: 是否自带情感标签（公开语料多为 True）。
        text_column / label_column: 该数据集原始的文本列与标签列名，
            供 CLI 自动识别与文档展示。
    """

    name: str
    description: str
    url: str
    sha256: str = ""
    filename: str = ""
    license: str = ""
    note: str = ""
    homepage: str = ""
    labeled: bool = True
    text_column: str = "text"
    label_column: str = "label"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DatasetError("DatasetSpec.name 不能为空")
        url = (self.url or "").strip()
        if not url:
            raise DatasetError(f"{self.name}: DatasetSpec.url 不能为空")
        scheme = urlsplit(url).scheme.lower()
        if scheme not in {"http", "https"}:
            raise DatasetError(
                f"{self.name}: 下载地址必须是 http(s)，拒绝 {scheme or '无 scheme'}：{url!r}"
            )
        sha = (self.sha256 or "").strip().lower()
        if sha and not _is_sha256_hex(sha):
            raise DatasetError(
                f"{self.name}: sha256 必须是 64 位十六进制（留空表示不固定校验和）：{self.sha256!r}"
            )
        object.__setattr__(self, "sha256", sha)
        filename = (self.filename or "").strip() or Path(urlsplit(url).path).name
        if not filename:
            filename = f"{self.name}.csv"
        if filename in {".", ".."} or Path(filename).name != filename:
            raise DatasetError(
                f"{self.name}: filename 只能是纯文件名，不能包含目录分隔符：{filename!r}"
            )
        object.__setattr__(self, "filename", filename)

    @property
    def format(self) -> str:
        """按扩展名推断格式：``csv`` / ``zip`` / 其它小写后缀。"""

        suffix = Path(self.filename).suffix.lower().lstrip(".")
        return suffix or "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


#: 内置公开数据集注册表（键为稳定短名，``--dataset`` 可大小写不敏感匹配）。
#:
#: URL 取公开镜像（GitHub raw / jsDelivr）；上游文件会更新，故默认不固定
#: ``sha256``。**测试绝不访问这些真实地址**：测试会注册本地 HTTP 服务的
#: 合成 DatasetSpec。
DATASETS: dict[str, DatasetSpec] = {
    "chnsenticorp": DatasetSpec(
        name="ChnSentiCorp",
        description="ChnSentiCorp 中文情感分析数据集（酒店评论子集，约 7.7k 条；实测正 5322 / 负 2443）",
        url=(
            "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/"
            "master/datasets/ChnSentiCorp_htl_all/ChnSentiCorp_htl_all.csv"
        ),
        filename="ChnSentiCorp_htl_all.csv",
        license="以上游发布页声明为准（仅限课程/研究用途）",
        note="列：label,review；label 1=正面 0=负面",
        homepage="https://github.com/SophonPlus/ChineseNlpCorpus",
        labeled=True,
        text_column="review",
        label_column="label",
    ),
    "waimai_10k": DatasetSpec(
        name="外卖评论数据集",
        description="外卖平台中文评论（约 1.2 万条；实测正 4000 / 负 7980，可用 --merge-balance 均衡）",
        url=(
            "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/"
            "master/datasets/waimai_10k/waimai_10k.csv"
        ),
        filename="waimai_10k.csv",
        license="以上游发布页声明为准（仅限课程/研究用途）",
        note="列：label,review；label 1=正面 0=负面",
        homepage="https://github.com/SophonPlus/ChineseNlpCorpus",
        labeled=True,
        text_column="review",
        label_column="label",
    ),
    "hotel_reviews": DatasetSpec(
        name="酒店评论数据集",
        description="ChnSentiCorp 酒店评论（约 7.7k 条），另一镜像，便于与 ChnSentiCorp 交叉核对",
        url=(
            "https://cdn.jsdelivr.net/gh/SophonPlus/ChineseNlpCorpus@master/"
            "datasets/ChnSentiCorp_htl_all/ChnSentiCorp_htl_all.csv"
        ),
        filename="hotel_reviews.csv",
        license="以上游发布页声明为准（仅限课程/研究用途）",
        note="列：label,review；label 1=正面 0=负面",
        homepage="https://github.com/SophonPlus/ChineseNlpCorpus",
        labeled=True,
        text_column="review",
        label_column="label",
    ),
    "online_shopping_10_cats": DatasetSpec(
        name="购物评论数据集",
        description="10 类商品中文评论（约 6.3 万条），含多分类标签，可抽取二分类子集",
        url=(
            "https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/"
            "master/datasets/online_shopping_10_cats/online_shopping_10_cats.csv"
        ),
        filename="online_shopping_10_cats.csv",
        license="以上游发布页声明为准（仅限课程/研究用途）",
        note="列：cat,label,review；label 1=正面 0=负面；不同商品类目可作分层维度",
        homepage="https://github.com/SophonPlus/ChineseNlpCorpus",
        labeled=True,
        text_column="review",
        label_column="label",
    ),
}


#: 默认标签映射：覆盖 0/1、正面/负面、pos/neg、英文正负等常见写法。
#: 键在比较前统一 ``strip().casefold()``；值只能是 ``positive`` / ``negative``。
DEFAULT_LABEL_MAP: dict[str, str] = {
    # 正面
    "1": "positive",
    "1.0": "positive",
    "positive": "positive",
    "pos": "positive",
    "p": "positive",
    "good": "positive",
    "true": "positive",
    "正面": "positive",
    "正": "positive",
    "正向": "positive",
    "好评": "positive",
    "好": "positive",
    "正类": "positive",
    # 负面
    "0": "negative",
    "0.0": "negative",
    "negative": "negative",
    "neg": "negative",
    "n": "negative",
    "bad": "negative",
    "false": "negative",
    "负面": "negative",
    "负": "negative",
    "负向": "negative",
    "差评": "negative",
    "差": "negative",
    "负类": "negative",
}


# =========================================================================
# 注册表
# =========================================================================

def list_datasets() -> list[DatasetSpec]:
    """返回全部已注册数据集（注册顺序，便于稳定展示与测试）。"""

    return list(DATASETS.values())


def register_dataset(spec: DatasetSpec, *, overwrite: bool = False) -> DatasetSpec:
    """注册/覆盖一个数据集描述，返回该描述（便于脚本化扩展）。"""

    if not isinstance(spec, DatasetSpec):
        raise DatasetError(f"register_dataset 需要 DatasetSpec，收到 {type(spec)!r}")
    key = spec.name.strip().casefold()
    existing_key = next(
        (item for item in DATASETS if item.casefold() == key), None
    )
    if existing_key is not None and not overwrite:
        raise DatasetError(f"数据集已注册：{spec.name}（覆盖请传 overwrite=True）")
    if existing_key is not None:
        del DATASETS[existing_key]
    DATASETS[key] = spec
    return spec


def get_dataset(name: str) -> DatasetSpec:
    """按短名/显示名查找数据集（大小写不敏感），找不到抛 DatasetError。"""

    query = str(name or "").strip()
    if not query:
        raise DatasetError("数据集名称不能为空")
    for key, spec in DATASETS.items():
        if query.casefold() in {key.casefold(), spec.name.casefold()}:
            return spec
    available = "、".join(f"{key}（{spec.name}）" for key, spec in DATASETS.items())
    raise DatasetError(f"未知数据集：{query}\n可用数据集：{available}")


def spec_for_file(path: str | Path) -> DatasetSpec | None:
    """按落盘文件名反查注册表（CLI 导入时自动采用该数据集的列名约定）。"""

    name = Path(path).name
    for spec in DATASETS.values():
        if spec.filename == name:
            return spec
    return None


# =========================================================================
# 下载与校验
# =========================================================================

def sha256_file(path: str | Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """流式计算文件 SHA256（小写十六进制）。"""

    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def count_records(path: str | Path) -> int:
    """粗略统计数据集条数：CSV 数据行 / ZIP 内文件数；其它返回 0。

    只用于来源说明里的「条数」，允许不精确（如 ZIP 内每个文件是一个类别）；
    具体清洗后的有效条数以 ``src/preprocessing`` 的 manifest 为准。
    """

    target = Path(path)
    suffix = target.suffix.lower()
    try:
        if suffix == ".csv":
            with target.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                next(reader, None)  # 表头
                return sum(1 for _ in reader)
        if suffix == ".zip":
            with zipfile.ZipFile(target) as archive:
                return sum(
                    1
                    for info in archive.infolist()
                    if not info.is_dir() and not info.filename.startswith("__MACOSX/")
                )
        if suffix in {".jsonl", ".txt"}:
            with target.open("r", encoding="utf-8-sig") as handle:
                return sum(1 for line in handle if line.strip())
    except (OSError, csv.Error, zipfile.BadZipFile):
        logger.warning("无法统计数据集条数：%s", target)
    return 0


def _open_url(url: str, timeout: float) -> Any:
    """默认下载器：标准库 urllib，返回可 ``read()`` 的响应对象。

    仅允许 http(s)，避免用户传入的 ``file://`` 等 scheme 读取本机文件。
    """

    if urlsplit(url).scheme.lower() not in {"http", "https"}:
        raise DatasetError(f"下载地址必须是 http(s)：{url!r}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _register_download_source(
    spec: DatasetSpec,
    destination: Path,
    actual_sha256: str,
    expected_sha256: str,
    count: int,
    json_path: str | Path | None,
    note_path: str | Path | None,
) -> None:
    """把一次下载登记进 ``sources.json`` / ``来源说明.md``。"""

    verified = "已按期望值校验通过" if expected_sha256 else "未固定期望值，已记录实际值供审计"
    note = (
        f"公开数据集下载：{spec.description}；文件={destination.name}；"
        f"格式={spec.format}；条数≈{count}；SHA256={actual_sha256}（{verified}）；"
        f"许可：{spec.license or '见发布页'}"
    )
    if spec.note:
        note = f"{note}；{spec.note}"
    record = SourceRecord(
        name=spec.name,
        url=spec.url,
        collected_at=utc_iso(),
        count=count,
        labeled=spec.labeled,
        note=note,
    )
    kwargs: dict[str, Any] = {}
    if json_path is not None:
        kwargs["json_path"] = json_path
    if note_path is not None:
        kwargs["note_path"] = note_path
    sources.register_source(record, **kwargs)


def download_dataset(
    name: str,
    target_dir: str | Path = DEFAULT_TARGET_DIR,
    timeout: float = DEFAULT_TIMEOUT,
    force: bool = False,
    *,
    url: str | None = None,
    sha256: str | None = None,
    write_source_note: bool = True,
    source_json_path: str | Path | None = None,
    source_note_path: str | Path | None = None,
    chunk_size: int = CHUNK_SIZE,
    opener: Callable[[str, float], Any] | None = None,
) -> Path:
    """下载公开数据集并返回落盘路径。

    Args:
        name: 注册表中的数据集短名或显示名。
        target_dir: 落盘目录；不存在会自动创建。
        timeout: 单次网络请求超时（秒）。
        force: 目标文件已存在时是否强制重新下载（默认复用已有文件）。
        url: 覆盖注册表 URL（镜像失效时使用）。
        sha256: 覆盖注册表期望 SHA256；空字符串表示不校验。
        write_source_note: 是否登记来源（测试/临时目录可关闭）。
        source_json_path / source_note_path: 覆盖来源登记路径（测试用）。
        chunk_size: 流式读取块大小。
        opener: 注入的下载器 ``(url, timeout) -> response``（测试用）。

    Returns:
        下载完成后的文件路径。

    Raises:
        DatasetError: 网络失败、空文件、SHA256 不符或目标文件校验不通过。
            **任何失败都不会留下半成品文件。**
    """

    spec = get_dataset(name)
    if url is not None:
        spec = replace(spec, url=url)
    if sha256 is not None:
        spec = replace(spec, sha256=sha256)
    expected = spec.sha256
    if timeout is None or float(timeout) <= 0:
        raise DatasetError("timeout 必须为正数")
    if chunk_size <= 0:
        raise DatasetError("chunk_size 必须为正数")

    directory = Path(target_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatasetError(f"无法创建下载目录 {directory}：{exc}") from exc
    destination = directory / spec.filename

    if destination.exists() and not force:
        actual = sha256_file(destination)
        if expected and actual != expected:
            raise DatasetError(
                f"{spec.name}: 已存在的文件 {destination} 校验不符\n"
                f"  期望：{expected}\n  实际：{actual}\n"
                "可加 --force 重新下载，或删除该文件后重试。"
            )
        logger.info("数据集已存在，跳过下载：%s（SHA256=%s）", destination, actual)
        return destination

    open_stream = opener or _open_url
    temp_path: Path | None = None
    handle = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{spec.filename}.", suffix=".part", dir=str(directory)
        )
        temp_path = Path(temp_name)
        handle = os.fdopen(fd, "wb")
        hasher = hashlib.sha256()
        total = 0
        try:
            response = open_stream(spec.url, float(timeout))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise DatasetError(f"{spec.name}: 下载失败：{spec.url}（{exc}）") from exc
        try:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
                handle.write(chunk)
                total += len(chunk)
        except (OSError, ValueError) as exc:
            raise DatasetError(f"{spec.name}: 读取下载数据失败（{exc}）") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None

        if total <= 0:
            raise DatasetError(f"{spec.name}: 下载内容为空：{spec.url}")

        actual = hasher.hexdigest()
        if expected and actual != expected:
            raise DatasetError(
                f"{spec.name}: SHA256 校验失败，已丢弃下载内容\n"
                f"  期望：{expected}\n  实际：{actual}\n"
                "可检查 URL 是否指向了错误版本，或用 --sha256 更新期望值。"
            )

        os.replace(temp_path, destination)
        temp_path = None
    except DatasetError:
        raise
    except OSError as exc:
        raise DatasetError(f"{spec.name}: 落盘失败：{exc}") from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover - 防御式
                pass
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover - 防御式
                logger.warning("清理临时文件失败：%s", temp_path)

    logger.info(
        "已下载 %s → %s（%d 字节，SHA256=%s）", spec.name, destination, total, actual
    )
    if write_source_note:
        _register_download_source(
            spec,
            destination,
            actual,
            expected,
            count_records(destination),
            source_json_path,
            source_note_path,
        )
    return destination


# =========================================================================
# 格式统一：任意已标注 CSV → text,label
# =========================================================================

@dataclass
class ImportReport:
    """一次 :func:`import_labeled_csv` 的统计结果（同时可写出 manifest）。"""

    source: str
    output: str
    total_rows: int = 0
    kept: int = 0
    empty_text: int = 0
    duplicates: int = 0
    invalid_label: int = 0
    labels: dict[str, int] = field(default_factory=dict)
    source_sha256: str = ""
    output_sha256: str = ""
    examples_invalid: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_label_map(label_map: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(DEFAULT_LABEL_MAP)
    if label_map:
        for raw_key, raw_value in label_map.items():
            key = str(raw_key).strip().casefold()
            value = str(raw_value).strip().casefold()
            if not key:
                raise DatasetError("label_map 的原始标签不能为空")
            if value not in LABELS:
                raise DatasetError(
                    f"label_map[{raw_key!r}]={raw_value!r} 非法，目标值只能是 {LABELS}"
                )
            merged[key] = value
    return merged


def read_csv_header(path: str | Path) -> list[str]:
    """读取 CSV 表头（用于列名自动识别）。"""

    target = Path(path)
    if not target.exists():
        raise DatasetError(f"文件不存在：{target}")
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            header = list(csv.DictReader(handle).fieldnames or [])
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise DatasetError(f"读取 CSV 失败：{target}（{exc}）") from exc
    if not header:
        raise DatasetError(f"CSV 没有表头或为空：{target}")
    return header


def _read_csv_rows(
    path: str | Path,
    *,
    text_col: str,
    label_col: str,
) -> tuple[list[str], list[dict[str, str]]]:
    target = Path(path)
    header = read_csv_header(target)
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise DatasetError(f"读取 CSV 失败：{target}（{exc}）") from exc
    missing = [column for column in (text_col, label_col) if column not in header]
    if missing:
        raise DatasetError(
            f"{target} 缺少列 {missing}；实际表头：{header}\n"
            f"可用 --text-col / --label-col 指定（常见文本列：{TEXT_COLUMN_ALIASES}）"
        )
    return header, rows


def import_labeled_csv(
    path: str | Path,
    out_path: str | Path,
    text_col: str = "text",
    label_col: str = "label",
    label_map: Mapping[str, str] | None = None,
    *,
    drop_invalid: bool = False,
) -> ImportReport:
    """把公开数据集的 CSV 统一成 ``text,label``（UTF-8 BOM）。

    处理规则（顺序固定，统计口径可复现）：

    1. 文本用 :func:`src.crawler.normalize.normalize_text` 标准化（HTML 实体、
       NFKC、零宽字符、空白），标准化后为空记 ``empty_text``；
    2. 文本去重（按 :func:`src.crawler.normalize.text_signature` 签名），
       重复记 ``duplicates``；
    3. 标签经 ``label_map`` 映射为 ``positive`` / ``negative``。默认映射覆盖
       ``0/1``、``positive/negative``、``pos/neg``、``正面/负面``、``好评/差评``
       等常见写法；自定义 ``label_map`` 会覆盖同名默认项；
    4. 无法识别的标签记 ``invalid_label``：默认收集统计后抛
       :class:`DatasetError`（且**不写输出文件**）；``drop_invalid=True``
       时丢弃这些行并继续。

    Args:
        path: 输入 CSV（自动识别 UTF-8 BOM）。
        out_path: 输出 CSV，列固定为 ``text,label``，UTF-8 BOM。
        text_col: 文本列名。
        label_col: 标签列名。
        label_map: 追加/覆盖的 ``{原始标签: positive|negative}`` 映射。
        drop_invalid: 是否丢弃非法标签行（默认 False = 报错）。

    Returns:
        :class:`ImportReport` 统计结果。
    """

    mapping = _build_label_map(label_map)
    header, rows = _read_csv_rows(path, text_col=text_col, label_col=label_col)

    total = 0
    empty_text = 0
    duplicates = 0
    invalid_label = 0
    labels: dict[str, int] = {label: 0 for label in LABELS}
    seen_signatures: set[str] = set()
    kept: list[dict[str, str]] = []
    invalid_examples: list[str] = []
    invalid_values: dict[str, int] = {}

    for row in rows:
        total += 1
        text = normalize_text(row.get(text_col))
        if not text:
            empty_text += 1
            continue
        signature = text_signature(text)
        if signature in seen_signatures:
            duplicates += 1
            continue
        raw_label = str(row.get(label_col) or "").strip()
        label = mapping.get(raw_label.casefold())
        if label is None:
            invalid_label += 1
            invalid_values[raw_label] = invalid_values.get(raw_label, 0) + 1
            if len(invalid_examples) < 5:
                invalid_examples.append(raw_label or "<空>")
            continue
        seen_signatures.add(signature)
        labels[label] += 1
        kept.append({"text": text, "label": label})

    if invalid_label and not drop_invalid:
        detail = "、".join(f"{value!r}×{count}" for value, count in invalid_values.items())
        raise DatasetError(
            f"{path}: {invalid_label} 行标签无法映射为 positive/negative：{detail}\n"
            "请用 --label-map 原始标签=positive|negative 补充映射，"
            "或加 --drop-invalid 丢弃这些行。"
        )

    output = Path(out_path)
    _write_labeled_csv(output, kept)
    output_sha = sha256_file(output)
    source_sha = sha256_file(path)

    report = ImportReport(
        source=str(Path(path)),
        output=str(output),
        total_rows=total,
        kept=len(kept),
        empty_text=empty_text,
        duplicates=duplicates,
        invalid_label=invalid_label,
        labels=labels,
        source_sha256=source_sha,
        output_sha256=output_sha,
        examples_invalid=invalid_examples,
    )
    logger.info(
        "已统一 %s → %s（总 %d，保留 %d，空文本 %d，重复 %d，非法标签 %d）",
        path,
        output,
        total,
        len(kept),
        empty_text,
        duplicates,
        invalid_label,
    )
    return report


@dataclass
class BalanceReport:
    """多份已标注 CSV 合并 + 类别均衡后的统计。"""

    inputs: list[str]
    output: str
    seed: int
    per_label: int
    total_rows: int
    kept: int
    source_counts: dict[str, int]
    labels: dict[str, int]
    duplicates: int
    empty_text: int
    input_sha256s: list[str]
    output_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def merge_balance_labeled_csv(
    inputs: Iterable[str | Path],
    out_path: str | Path,
    *,
    seed: int = 42,
    max_per_label: int | None = None,
    drop_duplicates: bool = True,
) -> BalanceReport:
    """合并多份已标注 CSV，并按较小类别下采样成**类别均衡**的数据集。

    用于「公开语料正负不均衡」的场景：例如外卖评论 4000 正 / 7980 负，
    与 ChnSentiCorp 合并后按两类较小者下采样，得到正负各 N 条。

    规则（顺序固定、可复现）：

    1. 逐个读取 `text,label` CSV，文本标准化后为空记 ``empty_text``；
    2. 按文本签名跨文件去重（``drop_duplicates=True``），重复记 ``duplicates``；
    3. 每个类别分别用 ``random.Random(seed)`` 打乱后取前 N 条，
       ``N = min(max_per_label, 较小类别条数)``；
    4. 两类别合并后再用同一 RNG 打乱，写出 UTF-8 BOM 的 `text,label`。
    """

    paths = [Path(path) for path in inputs]
    if not paths:
        raise DatasetError("merge_balance_labeled_csv 至少需要一份输入 CSV")
    for path in paths:
        if not path.exists():
            raise DatasetError(f"输入文件不存在：{path}")

    pools: dict[str, list[dict[str, str]]] = {label: [] for label in LABELS}
    seen: set[str] = set()
    total_rows = empty_text = duplicates = 0
    input_sha256s: list[str] = []

    for path in paths:
        _header, rows = _read_csv_rows(path, text_col="text", label_col="label")
        input_sha256s.append(sha256_file(path))
        for row in rows:
            total_rows += 1
            text = normalize_text(row.get("text"))
            label = str(row.get("label") or "").strip().lower()
            if label not in LABELS:
                raise DatasetError(
                    f"{path}：标签 {label!r} 非法（只允许 {LABELS}）；"
                    "请先用 import_labeled_csv 统一标签"
                )
            if not text:
                empty_text += 1
                continue
            signature = text_signature(text)
            if drop_duplicates and signature in seen:
                duplicates += 1
                continue
            seen.add(signature)
            pools[label].append({"text": text, "label": label})

    source_counts = {label: len(pools[label]) for label in LABELS}
    available = min(source_counts.values())
    if available <= 0:
        raise DatasetError(f"合并后至少有一个类别为空：{source_counts}")
    per_label = min(int(max_per_label), available) if max_per_label else available
    if per_label <= 0:
        raise DatasetError("max_per_label 必须为正整数")

    rng = random.Random(seed)
    balanced: list[dict[str, str]] = []
    for label in LABELS:
        pool = list(pools[label])
        rng.shuffle(pool)
        balanced.extend(pool[:per_label])
    rng.shuffle(balanced)

    output = Path(out_path)
    _write_labeled_csv(output, balanced)
    report = BalanceReport(
        inputs=[str(path) for path in paths],
        output=str(output),
        seed=seed,
        per_label=per_label,
        total_rows=total_rows,
        kept=len(balanced),
        source_counts=source_counts,
        labels={label: per_label for label in LABELS},
        duplicates=duplicates,
        empty_text=empty_text,
        input_sha256s=input_sha256s,
        output_sha256=sha256_file(output),
    )
    logger.info(
        "已合并均衡 %d 份 → %s（%d 条；每类 %d；原正/负 %s/%s；重复 %d）",
        len(paths), output, len(balanced), per_label,
        source_counts["positive"], source_counts["negative"], duplicates,
    )
    return report


def _write_labeled_csv(
    out_path: Path,
    rows: Iterable[Mapping[str, str]],
    *,
    columns: Iterable[str] = OUTPUT_COLUMNS,
) -> None:
    """原子写入 UTF-8 BOM 的 CSV（先写临时文件再 ``os.replace``）。"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(columns)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{out_path.name}.", suffix=".part", dir=str(out_path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, out_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover - 防御式
                pass


# =========================================================================
# 命令行入口：python -m src.crawler.datasets ...（由 scripts/get_data.sh 调用）
# =========================================================================

def _detect_column(
    header: Iterable[str],
    preferred: str | None,
    aliases: Iterable[str],
    what: str,
) -> str:
    columns = list(header)
    if preferred:
        if preferred in columns:
            return preferred
        raise DatasetError(f"找不到{what}列 {preferred!r}；实际表头：{columns}")
    for candidate in aliases:
        if candidate in columns:
            return candidate
    raise DatasetError(f"无法自动识别{what}列；实际表头：{columns}，可用 --{what} 显式指定")


def _parse_label_map_args(values: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise DatasetError(f"--label-map 需要 原始标签=positive|negative，收到 {item!r}")
        raw, target = item.split("=", 1)
        mapping[raw] = target
    return mapping


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.crawler.datasets",
        description=(
            "公开带标签中文情感语料：列出/下载数据集（SHA256 校验 + 来源登记），"
            "把任意已标注 CSV 统一成 text,label，或合并多份 CSV 做类别均衡。"
        ),
    )
    parser.add_argument(
        "--list", "--list-datasets", dest="list_datasets", action="store_true",
        help="列出内置公开数据集（名称/规模/许可/URL）",
    )
    parser.add_argument(
        "--download", "--dataset", dest="download", metavar="NAME",
        help="下载指定数据集（注册表短名或显示名）",
    )
    parser.add_argument(
        "--import-csv", dest="import_csv", metavar="PATH",
        help="把已标注 CSV 统一成 text,label",
    )
    parser.add_argument(
        "--merge-balance", dest="merge_balance", nargs="+", metavar="CSV",
        help="合并多份 text,label CSV 并按较小类别下采样成类别均衡数据集",
    )
    parser.add_argument("--seed", type=int, default=42, help="--merge-balance 的随机种子（默认 42）")
    parser.add_argument(
        "--max-per-label", type=int, default=None,
        help="--merge-balance 每个类别最多保留多少条（默认取较小类别条数）",
    )
    parser.add_argument(
        "--output", "-o", dest="output",
        help="--import-csv 的输出路径（默认与原文件同目录的 <名字>_unified.csv）",
    )
    parser.add_argument(
        "--target-dir", default=str(DEFAULT_TARGET_DIR),
        help=f"下载目录（默认 {DEFAULT_TARGET_DIR}）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="下载超时（秒）")
    parser.add_argument("--force", action="store_true", help="目标文件已存在时强制重新下载")
    parser.add_argument("--url", default=None, help="覆盖注册表 URL（镜像失效时使用）")
    parser.add_argument("--sha256", default=None, help="期望 SHA256（64 位十六进制，严格校验）")
    parser.add_argument("--text-col", default=None, help="文本列名（默认自动识别）")
    parser.add_argument("--label-col", default=None, help="标签列名（默认自动识别）")
    parser.add_argument(
        "--label-map", action="append", default=[], metavar="RAW=LABEL",
        help="追加标签映射，例如 --label-map 正面=positive，可重复",
    )
    parser.add_argument(
        "--drop-invalid", action="store_true", help="丢弃无法映射的标签行（默认报错）"
    )
    parser.add_argument(
        "--sources-json", default=None, help="来源登记 JSON 路径（默认 data/raw/sources.json）"
    )
    parser.add_argument(
        "--source-note", default=None, help="来源说明 Markdown 路径（默认 data/raw/来源说明.md）"
    )
    parser.add_argument("--no-source-note", action="store_true", help="不写来源登记（临时使用）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    return parser


def _print_dataset_list() -> None:
    specs = list_datasets()
    print(f"已注册公开数据集（共 {len(specs)} 个）：")
    for spec in specs:
        sha = spec.sha256[:12] + "…" if spec.sha256 else "未固定（下载后记录实际值）"
        print(f"\n- {spec.name}  [{spec.format}]")
        print(f"    说明：{spec.description}")
        print(f"    字段：{spec.text_column} / {spec.label_column}；{spec.note}")
        print(f"    许可：{spec.license}")
        print(f"    文件：{spec.filename}；sha256：{sha}")
        print(f"    URL ：{spec.url}")


def _run_download(args: argparse.Namespace) -> int:
    try:
        path = download_dataset(
            args.download,
            target_dir=args.target_dir,
            timeout=args.timeout,
            force=args.force,
            url=args.url,
            sha256=args.sha256,
            write_source_note=not args.no_source_note,
            source_json_path=args.sources_json,
            source_note_path=args.source_note,
        )
    except DatasetError as exc:
        print(f"[datasets] 下载失败：{exc}", file=sys.stderr)
        return 1
    payload = {
        "dataset": get_dataset(args.download).name,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "records": count_records(path),
    }
    if args.json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[datasets] 已下载：{path}")
        print(f"[datasets] 大小 {payload['bytes']} 字节；条数≈{payload['records']}")
        print(f"[datasets] SHA256：{payload['sha256']}")
        print("[datasets] 已登记来源：data/raw/来源说明.md（可用 --sources-json 指定路径）")
    return 0


def _run_import(args: argparse.Namespace) -> int:
    import_path = Path(args.import_csv)
    spec = spec_for_file(import_path)
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = import_path.with_name(f"{import_path.stem}_unified.csv")
    try:
        header = read_csv_header(import_path)
    except DatasetError as exc:
        print(f"[datasets] 读取失败：{exc}", file=sys.stderr)
        return 1
    preferred_text = args.text_col or (spec.text_column if spec else None)
    preferred_label = args.label_col or (spec.label_column if spec else None)
    try:
        text_col = _detect_column(header, preferred_text, TEXT_COLUMN_ALIASES, "text")
        label_col = _detect_column(header, preferred_label, LABEL_COLUMN_ALIASES, "label")
        report = import_labeled_csv(
            import_path,
            out_path,
            text_col=text_col,
            label_col=label_col,
            label_map=_parse_label_map_args(args.label_map),
            drop_invalid=args.drop_invalid,
        )
    except DatasetError as exc:
        print(f"[datasets] 格式统一失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        import json

        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[datasets] 已统一：{import_path} → {report.output}")
        print(f"[datasets] 总 {report.total_rows} 行；保留 {report.kept}；"
              f"空文本 {report.empty_text}；重复 {report.duplicates}；"
              f"非法标签 {report.invalid_label}")
        print(f"[datasets] 正/负：{report.labels.get('positive', 0)} / "
              f"{report.labels.get('negative', 0)}")
        print(f"[datasets] 输出 SHA256：{report.output_sha256}")
    return 0


def _run_merge_balance(args: argparse.Namespace) -> int:
    if not args.output:
        print("[datasets] --merge-balance 需要 --output 指定输出路径", file=sys.stderr)
        return 1
    try:
        report = merge_balance_labeled_csv(
            args.merge_balance,
            args.output,
            seed=args.seed,
            max_per_label=args.max_per_label,
        )
    except DatasetError as exc:
        print(f"[datasets] 合并均衡失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        import json

        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[datasets] 已合并均衡 → {report.output}")
        print(f"[datasets] 总 {report.total_rows} 行；保留 {report.kept}；"
              f"每类 {report.per_label}；原正/负 {report.source_counts['positive']} / "
              f"{report.source_counts['negative']}；重复 {report.duplicates}")
        print(f"[datasets] 输出 SHA256：{report.output_sha256}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not (args.list_datasets or args.download or args.import_csv or args.merge_balance):
        parser.print_help()
        return 0
    if args.list_datasets:
        _print_dataset_list()
        return 0
    if args.download:
        return _run_download(args)
    if args.merge_balance:
        return _run_merge_balance(args)
    return _run_import(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
