"""`/api/artifacts` 与 `/api/solo-comments` 的业务逻辑层。

与 `src/videos/service.py` 同一套做法（HTTP 语义留在 `src/inference/app.py`）：

- **业务规则**：上传校验与落盘、项目 CRUD、阶段表格 / 逐条评论 / 聚合分析 / 打开 / 删除、
  单独评论增删查；
- **后台流水线调度**：`queue.Queue` + 单个 daemon 工作线程，不引入 Celery / Redis /
  APScheduler；阶段时长由 :class:`~src.artifacts.config.ArtifactsConfig` 配置；
- **优雅退出**：lifespan 关停时置位 `stop_event`，流水线在阶段内立刻退出并把项目恢复成
  `queued`（下次启动由 :meth:`ArtifactService.start` 重新入队续跑）；
- **启动恢复**：扫描 `data/uploads/` 里残留的 `queued` / `processing` 项目，统一重置为
  `queued` 并重新入队，绝不留下永远卡在 `processing` 的僵尸项目；
- **代次（`run_id`）**：重新分析时递增，旧流水线在下一处更新时自动放弃，
  避免旧轮次覆盖新状态。

单进程生效：多 uvicorn worker 各自持有独立的内存队列，本阶段只保证
**单个 uvicorn 进程**内的状态机正确（与任务要求一致）。

安全：`table` / `comments` / `analysis` / `open` 只接受**服务端生成的 id + 固定 stage 枚举**，
对外绝不接受任何路径；上传文件名只取 basename。
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..preprocessing.dataset import load_records, save_records
from ..videos import service as videos_service
from . import pipeline, preview
from .config import ArtifactsConfig
from .errors import (
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    ArtifactValidationError,
    PipelineAborted,
    PipelineFailure,
)
from .messages import MESSAGE_QUEUED, failure_message, message_for
from .opener import open_path
from .schemas import (
    ALLOWED_SUFFIXES,
    DEFAULT_COMMENTS_LIMIT,
    DEFAULT_PROJECT_NAME,
    DEFAULT_SOLO_LIMIT,
    DEFAULT_TABLE_LIMIT,
    GROUP_DEFINITIONS,
    METHOD_RULE_PLACEHOLDER,
    MAX_COMMENTS_LIMIT,
    MAX_FILE_BYTES,
    MAX_SOLO_LIMIT,
    MAX_TABLE_LIMIT,
    MAX_TOTAL_BYTES,
    SENTIMENT_LABELS,
    SOLO_ID_RE,
)
from .store import (
    UPLOAD_FILENAME,
    ArtifactStore,
    initial_stages,
    new_project_id,
    new_solo_id,
    now_iso,
    safe_solo_id,
    safe_stage,
)

logger = logging.getLogger("src.artifacts")

#: 工作线程取队列的轮询间隔（秒）；只影响空闲时的响应粒度
QUEUE_POLL_SECONDS = 0.2

#: 关停时等待工作线程退出的最长时间（秒）
SHUTDOWN_TIMEOUT_SECONDS = 5.0

#: 项目显示名长度上限（防御性，避免异常超长名字写进列表）
MAX_NAME_LENGTH = 200


# --------------------------------------------------------------------------- #
# 响应组装（字段严格按契约，内部字段不外泄）
# --------------------------------------------------------------------------- #
def project_payload(record: dict[str, Any]) -> dict[str, Any]:
    """按契约 4.5.1 裁剪项目对象（`run_id` / `seq` / `pipeline` 等内部字段不外泄）。"""
    status = record.get("status", "queued")
    sentiment = record.get("sentiment")
    return {
        "id": record.get("id"),
        "name": record.get("name") or DEFAULT_PROJECT_NAME,
        "created_at": record.get("created_at"),
        "status": status,
        "progress": int(record.get("progress", 0)),
        "message": record.get("message") or message_for(status, record.get("error")),
        "sources": list(record.get("sources") or []),
        "stages": list(record.get("stages") or initial_stages()),
        "sentiment": dict(sentiment) if isinstance(sentiment, dict) else None,
        "method": record.get("method") or METHOD_RULE_PLACEHOLDER,
        "error": record.get("error"),
    }


def solo_payload(item: dict[str, Any]) -> dict[str, Any]:
    """按契约 4.5.2 裁剪单条评论对象。"""
    return {
        "id": item.get("id"),
        "text": item.get("text") or "",
        "label": item.get("label"),
        "confidence": float(item.get("confidence") or 0.0),
        "probs": dict(item.get("probs") or {}),
        "method": item.get("method") or METHOD_RULE_PLACEHOLDER,
        "created_at": item.get("created_at"),
    }


def empty_analysis() -> dict[str, Any]:
    """未完成 / 无产物时的聚合分析兜底（全 0，绝不编造数据）。

    `method` 仍如实标注为 ``rule_placeholder``（当前唯一的预测来源）。
    """
    return {
        "method": METHOD_RULE_PLACEHOLDER,
        "total": 0,
        "sentiment": {
            "positive": 0, "negative": 0, "neutral": 0,
            "positive_ratio": 0.0, "negative_ratio": 0.0, "neutral_ratio": 0.0,
        },
        "keywords": {"positive": [], "negative": []},
        "length": {"mean": 0.0, "median": 0.0, "p90": 0.0, "max": 0},
        "sample": [],
    }


class ArtifactService:
    """数据项目 + 单独评论的业务服务（单例由 :func:`get_service` 提供）。

    Args:
        root: 落盘根目录（默认 ``data/uploads`` 或 `ARTIFACTS_DIR`；测试传临时目录）。
        config: 流水线阶段时长配置（默认读环境变量）。
    """

    def __init__(self, root: str | Path | None = None, config: ArtifactsConfig | None = None) -> None:
        self.config = config if config is not None else ArtifactsConfig.from_env()
        self.store = ArtifactStore(root)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._queued_set: set[str] = set()
        self._queue_lock = threading.Lock()
        self._run_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        """后台工作线程是否在跑。"""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """启动后台工作线程（幂等），并把残留的进行中项目重置为 `queued` 后重新入队。"""
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop.clear()
            self._seq = max(
                [int(record.get("seq") or 0) for record in self.store.list_projects()] or [0]
            )
            recovered = self._requeue_unfinished()
            self._thread = threading.Thread(
                target=self._worker, name="artifacts-pipeline", daemon=True
            )
            self._thread.start()
            logger.info(
                "数据项目流水线已启动：阶段节奏 %.3fs、恢复 %d 个未完成项目",
                self.config.total_seconds,
                recovered,
            )

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """优雅关停：置位 `stop_event` 并等后台线程退出（幂等，可重复调用）。"""
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():  # pragma: no cover - 仅在阶段等待异常长时出现
                logger.warning("数据项目流水线未在 %.1fs 内退出", timeout)
        logger.info("数据项目流水线已关停")

    def ensure_started(self) -> None:
        """确保后台线程在跑（供路由层在未走 lifespan 的场景下也能工作）。"""
        if not self.running:
            self.start()

    # ------------------------------------------------------------------ #
    # 项目：上传 / 查询
    # ------------------------------------------------------------------ #
    def create_project(self, entries: list[tuple[str, bytes]], name: str | None = None) -> dict[str, Any]:
        """校验并落盘上传文件，创建项目并自动入队处理。

        Args:
            entries: ``[(原始文件名, 文件内容)]``；文件名只取 basename，后缀与大小都会校验。
            name: 项目显示名；为空时用「未自定义数据」。

        Raises:
            ArtifactValidationError: 未收到文件 / 后缀不支持 / 文件为空 / 内容无法解析（HTTP 400）。
            ArtifactTooLargeError: 单文件 > 20 MB 或合计 > 50 MB（HTTP 413）。
        """
        files = self._validate_uploads(entries)
        project_id = self._allocate_project_id()
        directory = self.store.create_project_dir(project_id)
        try:
            sources, upload_files = self._store_initial(project_id, directory, files)
            record = self._new_record(project_id, name, sources)
            self.store.write_artifact_json(
                project_id,
                UPLOAD_FILENAME,
                {
                    "project_id": project_id,
                    "name": record["name"],
                    "uploaded_at": record["created_at"],
                    "files": upload_files,
                },
            )
            self.store.write_project(project_id, record)
        except Exception:
            # 上传失败不留半个项目目录
            self.store.remove_project(project_id)
            raise
        self.ensure_started()
        self._enqueue(project_id)
        logger.info("项目已创建：%s（%d 个来源文件）", project_id, len(files))
        return project_payload(record)

    def list_projects(self) -> list[dict[str, Any]]:
        """返回全部项目对象（按创建时间倒序）。"""
        return [project_payload(record) for record in self.store.list_projects()]

    def get_project(self, project_id: str) -> dict[str, Any]:
        """返回项目对象；不存在（或 id 非法）→ 404。"""
        record = self.store.require_project(project_id)
        return project_payload(record)

    def analyze(self, project_id: str, mode: str | None = "auto") -> dict[str, Any]:
        """重新处理项目（重置为 `queued` 并清掉上一轮产物，然后入队）。

        Raises:
            ArtifactNotFoundError: 项目不存在（HTTP 404）。
            ArtifactValidationError: `mode` 不是 `auto` / `clean`（HTTP 400）。
        """
        record = self.store.require_project(project_id)
        if mode not in (None, "", "auto", "clean"):
            raise ArtifactValidationError(
                f"不支持的处理模式：{mode!r}（当前只支持 auto / clean，训练集制备不在本接口范围内）"
            )
        self.ensure_started()
        run_id = pipeline.new_run_id(record.get("run_id"))
        self._clear_run_artifacts(project_id)
        stages = initial_stages()
        stored_stages = record.get("stages") or []
        if stored_stages and stored_stages[0].get("key") == "initial":
            # 初始数据来自同一份 upload，行列数直接沿用，避免重新分析时闪 0
            stages[0] = dict(stored_stages[0])
        updated = self.store.update_project(
            project_id,
            record.get("run_id"),
            status="queued",
            progress=0,
            message=MESSAGE_QUEUED,
            sentiment=None,
            method=METHOD_RULE_PLACEHOLDER,
            error=None,
            stages=stages,
            run_id=run_id,
        )
        if not updated:
            raise ArtifactNotFoundError(f"项目不存在：{project_id}")
        self._enqueue(project_id)
        logger.info("项目已重新入队：%s（第 %s 代）", project_id, run_id)
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        """删除项目目录与全部产物；不存在 → 404。"""
        with self._run_lock:
            record = self.store.get_project(project_id)
            if record is None:
                raise ArtifactNotFoundError(f"项目不存在：{project_id}")
            removed = self.store.remove_project(project_id)
            logger.info("项目已删除：%s", project_id)
            return removed

    # ------------------------------------------------------------------ #
    # 项目：阶段 / 表格 / 逐条评论 / 聚合分析 / 打开
    # ------------------------------------------------------------------ #
    def list_stages(self, project_id: str) -> list[dict[str, Any]]:
        """返回三段阶段清单（`initial` / `clean` / `excluded`）；不存在 → 404。"""
        record = self.store.require_project(project_id)
        return list(record.get("stages") or initial_stages())

    def table(
        self,
        project_id: str,
        *,
        stage: str = "clean",
        offset: int = 0,
        limit: int = DEFAULT_TABLE_LIMIT,
    ) -> dict[str, Any]:
        """返回某个阶段的表格分页（`stage` 只接受固定枚举，绝不接受路径）。"""
        self.store.require_project(project_id)
        stage = safe_stage(stage)
        limit = _checked_limit(limit, MAX_TABLE_LIMIT, "limit")
        offset = max(int(offset), 0)
        path = self.store.stage_path(project_id, stage)
        try:
            columns, rows, total = preview.read_table(path, offset, limit)
        except OSError as exc:
            raise ArtifactValidationError(f"读取阶段文件失败：{stage}.csv（{exc}）") from exc
        return {
            "stage": stage,
            "columns": columns,
            "rows": rows,
            "total_rows": total,
            "offset": offset,
            "limit": limit,
            "encoding": "utf-8-sig",
        }

    def comments(
        self,
        project_id: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_COMMENTS_LIMIT,
        label: str | None = None,
    ) -> dict[str, Any]:
        """返回逐条评论的占位情感结果（可按 `label` 过滤），按 clean 表行序号排列。"""
        self.store.require_project(project_id)
        limit = _checked_limit(limit, MAX_COMMENTS_LIMIT, "limit")
        offset = max(int(offset), 0)
        if label not in (None, ""):
            if label not in SENTIMENT_LABELS:
                raise ArtifactValidationError(
                    f"不支持的 label：{label!r}（可选：{'、'.join(SENTIMENT_LABELS)}）"
                )
        else:
            label = None
        directory = self.store.project_dir(project_id)
        total, items = preview.build_comments(
            directory / "clean.csv",
            directory / "sentiment.csv",
            offset=offset,
            limit=limit,
            label=label,
        )
        return {"total": total, "offset": offset, "limit": limit, "items": items}

    def analysis(self, project_id: str) -> dict[str, Any]:
        """返回聚合情感分析；未完成 / 无产物时返回全 0 兜底（仍带 `method`）。"""
        self.store.require_project(project_id)
        payload = self.store.read_artifact_json(project_id, "analysis.json")
        if not isinstance(payload, dict):
            return empty_analysis()
        fallback = empty_analysis()
        sentiment = payload.get("sentiment") if isinstance(payload.get("sentiment"), dict) else {}
        return {
            "method": payload.get("method") or METHOD_RULE_PLACEHOLDER,
            "total": int(payload.get("total") or 0),
            "sentiment": {**fallback["sentiment"], **sentiment},
            "keywords": payload.get("keywords") if isinstance(payload.get("keywords"), dict) else fallback["keywords"],
            "length": payload.get("length") if isinstance(payload.get("length"), dict) else fallback["length"],
            "sample": payload.get("sample") if isinstance(payload.get("sample"), list) else [],
        }

    def open_csv(self, project_id: str, stage: str = "clean") -> dict[str, Any]:
        """在服务器本机用系统默认程序打开某个阶段的 CSV。

        Raises:
            ArtifactNotFoundError: 项目不存在（HTTP 404）。
            ArtifactValidationError: 阶段枚举非法 / 文件不是 .csv / 解析后不在允许目录内 /
                该阶段文件不存在（HTTP 400）。
        """
        self.store.require_project(project_id)
        path = self.store.resolve_open_path(project_id, stage)
        if not path.is_file():
            raise ArtifactValidationError(f"该阶段文件不存在：{safe_stage(stage)}.csv")
        open_path(path)
        return {"opened": True, "path": str(path)}

    def resolve_stage_path(self, project_id: str, stage: str = "clean") -> dict[str, Any]:
        """**只读**返回某阶段 CSV 的绝对路径，**绝不调用 opener、不打开任何程序**。

        界面的「复制文件地址」用它，避免为了拿路径而真的弹一次 Excel。
        与 :meth:`open_csv` 共用同一套安全校验（固定 stage 枚举、只接受服务端生成的 id、
        ``resolve()`` 后必须位于 ``data/uploads/`` 或 ``data/processed/`` 内），
        区别是文件不存在时**不报错**，而是如实返回 ``exists: false``。

        Raises:
            ArtifactNotFoundError: 项目不存在（HTTP 404）。
            ArtifactValidationError: 阶段枚举非法 / 解析后不在允许目录内（HTTP 400）。
        """
        self.store.require_project(project_id)
        path = self.store.resolve_open_path(project_id, stage)
        return {"stage": safe_stage(stage), "path": str(path), "exists": path.is_file()}

    # ------------------------------------------------------------------ #
    # 左栏三段
    # ------------------------------------------------------------------ #
    def list_groups(self) -> dict[str, Any]:
        """`GET /api/artifacts`：projects / solo / videos 三段（顺序与 label 取自契约常量）。"""
        videos: list[dict[str, Any]] = []
        video_service = videos_service.peek_service()
        if video_service is not None:
            videos = list(video_service.list_videos().get("videos") or [])
        items = {
            "projects": self.list_projects(),
            "solo": [solo_payload(item) for item in self._sorted_solo()],
            "videos": videos,
        }
        return {
            "groups": [
                {"key": key, "label": label, "items": items[key]}
                for key, label in GROUP_DEFINITIONS
            ]
        }

    # ------------------------------------------------------------------ #
    # 单独评论
    # ------------------------------------------------------------------ #
    def create_solo(self, payload: dict[str, Any]) -> dict[str, Any]:
        """保存一条单独评论（校验 label 枚举、confidence ∈ [0,1]、text 非空）。

        Raises:
            ArtifactValidationError: 任一字段不合法（HTTP 400）。
        """
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ArtifactValidationError("text 不能为空或纯空白")
        label = payload.get("label")
        if label not in SENTIMENT_LABELS:
            raise ArtifactValidationError(
                f"label 必须是 {' / '.join(SENTIMENT_LABELS)} 之一，收到：{label!r}"
            )
        confidence = _checked_probability(payload.get("confidence"), "confidence")
        probs = _checked_probs(payload.get("probs"))
        method = payload.get("method") or METHOD_RULE_PLACEHOLDER
        if not isinstance(method, str) or not method.strip():
            raise ArtifactValidationError(f"method 必须是非空字符串，收到：{method!r}")

        item = {
            "id": self._allocate_solo_id(),
            "text": text.strip(),
            "label": label,
            "confidence": round(confidence, 4),
            "probs": probs,
            "method": method.strip(),
            "created_at": now_iso(),
        }
        return solo_payload(self.store.append_solo(item))

    def list_solo(self, *, offset: int = 0, limit: int = DEFAULT_SOLO_LIMIT) -> dict[str, Any]:
        """返回单独评论分页（按 `created_at` 倒序，最新在前）。"""
        limit = _checked_limit(limit, MAX_SOLO_LIMIT, "limit")
        offset = max(int(offset), 0)
        items = self._sorted_solo()
        total = len(items)
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [solo_payload(item) for item in items[offset : offset + limit]],
        }

    def _sorted_solo(self) -> list[dict[str, Any]]:
        """单独评论按 `created_at` 倒序（同秒时用 id 兜底，保证顺序稳定）。"""
        return sorted(
            self.store.list_solo(),
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")),
            reverse=True,
        )

    def delete_solo(self, comment_id: str) -> dict[str, Any]:
        """删除单条评论；不存在（或 id 形态非法）→ 404。"""
        if not isinstance(comment_id, str) or not SOLO_ID_RE.match(comment_id):
            raise ArtifactNotFoundError(f"评论不存在：{comment_id}")
        removed = self.store.delete_solo([safe_solo_id(comment_id)])
        if removed == 0:
            raise ArtifactNotFoundError(f"评论不存在：{comment_id}")
        return {"deleted": True, "deleted_count": removed}

    def delete_solo_many(self, ids: str | None) -> dict[str, Any]:
        """批量删除（`ids` 为逗号分隔），**幂等**：能删多少算多少，绝不因「没删到」报错。

        - `ids` 缺省 / 为空 / 全是空白 → ``{"deleted": true, "deleted_count": 0}``（200）；
        - 传入的 id 部分或全部不存在 → 仍 200，只统计真实删除条数；
        - 只有在 id **形态非法**（不匹配 ``^c_…``）时才 400 —— 那是客户端 bug，应显式暴露。

        这样前端「清空全部」在列表为空时天然传入空 `ids` 也不会弹错误提示。
        """
        tokens = [token.strip() for token in (ids or "").split(",") if token.strip()]
        invalid = [token for token in tokens if not SOLO_ID_RE.match(token)]
        if invalid:
            raise ArtifactValidationError(f"非法的评论 id：{invalid[0]!r}")
        if not tokens:
            return {"deleted": True, "deleted_count": 0}
        return {"deleted": True, "deleted_count": self.store.delete_solo(tokens)}

    # ------------------------------------------------------------------ #
    # 上传解析
    # ------------------------------------------------------------------ #
    def _validate_uploads(self, entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        """校验后缀 / 大小并只保留 basename（**对外绝不接受路径**）。"""
        files: list[tuple[str, bytes]] = []
        if not entries:
            raise ArtifactValidationError("未收到任何文件（字段名必须为 files）")
        total = 0
        for raw_name, content in entries:
            filename = _basename(raw_name)
            if not filename:
                raise ArtifactValidationError(f"文件名不合法：{raw_name!r}")
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise ArtifactValidationError(
                    f"不支持的文件格式：{filename}（仅支持 {'、'.join(ALLOWED_SUFFIXES)}）"
                )
            size = len(content or b"")
            if size == 0:
                raise ArtifactValidationError(f"上传文件为空：{filename}")
            if size > MAX_FILE_BYTES:
                raise ArtifactTooLargeError(
                    f"文件过大：{filename}（单文件上限 {MAX_FILE_BYTES // (1024 * 1024)} MB）"
                )
            total += size
            if total > MAX_TOTAL_BYTES:
                raise ArtifactTooLargeError(
                    f"合计文件过大（合计上限 {MAX_TOTAL_BYTES // (1024 * 1024)} MB）"
                )
            files.append((filename, content))
        return files

    def _store_initial(
        self, project_id: str, directory: Path, files: list[tuple[str, bytes]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """把上传原件解析成统一 CSV（`initial.csv`，UTF-8 BOM）并返回来源元信息。

        解析复用 `src/preprocessing.dataset`（`load_records` + `save_records`），
        不另写一套 CSV / XLSX 读取逻辑；文件名只用于 `input_file` 元信息与 `sources[].filename`，
        **不会**被当作路径使用（落盘固定为 `initial.csv`）。
        """
        sources: list[dict[str, Any]] = []
        upload_files: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="artifact-parse-") as tmp:
            tmp_dir = Path(tmp)
            records = []
            for index, (filename, content) in enumerate(files):
                # 每个文件放在独立子目录里，临时文件名保持原 basename，
                # 这样 load_records 记录的 input_file 元信息就是用户看到的文件名。
                slot = tmp_dir / str(index)
                slot.mkdir()
                local = slot / filename
                local.write_bytes(content)
                try:
                    parsed = load_records(local, keep_empty=True)
                except ValueError as exc:
                    raise ArtifactValidationError(f"{filename}：{exc}") from exc
                records.extend(parsed)
                sources.append({"filename": filename, "rows": len(parsed), "size": len(content)})
                upload_files.append(
                    {
                        "filename": filename,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "rows": len(parsed),
                    }
                )
            if not records:
                raise ArtifactValidationError("上传文件中没有任何数据行")
            save_records(records, directory / "initial.csv")
        return sources, upload_files

    def _new_record(
        self, project_id: str, name: str | None, sources: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """构造一条全新的项目记录（`stages` 里 `initial` 的行列数已可用）。"""
        self._seq += 1
        display = (name or "").strip()[:MAX_NAME_LENGTH] or DEFAULT_PROJECT_NAME
        rows, columns = preview.count_rows_columns(self.store.stage_path(project_id, "initial"))
        stages = initial_stages()
        stages[0] = {"key": "initial", "label": stages[0]["label"], "rows": rows, "columns": columns}
        return {
            "id": project_id,
            "name": display,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "status": "queued",
            "progress": 0,
            "message": MESSAGE_QUEUED,
            "sources": sources,
            "stages": stages,
            "sentiment": None,
            "method": METHOD_RULE_PLACEHOLDER,
            "error": None,
            # 内部字段（响应组装会裁剪掉）：代次编号、创建顺序、清洗管线统计
            "run_id": 1,
            "seq": self._seq,
            "pipeline": None,
        }

    def _allocate_project_id(self) -> str:
        """分配一个未被占用的项目 id。"""
        for _ in range(10):  # pragma: no branch - 实际几乎不会碰撞
            candidate = new_project_id()
            if not self.store.exists(candidate):
                return candidate
        raise ArtifactValidationError("无法分配项目 id，请稍后重试")  # pragma: no cover

    def _allocate_solo_id(self) -> str:
        """分配一个未被占用的单独评论 id。"""
        existing = {item.get("id") for item in self.store.list_solo()}
        for _ in range(10):  # pragma: no branch - 实际几乎不会碰撞
            candidate = new_solo_id()
            if candidate not in existing:
                return candidate
        raise ArtifactValidationError("无法分配评论 id，请稍后重试")  # pragma: no cover

    def _clear_run_artifacts(self, project_id: str) -> None:
        """重新分析前清掉上一轮产物（避免新状态读到旧表）。"""
        for name in ("clean.csv", "excluded.csv", "sentiment.csv", "analysis.json"):
            try:
                path = self.store.artifact_path(project_id, name)
            except ArtifactValidationError:  # pragma: no cover - id 已校验过
                continue
            try:
                if path.is_file():
                    path.unlink()
            except OSError:  # pragma: no cover - 仅在文件被占用时出现
                logger.warning("清理旧产物失败：%s", path)

    # ------------------------------------------------------------------ #
    # 队列与后台线程
    # ------------------------------------------------------------------ #
    def _requeue_unfinished(self) -> int:
        """把残留的进行中项目重置为 `queued` 并入队（服务重启后不留僵尸项目）。"""
        count = 0
        for record in self.store.list_projects():
            if record.get("status") not in ("queued", "processing"):
                continue
            project_id = record.get("id")
            if not isinstance(project_id, str):
                continue
            self.store.update_project(
                project_id,
                record.get("run_id"),
                status="queued",
                progress=0,
                message=MESSAGE_QUEUED,
                error=None,
            )
            self._enqueue(project_id)
            count += 1
        return count

    def _enqueue(self, project_id: str) -> None:
        """入队（同一项目最多只有一个待处理条目，避免重复跑）。"""
        with self._queue_lock:
            if project_id in self._queued_set:
                return
            self._queued_set.add(project_id)
        self._queue.put(project_id)

    def _worker(self) -> None:
        """后台工作线程主循环：取项目 → 跑流水线 → 处理中断 / 失败 / 重跑。"""
        while True:
            if self._stop.is_set():
                return
            try:
                project_id = self._queue.get(timeout=QUEUE_POLL_SECONDS)
            except queue.Empty:
                continue
            try:
                with self._queue_lock:
                    self._queued_set.discard(project_id)
                self._process(project_id)
            except Exception:  # noqa: BLE001 - 兜底：工作线程绝不能因单项目异常而退出
                logger.exception("数据项目处理出现未预期异常：%s", project_id)
            finally:
                self._queue.task_done()

    def _process(self, project_id: str) -> None:
        """跑一个项目的完整流水线（同线程内串行）。"""
        record = self.store.get_project(project_id)
        if record is None:
            return
        run_id = record.get("run_id")
        try:
            pipeline.run_pipeline(self.store, project_id, self.config, stop_event=self._stop)
        except PipelineAborted as exc:
            logger.info("流水线中断（将被重跑）：%s（%s）", project_id, exc)
            self._reset_to_queued(project_id, run_id)
        except PipelineFailure as exc:
            # 状态与 error 已由 pipeline 写入，这里只记日志（禁止把线程带崩）
            logger.warning("流水线失败：%s（%s）", project_id, exc)
        except Exception as exc:  # noqa: BLE001 - 任何意外都必须落到 failed
            logger.exception("流水线内部错误：%s", project_id)
            self._mark_failed(project_id, run_id, f"内部错误：{exc}")
        finally:
            self._maybe_rerun(project_id, run_id)

    def _reset_to_queued(self, project_id: str, run_id: Any = None) -> bool:
        """把项目恢复成 `queued`（仅当代次未变时才写，避免覆盖新的重新分析请求）。"""
        return self.store.update_project(
            project_id,
            run_id,
            status="queued",
            progress=0,
            message=MESSAGE_QUEUED,
            error=None,
        )

    def _mark_failed(self, project_id: str, run_id: Any, reason: str) -> None:
        """把项目写成 `failed` 并带原因（保证不出现永远卡住的项目）。"""
        self.store.update_project(
            project_id,
            run_id,
            status="failed",
            message=failure_message(reason),
            error=reason,
        )

    def _maybe_rerun(self, project_id: str, run_id: Any) -> None:
        """流水线运行期间项目被重新分析（代次变了）时补跑一轮。"""
        if self._stop.is_set():
            return
        record = self.store.get_project(project_id)
        if record is None or record.get("run_id") == run_id:
            return
        if record.get("status") == "queued":
            self._enqueue(project_id)


# --------------------------------------------------------------------------- #
# 请求参数校验工具
# --------------------------------------------------------------------------- #
def _basename(filename: Any) -> str:
    """只取 basename：把反斜杠统一成斜杠后再切最后一段（Windows / POSIX 通吃）。"""
    if not isinstance(filename, str):
        return ""
    name = os.path.basename(filename.replace("\\", "/").strip())
    return "" if name in (".", "..") else name


def _checked_limit(limit: Any, maximum: int, name: str = "limit") -> int:
    """校验分页 `limit`（1 ~ `maximum`），越界抛 :class:`ArtifactValidationError`（HTTP 400）。"""
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ArtifactValidationError(f"{name} 必须是整数") from exc
    if value < 1 or value > maximum:
        raise ArtifactValidationError(f"{name} 必须在 1 ~ {maximum} 之间（收到 {value}）")
    return value


def _checked_probability(value: Any, name: str) -> float:
    """校验概率 / 置信度：必须是 ``[0, 1]`` 的数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactValidationError(f"{name} 必须是 [0, 1] 之间的数字（收到 {value!r}）")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ArtifactValidationError(f"{name} 必须在 [0, 1] 之间（收到 {number}）")
    return number


