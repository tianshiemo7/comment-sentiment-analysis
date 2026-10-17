"""`/api/videos` 任务存储：`index.json` + 每个视频一个 `<bvid>.json`。

落盘位置：**默认 `outputs/videos/`**（运行期产物目录，不入库）：

```
outputs/videos/
├── index.json          # 任务索引（列表接口与恢复逻辑的唯一真相来源）
├── BV1xx411c7mD.json   # 该视频的完整 detail（契约第三节的结构）
├── BV1xx411c7mD/       # 分步流水线的任务级中间产物（settings 提交时才有）
│   ├── crawl.json      # 采集产物：原始评论记录 / 占位计数
│   ├── clean.json      # 清洗后评论（clean_text + 去空去重）
│   └── analyze.json    # 逐条规则预测结果（审计用）
└── BV1ab411c7mE.json
```

设计要点：

1. **线程安全**：所有读写都在 :class:`threading.RLock` 内完成（后台线程与 HTTP 线程并发）；
2. **原子落盘**：先写 `<name>.tmp` 再 :func:`os.replace`，避免进程被杀时留下半截 JSON；
3. **损坏容错**：`index.json` 解析失败时按空索引处理（不让服务起不来），单条 detail 损坏
   时按「无 detail」处理，由 service 层决定是否重建；
4. **路径安全**：所有 `<bvid>.json` 路径都由 :func:`safe_bvid` 校验过正则后才拼接，
   `..` / 路径分隔符 / 非法字符一律拒绝（防御性，即使调用方漏校验也穿不出去）。
  阶段产物文件名只接受固定白名单（:data:`STAGE_FILES`），不接受任何外部路径。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .schemas import BVID_RE

#: 默认落盘根目录（测试可 monkeypatch 该常量，或用 ``VIDEOS_DIR`` 环境变量覆盖）
DEFAULT_VIDEOS_DIR = Path("outputs") / "videos"

#: 任务索引文件名
INDEX_FILENAME = "index.json"

#: 索引结构版本（将来迁移用）
INDEX_VERSION = 1

#: 分步流水线的阶段产物文件名（固定白名单；不接受调用方拼路径）
STAGE_FILES: dict[str, str] = {
    "crawl": "crawl.json",
    "clean": "clean.json",
    "analyze": "analyze.json",
}


def default_root() -> Path:
    """返回默认落盘根目录：``VIDEOS_DIR`` 环境变量优先，否则 :data:`DEFAULT_VIDEOS_DIR`。"""
    override = os.getenv("VIDEOS_DIR")
    return Path(override) if override and override.strip() else Path(DEFAULT_VIDEOS_DIR)


def now_iso(moment: datetime | None = None) -> str:
    """返回带本地时区的 ISO8601 字符串（秒级，形如 ``2026-10-16T14:30:00+08:00``）。"""
    current = moment or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    return current.isoformat(timespec="seconds")


def safe_bvid(bvid: str) -> str:
    """校验 `bvid` 形态并原样返回；不合法时抛 :class:`ValueError`（防目录穿越）。"""
    if not isinstance(bvid, str) or not BVID_RE.match(bvid or ""):
        raise ValueError(f"非法的 BV 号：{bvid!r}（必须匹配 {BVID_RE.pattern}）")
    return bvid


class VideoStore:
    """任务索引 + 单视频 detail 的落盘存储（线程安全）。

    Attributes:
        root: 落盘根目录（默认 :func:`default_root`）。
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(default_root() if root is None else root)
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # 索引读写
    # ------------------------------------------------------------------ #
    @property
    def index_path(self) -> Path:
        return self.root / INDEX_FILENAME

    def load(self) -> None:
        """从 `index.json` 读回索引（文件缺失或损坏时按空索引处理，不抛异常）。"""
        with self._lock:
            self._index = {}
            path = self.index_path
            if not path.is_file():
                return
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return
            videos = payload.get("videos") if isinstance(payload, dict) else None
            if not isinstance(videos, list):
                return
            for task in videos:
                if not isinstance(task, dict):
                    continue
                bvid = task.get("bvid")
                if isinstance(bvid, str) and BVID_RE.match(bvid):
                    self._index[bvid] = task

    def list_tasks(self) -> list[dict[str, Any]]:
        """返回全部任务记录的**深拷贝**（调用方随便改，不会污染存储）。"""
        with self._lock:
            return [json.loads(json.dumps(task, ensure_ascii=False)) for task in self._index.values()]

    def get_task(self, bvid: str) -> dict[str, Any] | None:
        """按 `bvid` 取任务记录（深拷贝）；不存在或非法 bvid 返回 ``None``。"""
        try:
            safe_bvid(bvid)
        except ValueError:
            return None
        with self._lock:
            task = self._index.get(bvid)
            return None if task is None else json.loads(json.dumps(task, ensure_ascii=False))

    def upsert_task(self, task: dict[str, Any]) -> None:
        """写入 / 覆盖一条任务记录并立即落盘索引。"""
        bvid = safe_bvid(task.get("bvid", ""))
        with self._lock:
            self._index[bvid] = json.loads(json.dumps(task, ensure_ascii=False))
            self._flush_index_locked()

    def update_task(self, bvid: str, expected_run_id: int | None, **changes: Any) -> bool:
        """按 ``run_id`` 做乐观并发更新，返回是否更新成功。

        `expected_run_id` 是每次提交 / 重试自增的一代编号：后台流水线每步更新前都拿它比对，
        一旦任务被 retry、重新提交或删除，旧的那一轮就会更新失败并自觉退出，
        从而避免「旧流水线覆盖新状态」。传 ``None`` 表示不校验代次。

        Note:
            代次校验参数名为 ``expected_run_id``（而不是 ``run_id``），因为新代次号本身
            也要通过 ``changes`` 写进任务记录（``update_task(bvid, old, run_id=new)``）。

        Returns:
            ``True`` 表示任务存在且代次一致、已写入；``False`` 表示任务已消失或代次过期。
        """
        try:
            safe_bvid(bvid)
        except ValueError:
            return False
        with self._lock:
            task = self._index.get(bvid)
            if task is None:
                return False
            if expected_run_id is not None and task.get("run_id") != expected_run_id:
                return False
            task.update(json.loads(json.dumps(changes, ensure_ascii=False)))
            task["updated_at"] = changes.get("updated_at") or now_iso()
            self._flush_index_locked()
            return True

    def remove_task(self, bvid: str) -> bool:
        """删除任务记录、detail 文件与阶段产物目录；返回是否真的删掉了索引记录。"""
        try:
            safe_bvid(bvid)
        except ValueError:
            return False
        with self._lock:
            existed = self._index.pop(bvid, None) is not None
            if existed:
                self._flush_index_locked()
            detail = self.detail_path(bvid)
            try:
                if detail.is_file():
                    detail.unlink()
            except OSError:
                pass
            self.clear_stage_artifacts(bvid)
            return existed

    # ------------------------------------------------------------------ #
    # 单视频 detail 读写
    # ------------------------------------------------------------------ #
    def detail_path(self, bvid: str) -> Path:
        """返回 ``<root>/<bvid>.json``（bvid 先过正则，杜绝路径注入）。"""
        return self.root / f"{safe_bvid(bvid)}.json"

    def read_detail(self, bvid: str) -> dict[str, Any] | None:
        """读取单视频 detail；不存在、非法或损坏时返回 ``None``。"""
        try:
            path = self.detail_path(bvid)
        except ValueError:
            return None
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

    def write_detail(self, bvid: str, detail: dict[str, Any]) -> Path:
        """原子写入单视频 detail，返回写出的路径。"""
        path = self.detail_path(bvid)
        with self._lock:
            self._atomic_write_json(path, detail)
        return path

    # ------------------------------------------------------------------ #
    # 分步流水线中间产物（`outputs/videos/<bvid>/`，不覆盖 `<bvid>.json`）
    # ------------------------------------------------------------------ #
    def stage_dir(self, bvid: str) -> Path:
        """返回任务级中间产物目录 ``<root>/<bvid>/``（bvid 先过正则）。"""
        return self.root / safe_bvid(bvid)

    def stage_path(self, bvid: str, stage: str) -> Path:
        """返回 ``<root>/<bvid>/<stage>.json``；阶段名只接受固定白名单。"""
        filename = STAGE_FILES.get(stage)
        if filename is None:
            raise ValueError(f"未知的阶段产物：{stage!r}（只支持 {sorted(STAGE_FILES)}）")
        return self.stage_dir(bvid) / filename

    def read_stage(self, bvid: str, stage: str) -> dict[str, Any] | None:
        """读取某阶段中间产物；不存在 / 损坏 / 阶段名非法时返回 ``None``。"""
        try:
            path = self.stage_path(bvid, stage)
        except ValueError:
            return None
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                return None
            return payload if isinstance(payload, dict) else None

    def write_stage(self, bvid: str, stage: str, payload: dict[str, Any]) -> Path:
        """原子写入某阶段中间产物，返回写出的路径。"""
        path = self.stage_path(bvid, stage)
        with self._lock:
            self._atomic_write_json(path, payload)
        return path

    def clear_stage_artifacts(self, bvid: str) -> None:
        """删除该任务的全部中间产物目录（retry / delete 用；不存在时静默忽略）。"""
        try:
            directory = self.stage_dir(bvid)
        except ValueError:
            return
        with self._lock:
            try:
                if directory.is_dir():
                    shutil.rmtree(directory, ignore_errors=True)
            except OSError:  # pragma: no cover - 极端文件系统错误
                pass

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _flush_index_locked(self) -> None:
        """写索引（调用方须已持有 ``self._lock``）。"""
        payload = {
            "version": INDEX_VERSION,
            "updated_at": now_iso(),
            "total": len(self._index),
            "videos": list(self._index.values()),
        }
        self._atomic_write_json(self.index_path, payload)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """先写 `.tmp` 再 ``os.replace``，避免进程中断留下半截 JSON。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
