"""`/api/videos` 业务逻辑层（与 Web 框架解耦：HTTP 语义留在 `src/inference/app.py`）。

职责：

- **提交 / 重试 / 删除 / 列表 / 详情** 五类操作的业务规则（BV 号校验、409 去重、404）；
- **后台流水线调度**：`queue.Queue` + 单个 daemon 工作线程（不引入 Celery / Redis /
  APScheduler），阶段时长由 :class:`~src.videos.config.PipelineConfig` 配置；
- **优雅退出**：lifespan 关停时置位 `stop_event`，流水线在阶段内立刻退出并把任务
  恢复成 `queued`（下次启动由 :meth:`VideoService.start` 重新入队续跑）；
- **启动恢复**：`index.json` 里残留的 `queued` / `crawling` / `processing` 任务统一
  重置为 `queued` 并重新入队，绝不留下永远卡在 `crawling` 的僵尸任务。

并发要点：`submit` / `retry` / `delete` 在 `_run_lock` 内串行执行；`run_id`（代次编号）
让「被 retry 或重新提交」的旧流水线在下一处更新时自动放弃，避免旧轮次覆盖新状态。

单进程生效：多 uvicorn worker 各自持有独立的内存队列，本阶段只保证
**单个 uvicorn 进程**内的状态机正确（与任务要求一致）。
"""

from __future__ import annotations

import logging
import queue
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import pipeline, placeholder
from .config import PipelineConfig
from .errors import (
    MESSAGE_BUSY,
    MESSAGE_INVALID_BVID,
    InvalidBvidError,
    PipelineAborted,
    PipelineFailure,
    VideoBusyError,
    VideoNotFoundError,
)
from .messages import MESSAGE_QUEUED, message_for
from .schemas import BVID_RE, RUNNING_STATUSES
from .store import VideoStore, now_iso

logger = logging.getLogger("src.videos")

#: 工作线程取队列的轮询间隔（秒）；只影响空闲时的响应粒度
QUEUE_POLL_SECONDS = 0.2

#: 关停时等待工作线程退出的最长时间（秒）
SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _submit_payload(task: dict[str, Any]) -> dict[str, Any]:
    """提交 / 重试接口的响应体（契约锁定为 4 个字段）。"""
    return {
        "bvid": task["bvid"],
        "status": task["status"],
        "progress": int(task.get("progress", 0)),
        "created_at": task["created_at"],
    }