def _checked_probs(value: Any) -> dict[str, float]:
    """校验 `probs`：``{标签: 概率}``，值为 ``[0, 1]`` 的数字；缺失时返回空对象。"""
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"probs 必须是对象（收到 {type(value).__name__}）")
    probs: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ArtifactValidationError(f"probs 的键必须是非空字符串（收到 {key!r}）")
        probs[key] = round(_checked_probability(raw, f"probs[{key}]"), 4)
    return probs


# --------------------------------------------------------------------------- #
# 进程内单例（路由层与 lifespan 共用）
# --------------------------------------------------------------------------- #
_service: ArtifactService | None = None
_service_lock = threading.Lock()


def get_service() -> ArtifactService:
    """返回进程内单例（首次调用时惰性创建，落盘目录为默认 ``data/uploads``）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ArtifactService()
    return _service


def peek_service() -> ArtifactService | None:
    """返回当前单例但**不创建**（测试清理用）。"""
    return _service


def set_service(service: ArtifactService | None) -> None:
    """替换进程内单例（测试注入临时目录 / 极短时长配置用；传 ``None`` 即清除）。"""
    global _service
    with _service_lock:
        _service = service


__all__ = [
    "ArtifactService",
    "empty_analysis",
    "get_service",
    "peek_service",
    "project_payload",
    "set_service",
    "solo_payload",
]
