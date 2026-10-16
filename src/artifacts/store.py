"""数据项目与单独评论的落盘存储（线程安全 + 原子写）。

落盘布局（契约 4.5.1）：

```
data/uploads/                       # PROJECTS_ROOT（默认；可用 ARTIFACTS_DIR 覆盖）
├── <project_id>/
│   ├── upload.json                 # 上传元数据（原始文件名、大小、sha256、上传时间、显示名）
│   ├── project.json                # 项目状态与阶段清单（对外主状态文件）
│   ├── initial.csv                 # 初始数据（上传原件解析后的统一 CSV，UTF-8 BOM）
│   ├── clean.csv                   # 筛选后-保留（清洗 + 分词后的评论）
│   ├── excluded.csv                # 筛选后-排除（含排除原因列）
│   ├── sentiment.csv               # 逐条情感（占位实现：label / confidence / 概率）
│   └── analysis.json               # 聚合情感分析（占比、词频、长度分布）
└── solo_comments.json              # 全部单独评论（数组，按 created_at 倒序展示）
```

设计要点（与 `src/videos/store.py` 一致）：

1. **线程安全**：所有读写都在 :class:`threading.RLock` 内完成（后台处理线程与 HTTP 线程并发）；
2. **原子落盘**：先写 `<name>.tmp` 再 :func:`os.replace`，进程被杀不会留下半截 JSON；
3. **损坏容错**：`project.json` 解析失败按「项目不存在」处理，不让服务起不来；
4. **路径安全**：任何路径都先过 :func:`safe_project_id` 正则，
   `..` / 路径分隔符 / 非法字符一律拒绝（防御性，即使调用方漏校验也穿不出去）；
   `stage` 只接受固定枚举，**对外绝不接受路径**。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ArtifactNotFoundError, ArtifactValidationError
from .schemas import (
    PROJECT_ID_RE,
    SOLO_ID_RE,
    STAGE_KEYS,
    STAGE_LABELS,
)

#: 默认落盘根目录（测试可 monkeypatch 该常量，或用 ``ARTIFACTS_DIR`` 环境变量覆盖）
DEFAULT_PROJECTS_ROOT = Path("data") / "uploads"

#: `open` 允许的另一处根目录（契约 4.5.3：解析后必须在 data/uploads/ 或 data/processed/ 内）
DEFAULT_PROCESSED_ROOT = Path("data") / "processed"

#: 环境变量：覆盖项目落盘根目录
ENV_PROJECTS_DIR = "ARTIFACTS_DIR"

#: 项目主状态文件 / 上传元数据文件名（契约锁定）
PROJECT_FILENAME = "project.json"
UPLOAD_FILENAME = "upload.json"

#: 单独评论的集中存放文件（契约锁定：data/uploads/solo_comments.json）
SOLO_FILENAME = "solo_comments.json"

#: 项目状态文件结构版本（将来迁移用）
PROJECT_VERSION = 1


def default_projects_root() -> Path:
    """返回默认落盘根目录：``ARTIFACTS_DIR`` 环境变量优先，否则 :data:`DEFAULT_PROJECTS_ROOT`。"""
    override = os.getenv(ENV_PROJECTS_DIR)
    return Path(override) if override and override.strip() else Path(DEFAULT_PROJECTS_ROOT)


def now_iso(moment: datetime | None = None) -> str:
    """返回带本地时区的 ISO8601 字符串（秒级，形如 ``2026-10-16T15:30:00+08:00``）。"""
    current = moment or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return current.isoformat(timespec="seconds")


def new_project_id() -> str:
    """生成项目 id：``p_YYYYMMDD_HHMM_<6位十六进制>``。"""
    now = datetime.now().astimezone()
    return f"p_{now:%Y%m%d}_{now:%H%M}_{secrets.token_hex(3)}"


def new_solo_id() -> str:
    """生成单独评论 id：``c_YYYYMMDD_HHMM_<6位十六进制>``。"""
    now = datetime.now().astimezone()
    return f"c_{now:%Y%m%d}_{now:%H%M}_{secrets.token_hex(3)}"


def safe_project_id(project_id: str) -> str:
    """校验项目 id 形态并原样返回；不合法时抛 :class:`ArtifactValidationError`（防目录穿越）。"""
    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id or ""):
        raise ArtifactValidationError(f"非法的项目 id：{project_id!r}")
    return project_id


def safe_solo_id(comment_id: str) -> str:
    """校验单独评论 id 形态并原样返回；不合法时抛 :class:`ArtifactValidationError`。"""
    if not isinstance(comment_id, str) or not SOLO_ID_RE.match(comment_id or ""):
        raise ArtifactValidationError(f"非法的评论 id：{comment_id!r}")
    return comment_id


def safe_stage(stage: str) -> str:
    """校验阶段枚举（``initial`` | ``clean`` | ``excluded``）；**只接受固定枚举，绝不接受路径**。"""
    if not isinstance(stage, str) or stage not in STAGE_KEYS:
        raise ArtifactValidationError(
            f"不支持的阶段：{stage!r}（可选：{'、'.join(STAGE_KEYS)}）"
        )
    return stage


def initial_stages() -> list[dict[str, Any]]:
    """`queued` 状态的阶段清单：三段都存在，行列数均为 0。"""
    return [
        {"key": key, "label": STAGE_LABELS[key], "rows": 0, "columns": 0}
        for key in STAGE_KEYS
    ]


def _deep_copy(payload: Any) -> Any:
    """基于 JSON 的深拷贝（存储里的对象结构都可 JSON 序列化）。"""
    return json.loads(json.dumps(payload, ensure_ascii=False))


class ArtifactStore:
    """项目目录 + 单独评论文件的落盘存储（线程安全）。

    Attributes:
        root: 落盘根目录（默认 :func:`default_projects_root`）。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(default_projects_root() if root is None else root)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # 路径（全部先过 id / 枚举校验）
    # ------------------------------------------------------------------ #
    @property
    def solo_path(self) -> Path:
        return self.root / SOLO_FILENAME

    def project_dir(self, project_id: str) -> Path:
        """返回 ``<root>/<project_id>``（id 先过正则，杜绝路径注入）。"""
        return self.root / safe_project_id(project_id)

    def artifact_path(self, project_id: str, filename: str) -> Path:
        """返回项目目录下某个固定产物的路径（`filename` 由本包内部常量给出）。"""
        return self.project_dir(project_id) / filename

    def stage_path(self, project_id: str, stage: str) -> Path:
        """返回 ``<project>/<stage>.csv``（stage 只接受固定枚举）。"""
        return self.artifact_path(project_id, f"{safe_stage(stage)}.csv")

    def processed_root(self) -> Path:
        """`open` 允许的第二处根目录（``data/processed/``）。"""
        return Path(DEFAULT_PROCESSED_ROOT)

    def resolve_open_path(self, project_id: str, stage: str) -> Path:
        """校验并返回可打开的 CSV 绝对路径（契约 4.5.3 的硬要求）。

        依次校验：项目 id 形态 → stage 枚举 → 扩展名为 ``.csv`` →
        ``resolve()`` 后父目录位于 ``data/uploads/`` 或 ``data/processed/`` 之内。
        任一条不满足都抛 :class:`ArtifactValidationError`（HTTP 400）。
        """
        safe_stage(stage)
        path = self.stage_path(project_id, stage).resolve()
        if path.suffix.lower() != ".csv":
            raise ArtifactValidationError(f"只允许打开 .csv 文件：{path.name}")
        allowed = [self.root.resolve(), self.processed_root().resolve()]
        if not any(path.is_relative_to(root) for root in allowed):
            raise ArtifactValidationError("目标文件不在 data/uploads/ 或 data/processed/ 之内")
        return path

    # ------------------------------------------------------------------ #
    # 项目读写
    # ------------------------------------------------------------------ #
    def create_project_dir(self, project_id: str) -> Path:
        """创建项目目录（已存在则直接复用）。"""
        directory = self.project_dir(project_id)
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def exists(self, project_id: str) -> bool:
        """项目目录与 `project.json` 都在才算存在（id 非法时返回 ``False``）。"""
        try:
            path = self.artifact_path(project_id, PROJECT_FILENAME)
        except ArtifactValidationError:
            return False
        with self._lock:
            return path.is_file()

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """读取项目记录（深拷贝）；不存在、id 非法或损坏时返回 ``None``。"""
        try:
            path = self.artifact_path(project_id, PROJECT_FILENAME)
        except ArtifactValidationError:
            return None
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return None
            return _deep_copy(payload) if isinstance(payload, dict) else None

    def require_project(self, project_id: str) -> dict[str, Any]:
        """读取项目记录，不存在时抛 :class:`ArtifactNotFoundError`（HTTP 404）。"""
        record = self.get_project(project_id)
        if record is None:
            raise ArtifactNotFoundError(f"项目不存在：{project_id}")
        return record

    def write_project(self, project_id: str, record: dict[str, Any]) -> Path:
        """原子写入项目记录，返回写出的路径。"""
        path = self.artifact_path(project_id, PROJECT_FILENAME)
        with self._lock:
            self._atomic_write_json(path, record)
        return path

    def update_project(self, project_id: str, expected_run_id: Any, **changes: Any) -> bool:
        """按 ``run_id`` 做乐观并发更新，返回是否更新成功。

        与 `src/videos/store.py` 同一套代次机制：后台流水线每步更新前都比对
        ``expected_run_id``，一旦项目被删除或重新分析，旧的那一轮就会更新失败并自觉退出，
        避免「旧流水线覆盖新状态」。

        Returns:
            ``True`` 表示项目存在且代次一致、已写入；``False`` 表示项目已消失或代次过期。
        """
        try:
            path = self.artifact_path(project_id, PROJECT_FILENAME)
        except ArtifactValidationError:
            return False
        with self._lock:
            if not path.is_file():
                return False
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return False
            if not isinstance(record, dict):
                return False
            if expected_run_id is not None and record.get("run_id") != expected_run_id:
                return False
            record.update(_deep_copy(changes))
            record["updated_at"] = changes.get("updated_at") or now_iso()
            self._atomic_write_json(path, record)
            return True

    def list_projects(self) -> list[dict[str, Any]]:
        """扫描根目录下所有合法项目并返回记录列表（按创建时间倒序，最新在前）。"""
        with self._lock:
            if not self.root.is_dir():
                return []
            candidates = sorted(
                (entry for entry in self.root.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name,
            )
        records: list[dict[str, Any]] = []
        for entry in candidates:
            if not PROJECT_ID_RE.match(entry.name):
                continue
            record = self.get_project(entry.name)
            if record is not None:
                records.append(record)
        records.sort(
            key=lambda record: (str(record.get("created_at") or ""), int(record.get("seq") or 0)),
            reverse=True,
        )
        return records

    def remove_project(self, project_id: str) -> bool:
        """删除项目目录（含全部产物）；返回之前是否存在。"""
        try:
            directory = self.project_dir(project_id)
        except ArtifactValidationError:
            return False
        with self._lock:
            existed = (directory / PROJECT_FILENAME).is_file()
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            return existed

    def write_artifact_json(self, project_id: str, filename: str, payload: dict[str, Any]) -> Path:
        """原子写入项目目录下的 JSON 产物（`upload.json` / `analysis.json`）。"""
        path = self.artifact_path(project_id, filename)
        with self._lock:
            self._atomic_write_json(path, payload)
        return path

    def read_artifact_json(self, project_id: str, filename: str) -> dict[str, Any] | None:
        """读取项目目录下的 JSON 产物；不存在或损坏返回 ``None``。"""
        try:
            path = self.artifact_path(project_id, filename)
        except ArtifactValidationError:
            return None
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

    # ------------------------------------------------------------------ #
    # 单独评论（集中存放为数组）
    # ------------------------------------------------------------------ #
    def list_solo(self) -> list[dict[str, Any]]:
        """读取全部单独评论（深拷贝）；文件不存在或损坏时按空列表处理。"""
        with self._lock:
            return self._read_solo_locked()

    def append_solo(self, item: dict[str, Any]) -> dict[str, Any]:
        """追加一条单独评论并落盘，返回写入的对象。"""
        with self._lock:
            items = self._read_solo_locked()
            items.append(_deep_copy(item))
            self._write_solo_locked(items)
            return _deep_copy(item)

    def delete_solo(self, comment_ids: list[str]) -> int:
        """删除若干条单独评论，返回实际删除的条数（不存在的 id 静默忽略）。"""
        targets = set(comment_ids)
        with self._lock:
            items = self._read_solo_locked()
            kept = [item for item in items if item.get("id") not in targets]
            removed = len(items) - len(kept)
            if removed:
                self._write_solo_locked(kept)
            return removed

    def _read_solo_locked(self) -> list[dict[str, Any]]:
        """读取 `solo_comments.json`（调用方须已持有 ``self._lock``）。"""
        path = self.solo_path
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _write_solo_locked(self, items: list[dict[str, Any]]) -> None:
        """原子写入 `solo_comments.json`（调用方须已持有 ``self._lock``）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(self.solo_path, items)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        """先写 `.tmp` 再 ``os.replace``，避免进程中断留下半截 JSON。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