class VideoService:
    """视频分析任务的业务服务（单例由 :func:`get_service` 提供）。

    Args:
        root: 任务落盘根目录（默认 ``outputs/videos`` 或 ``VIDEOS_DIR``；测试传临时目录）。
        config: 流水线阶段时长配置（默认读环境变量，合计 30 秒）。
    """

    def __init__(self, root: str | Path | None = None, config: PipelineConfig | None = None) -> None:
        self.config = config if config is not None else PipelineConfig.from_env()
        self.store = VideoStore(root)
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
        """启动后台工作线程（幂等）。

        启动时从磁盘读回索引，把上次残留的 `queued` / `crawling` / `processing`
        任务重置为 `queued` 并重新入队 —— 服务重启后不会留下卡死的任务。
        """
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop.clear()
            self.store.load()
            self._seq = max(
                [int(task.get("seq") or 0) for task in self.store.list_tasks()] or [0]
            )
            recovered = self._requeue_unfinished()
            self._thread = threading.Thread(
                target=self._worker, name="videos-pipeline", daemon=True
            )
            self._thread.start()
            logger.info(
                "视频分析流水线已启动：阶段时长 %.3fs、恢复 %d 个未完成任务",
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
                logger.warning("视频分析流水线未在 %.1fs 内退出", timeout)
        logger.info("视频分析流水线已关停")

    def ensure_started(self) -> None:
        """确保后台线程在跑（供路由层在未走 lifespan 的场景下也能工作）。"""
        if not self.running:
            self.start()

    # ------------------------------------------------------------------ #
    # 业务操作
    # ------------------------------------------------------------------ #
    def submit(self, bvid: str) -> dict[str, Any]:
        """提交 BV 号，创建（或重跑）分析任务。

        Raises:
            InvalidBvidError: BV 号不符合 ``^BV[0-9A-Za-z]{10}$``（HTTP 400）。
            VideoBusyError: 同一 BV 号已在排队 / 爬取 / 处理中（HTTP 409）。
        """
        if not isinstance(bvid, str) or not BVID_RE.match(bvid):
            raise InvalidBvidError(MESSAGE_INVALID_BVID)

        with self._run_lock:
            existing = self.store.get_task(bvid)
            if existing is not None and existing.get("status") in RUNNING_STATUSES:
                raise VideoBusyError(MESSAGE_BUSY)
            self.ensure_started()
            task = self._new_task(bvid, existing)
            self.store.upsert_task(task)
            self._enqueue(bvid)
            return _submit_payload(task)

    def retry(self, bvid: str) -> dict[str, Any]:
        """把任务重置为 `queued` 并重新入队；不存在（或 bvid 非法）→ 404。"""
        with self._run_lock:
            task = self.store.get_task(bvid)
            if task is None:
                raise VideoNotFoundError(f"任务不存在：{bvid}")
            self.ensure_started()
            run_id = pipeline.new_run_id(task.get("run_id"))
            updated = self.store.update_task(
                bvid,
                task.get("run_id"),
                status="queued",
                progress=0,
                message=MESSAGE_QUEUED,
                phases=pipeline.initial_phases(),
                comment_count=0,
                error=None,
                run_id=run_id,
            )
            if not updated:
                raise VideoNotFoundError(f"任务不存在：{bvid}")
            self._enqueue(bvid)
            payload = _submit_payload(self.store.get_task(bvid) or task)
            logger.info("任务已重试：%s（第 %s 代）", bvid, run_id)
            return payload

    def delete(self, bvid: str) -> bool:
        """删除任务与 detail 文件；不存在（或 bvid 非法）→ 404。"""
        with self._run_lock:
            if self.store.get_task(bvid) is None:
                raise VideoNotFoundError(f"任务不存在：{bvid}")
            removed = self.store.remove_task(bvid)
            logger.info("任务已删除：%s", bvid)
            return removed

    def list_videos(self) -> dict[str, Any]:
        """返回 ``{"total": int, "videos": [...]}``，按 `created_at` 倒序（最新在前）。"""
        tasks = self.store.list_tasks()
        tasks.sort(
            key=lambda task: (str(task.get("created_at") or ""), int(task.get("seq") or 0)),
            reverse=True,
        )
        return {"total": len(tasks), "videos": [self._list_item(task) for task in tasks]}

    def detail(self, bvid: str) -> dict[str, Any]:
        """返回详情响应体；不存在（或 bvid 非法）→ 404。

        未完成时**不报错**，仍返回完整结构：`sentiment` 为 ``null``，
        `trend` / `keywords` / `top_comments` 为空，`analysis.status` 为当前状态。
        """
        task = self.store.get_task(bvid)
        if task is None:
            raise VideoNotFoundError(f"任务不存在：{bvid}")
        return self.compose_detail(task)

    # ------------------------------------------------------------------ #
    # 响应组装
    # ------------------------------------------------------------------ #
    def _list_item(self, task: dict[str, Any]) -> dict[str, Any]:
        """按契约字段裁剪列表项（内部字段如 `run_id` / `seq` 不外泄）。"""
        status = task.get("status", "queued")
        return {
            "bvid": task.get("bvid"),
            "title": task.get("title"),
            "owner": task.get("owner"),
            "cover_url": task.get("cover_url"),
            "status": status,
            "progress": int(task.get("progress", 0)),
            "message": task.get("message") or message_for(status, task.get("error")),
            "phases": deepcopy(task.get("phases") or pipeline.initial_phases()),
            "comment_count": int(task.get("comment_count", 0)),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        }

    def compose_detail(self, task: dict[str, Any]) -> dict[str, Any]:
        """由任务记录（+ 已落盘的 detail）组装详情响应体。"""
        bvid = task["bvid"]
        status = task.get("status", "queued")
        completed = status == "completed"

        stored: dict[str, Any] = {}
        if completed:
            stored = self.store.read_detail(bvid) or {}
            if not stored:
                # detail 文件丢失（人为删除 / 损坏）时，用确定性占位逻辑重建，接口不报错
                logger.warning("detail 文件缺失，按占位逻辑重建：%s", bvid)
                stored = placeholder.build_detail(
                    bvid, comment_total=task.get("comment_count")
                )
                self.store.write_detail(bvid, stored)

        stored_video = stored.get("video") or {}
        video_block = {
            "bvid": bvid,
            "title": task.get("title"),
            "owner": task.get("owner"),
            "cover_url": task.get("cover_url"),
            "pubdate": stored_video.get("pubdate") if completed else None,
            "crawled_at": stored_video.get("crawled_at") if completed else None,
            "comment_total": (
                int(stored_video.get("comment_total") or task.get("comment_count") or 0)
                if completed
                else int(task.get("comment_count") or 0)
            ),
        }
        analysis_block = {
            "status": status,
            "progress": int(task.get("progress", 0)),
            "message": task.get("message") or message_for(status, task.get("error")),
            "length_stats": (stored.get("analysis") or {}).get("length_stats")
            if completed
            else None,
        }
        return {
            "video": video_block,
            "analysis": analysis_block,
            "sentiment": stored.get("sentiment") if completed else None,
            "trend": list(stored.get("trend") or []) if completed else [],
            "keywords": (
                stored.get("keywords") if completed else {"positive": [], "negative": []}
            )
            or {"positive": [], "negative": []},
            "model": stored.get("model") or placeholder.build_model_block(),
            "top_comments": (
                stored.get("top_comments") if completed else {"positive": [], "negative": []}
            )
            or {"positive": [], "negative": []},
            "error": task.get("error"),
            # 诚实标注：当前 detail 由占位流水线生成（真实爬虫未接入）
            "placeholder": bool(stored.get("placeholder", True)) if completed else True,
        }

    # ------------------------------------------------------------------ #
    # 队列与后台线程
    # ------------------------------------------------------------------ #
    def _new_task(self, bvid: str, existing: dict[str, Any] | None) -> dict[str, Any]:
        """构造一条全新的任务记录（重跑时保留 `created_at` / 标题等已抓到的信息）。"""
        self._seq += 1
        existing = existing or {}
        return {
            "bvid": bvid,
            "title": existing.get("title"),
            "owner": existing.get("owner"),
            "cover_url": existing.get("cover_url"),
            "status": "queued",
            "progress": 0,
            "message": MESSAGE_QUEUED,
            "phases": pipeline.initial_phases(),
            "comment_count": 0,
            "created_at": existing.get("created_at") or now_iso(),
            "updated_at": now_iso(),
            "error": None,
            # 内部字段（响应模型会裁剪掉）：代次编号与创建顺序
            "run_id": pipeline.new_run_id(existing.get("run_id")),
            "seq": self._seq,
        }

    def _requeue_unfinished(self) -> int:
        """把索引里未完成的任务（含上次进程残留的 running 状态）重置为 `queued` 并入队。"""
        count = 0
        for task in self.store.list_tasks():
            bvid = task.get("bvid")
            if not isinstance(bvid, str) or task.get("status") not in RUNNING_STATUSES:
                continue
            self.store.update_task(
                bvid,
                task.get("run_id"),
                status="queued",
                progress=0,
                message=MESSAGE_QUEUED,
                phases=pipeline.initial_phases(),
                error=None,
            )
            self._enqueue(bvid)
            count += 1
        return count

    def _enqueue(self, bvid: str) -> None:
        """入队（同一 BV 号最多只有一个待处理条目，避免重复跑）。"""
        with self._queue_lock:
            if bvid in self._queued_set:
                return
            self._queued_set.add(bvid)
        self._queue.put(bvid)

    def _worker(self) -> None:
        """后台工作线程主循环：取任务 → 跑流水线 → 处理中断 / 失败 / 重跑。"""
        while True:
            if self._stop.is_set():
                return
            try:
                bvid = self._queue.get(timeout=QUEUE_POLL_SECONDS)
            except queue.Empty:
                continue
            try:
                with self._queue_lock:
                    self._queued_set.discard(bvid)
                self._process(bvid)
            except Exception:  # noqa: BLE001 - 兜底：工作线程绝不能因单任务异常而退出
                logger.exception("视频分析任务出现未预期异常：%s", bvid)
            finally:
                self._queue.task_done()

    def _process(self, bvid: str) -> None:
        """跑一个任务的完整流水线（同线程内串行，天然串行化同 BV 号的两轮）。"""
        task = self.store.get_task(bvid)
        if task is None:
            return
        run_id = task.get("run_id")
        try:
            pipeline.run_pipeline(self.store, bvid, self.config, stop_event=self._stop)
        except PipelineAborted as exc:
            logger.info("流水线中断（将被重跑）：%s（%s）", bvid, exc)
            self._reset_to_queued(bvid, run_id)
        except PipelineFailure as exc:
            # 状态与 error 已由 pipeline 写入，这里只记日志（禁止把线程带崩）
            logger.warning("流水线失败：%s（%s）", bvid, exc)
        except Exception as exc:  # noqa: BLE001 - 任何意外都必须落到 failed，不能卡在 crawling
            logger.exception("流水线内部错误：%s", bvid)
            self._mark_failed(bvid, run_id, f"内部错误：{exc}")
        finally:
            self._maybe_rerun(bvid, run_id)

    def _reset_to_queued(self, bvid: str, run_id: Any = None) -> bool:
        """把任务恢复成 `queued`（仅当代次未变时才写，避免覆盖 retry 的新状态）。"""
        return self.store.update_task(
            bvid,
            run_id,
            status="queued",
            progress=0,
            message=MESSAGE_QUEUED,
            phases=pipeline.initial_phases(),
            error=None,
        )

    def _mark_failed(self, bvid: str, run_id: Any, reason: str) -> None:
        """把任务写成 `failed` 并带原因（保证不出现永远卡住的进行中任务）。"""
        self.store.update_task(
            bvid,
            run_id,
            status="failed",
            message=message_for("failed", reason),
            error=reason,
        )

    def _maybe_rerun(self, bvid: str, run_id: Any) -> None:
        """流水线运行期间任务被 retry / 重新提交（代次变了）时补跑一轮。"""
        if self._stop.is_set():
            return
        task = self.store.get_task(bvid)
        if task is None or task.get("run_id") == run_id:
            return
        if task.get("status") == "queued":
            self._enqueue(bvid)


# --------------------------------------------------------------------------- #
# 进程内单例（路由层与 lifespan 共用）
# --------------------------------------------------------------------------- #
_service: VideoService | None = None
_service_lock = threading.Lock()


def get_service() -> VideoService:
    """返回进程内单例（首次调用时惰性创建，落盘目录为默认的 ``outputs/videos``）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = VideoService()
    return _service


def peek_service() -> VideoService | None:
    """返回当前单例但**不创建**（测试清理用）。"""
    return _service


def set_service(service: VideoService | None) -> None:
    """替换进程内单例（测试注入临时目录 / 短时长配置用；传 ``None`` 即清除）。"""
    global _service
    with _service_lock:
        _service = service
