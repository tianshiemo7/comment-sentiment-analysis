"""后台处理流水线：`queued → crawling → processing → completed`（失败则 `failed`）。

**默认仍是占位流水线**：不联网，按可配置时长推进状态机 + 生成占位 detail，
让前端把状态、进度条、子步骤走通，绝不让任务永远卡在 `crawling`。

**真实采集是 opt-in（默认关闭）**：当 ``PipelineConfig.real_crawl=True``
（或环境变量 ``VIDEO_REAL_CRAWL=1``，见 `crawler_adapter.py`）时，「采集评论」
阶段会调用 :func:`src.videos.crawler_adapter.collect_video_comments`
（底层是 ``src.crawler.fetch_video_comments``），用**真实条数/真实评论**生成
detail；采集失败则把任务写成 ``failed`` 并抛 :class:`PipelineFailure`。
``real_crawl=False``（默认）时行为与之前逐字段一致（现有测试逐字段断言）。

真实接入点（后续只需替换各阶段的数据来源，结构不变）：

| 阶段 | 现在的实现 | 真实接入点 |
| --- | --- | --- |
| 采集评论 | 默认按确定性种子「假装」爬到 N 条；`real_crawl=True` 时调真实爬虫 | `src.crawler.fetch_video_comments` / `crawl_video`（已实现 robots + 限速 + 风控退避 + 标准化） |
| 文本清洗 | 无实际操作，只推时间 | `src.preprocessing.clean`（清洗 + 去重 + 分词） |
| 情感推理 | 无实际操作，只推时间（真实评论的极性用规则占位预测） | `src.inference.service.load_model` + `predict_text`（真实前向） |
| 统计分析 | 无实际操作，只推时间（真实评论按真实文本 / ctime 统计） | `src.evaluation` / 词频统计，产出 sentiment / trend / keywords |
| 生成 detail | `placeholder.build_detail`（占位数据带 `placeholder: true`） | 用上面四阶段的真实产物组装同一份结构 |

并发模型：**单工作线程 + 队列**（`service.VideoService`），不引入 Celery / Redis /
APScheduler；`stop_event` 被置位时流水线立刻放弃当前阶段并抛
:class:`~src.videos.errors.PipelineAborted`，由 service 层把任务恢复成 `queued`，
保证优雅退出后重启能续跑。

真实采集的关停语义：网络采集本身是同步阻塞的，`stop_event` 只会在采集返回后的
第一个检查点生效（不会强杀正在进行的 HTTP 请求）；关停后任务仍会被恢复成 `queued`。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from . import crawler_adapter, placeholder
from .config import PipelineConfig
from .errors import PipelineAborted, PipelineFailure
from .messages import MESSAGE_COMPLETED, MESSAGE_CRAWLING, MESSAGE_PROCESSING, failure_message
from .schemas import PHASE_NAMES
from .store import VideoStore, now_iso

#: 进度区间划分：`(起始 progress, 结束 progress)`
PHASE_PROGRESS_RANGES: tuple[tuple[int, int], ...] = ((0, 40), (40, 60), (60, 85), (85, 100))

#: 模拟失败的阶段名（`VIDEO_SIMULATE_FAILURE=1` 时用，验证 `failed` + retry 分支）
FAILURE_STAGE = "情感推理"

#: 模拟失败的原因文案
SIMULATED_FAILURE_REASON = "占位流水线模拟的情感推理失败（VIDEO_SIMULATE_FAILURE=1）"


@dataclass(frozen=True)
class Stage:
    """一个流水线阶段。

    Attributes:
        name: 子步骤名（与 :data:`src.videos.schemas.PHASE_NAMES` 一致）。
        start: 该阶段起始 `progress`。
        end: 该阶段结束 `progress`。
        status: 该阶段进行时的任务状态。
        message: 该阶段进行时的 `message`。
        seconds_attr: `PipelineConfig` 上控制该阶段时长的属性名。
    """

    name: str
    start: int
    end: int
    status: str
    message: str
    seconds_attr: str


#: 流水线阶段定义（顺序即状态机顺序）
STAGES: tuple[Stage, ...] = (
    Stage("采集评论", 0, 40, "crawling", MESSAGE_CRAWLING, "crawl_seconds"),
    Stage("文本清洗", 40, 60, "processing", MESSAGE_PROCESSING, "clean_seconds"),
    Stage("情感推理", 60, 85, "processing", MESSAGE_PROCESSING, "sentiment_seconds"),
    Stage("统计分析", 85, 100, "processing", MESSAGE_PROCESSING, "stats_seconds"),
)


def initial_phases() -> list[dict[str, str]]:
    """`queued` 状态的 phases：四个子步骤全 `pending`。"""
    return [{"name": name, "status": "pending"} for name in PHASE_NAMES]


def phases_at(stage_index: int, *, failed: bool = False) -> list[dict[str, str]]:
    """返回处在第 `stage_index` 个阶段时的 phases。

    Args:
        stage_index: 当前阶段下标；`len(STAGES)` 表示全部完成。
        failed: 为 ``True`` 时把当前阶段标为 `failed`（用于 `failed` 状态）。
    """
    phases: list[dict[str, str]] = []
    for index, name in enumerate(PHASE_NAMES):
        if index < stage_index:
            status = "done"
        elif index == stage_index:
            status = "failed" if failed else "active"
        else:
            status = "pending"
        phases.append({"name": name, "status": status})
    return phases


def completed_phases() -> list[dict[str, str]]:
    """`completed` 状态的 phases：四个子步骤全 `done`。"""
    return phases_at(len(STAGES))


def stage_progress(stage: Stage, step: int, steps: int) -> int:
    """把阶段内的第 `step` 步（1..`steps`）换算成全局 `progress`（单调不减）。"""
    if steps <= 0:
        return stage.end
    ratio = min(max(step / steps, 0.0), 1.0)
    return int(round(stage.start + (stage.end - stage.start) * ratio))


def run_pipeline(
    store: VideoStore,
    bvid: str,
    config: PipelineConfig,
    *,
    stop_event: threading.Event | None = None,
    on_update: Callable[[dict[str, Any] | None], None] | None = None,
    collector: Callable[[str, crawler_adapter.VideoCrawlConfig], list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    """同步跑完一个视频的整条流水线，返回生成的 detail 字典。

    Args:
        store: 任务存储。
        bvid: BV 号（任务必须已存在）。
        config: 阶段时长等配置（**测试用毫秒级配置即可跑完整个状态机**）。
        stop_event: 关停信号；置位后立刻抛 :class:`PipelineAborted`。
        on_update: 每次成功更新任务后回调（传入任务快照），供测试做确定性断言。
        collector: 可注入的真实采集函数（默认
            :func:`src.videos.crawler_adapter.collect_video_comments`）；
            只有 ``config.real_crawl=True`` 时才会被调用，测试用它完全离线。

    Raises:
        PipelineAborted: 任务被删除 / 被 retry 换代 / 收到关停信号。
        PipelineFailure: 模拟失败、真实采集失败或流水线内部错误（任务已写成 `failed`）。
    """
    task = store.get_task(bvid)
    if task is None:
        raise PipelineAborted(f"任务不存在或已删除：{bvid}")
    run_id = task.get("run_id")

    # 评论总数：
    # - 默认占位路径：提前按确定性种子确定总数，采集阶段让 comment_count 朝它增长；
    # - 真实采集路径：采集阶段拿到记录后再用 len(records) 确定（见下方 opt-in 分支）。
    total_comments: int | None = None if config.real_crawl else _planned_comment_total(bvid)
    real_comments: list[dict[str, Any]] | None = None

    for index, stage in enumerate(STAGES):
        if _stopped(stop_event):
            raise PipelineAborted("收到关停信号")

        # ---- 模拟失败（仅测试/演示用）：在「情感推理」阶段失败 ----
        if config.simulate_failure and stage.name == FAILURE_STAGE:
            _require(
                _apply(
                    store, bvid, run_id, on_update,
                    status="failed",
                    progress=stage.start,
                    message=failure_message(SIMULATED_FAILURE_REASON),
                    error=SIMULATED_FAILURE_REASON,
                    phases=phases_at(index, failed=True),
                    comment_count=int(total_comments or 0),
                ),
                bvid,
            )
            raise PipelineFailure(SIMULATED_FAILURE_REASON)

        # ---- 进入阶段 ----
        _require(
            _apply(
                store, bvid, run_id, on_update,
                status=stage.status,
                progress=stage.start,
                message=stage.message,
                phases=phases_at(index),
            ),
            bvid,
        )

        # ---- 真实采集（opt-in）：进入「采集评论」阶段时调用适配器 ----
        # 默认 real_crawl=False 时这一整段都不会执行，输出与之前逐字段一致。
        if index == 0 and config.real_crawl:
            try:
                real_comments = _collect_real_comments(bvid, config, collector)
            except Exception as exc:  # noqa: BLE001 - 采集异常必须落 failed，不能卡在 crawling
                reason = _crawl_failure_reason(exc)
                _mark_crawl_failed(store, bvid, run_id, on_update, stage, index, reason)
                raise PipelineFailure(reason) from exc
            if not real_comments:
                reason = (
                    f"真实采集没有返回任何评论（{bvid}）："
                    "视频可能无评论、评论已关闭或接口无数据"
                )
                _mark_crawl_failed(store, bvid, run_id, on_update, stage, index, reason)
                raise PipelineFailure(reason)
            total_comments = len(real_comments)

        # ---- 阶段内分步推进（progress 单调不减）----
        # 真实采集已经消耗了真实网络时间，不再额外 sleep「采集评论」的模拟时长，
        # 否则默认配置会在真实采集后白等 12 秒；其它阶段与默认占位路径不变。
        seconds = float(getattr(config, stage.seconds_attr))
        if index == 0 and config.real_crawl:
            seconds = 0.0
        steps = config.steps_per_phase
        for step in range(1, steps + 1):
            if _stopped(stop_event):
                raise PipelineAborted("收到关停信号")
            if seconds > 0:
                _sleep(seconds / steps, stop_event)
            changes: dict[str, Any] = {"progress": stage_progress(stage, step, steps)}
            if stage.name == "采集评论":
                changes["comment_count"] = int(
                    round(int(total_comments or 0) * step / steps)
                )
            _require(_apply(store, bvid, run_id, on_update, **changes), bvid)

    # ---- 生成 detail 并落盘，然后置为 completed ----
    # 真实采集路径把真实评论交给 detail 构建；默认路径 comments=None，输出与之前完全一致。
    detail = placeholder.build_detail(
        bvid,
        finished_at=datetime.now().astimezone(),
        comment_total=total_comments,
        comments=real_comments,
    )
    store.write_detail(bvid, detail)
    _require(
        _apply(
            store, bvid, run_id, on_update,
            status="completed",
            progress=100,
            message=MESSAGE_COMPLETED,
            phases=completed_phases(),
            comment_count=int(total_comments or 0),
            title=detail["video"]["title"],
            owner=detail["video"]["owner"],
            cover_url=detail["video"]["cover_url"],
            error=None,
        ),
        bvid,
    )
    return detail


def new_run_id(previous: Any) -> int:
    """提交 / 重试时递增的代次编号（用于让旧的流水线自觉退出）。"""
    try:
        return int(previous) + 1
    except (TypeError, ValueError):
        return 1


def _planned_comment_total(bvid: str) -> int:
    """占位流水线里「本次能爬到多少条评论」（确定性，与占位 detail 保持一致）。"""
    return placeholder.planned_comment_total(bvid)


def _collect_real_comments(
    bvid: str,
    config: PipelineConfig,
    collector: Callable[[str, crawler_adapter.VideoCrawlConfig], list[dict[str, Any]]]
    | None = None,
) -> list[dict[str, Any]]:
    """真实采集：「采集评论」阶段的数据来源（默认适配层，可注入假采集器）。

    适配层负责把底层异常统一包装成 :class:`CrawlAdapterError`（原因可读）；
    ``collector`` 仅供测试注入，生产路径传 ``None`` 即走默认适配器。
    """
    crawl_config = config.crawl_config()
    if collector is not None:
        return collector(bvid, crawl_config)
    return crawler_adapter.collect_video_comments(bvid, crawl_config)


def _crawl_failure_reason(exc: BaseException) -> str:
    """把采集异常整理成任务 `error` 文案（适配层异常本身已带可读原因）。"""
    if isinstance(exc, crawler_adapter.CrawlAdapterError):
        return str(exc) or "采集评论失败"
    return f"采集评论失败：{type(exc).__name__}: {exc}"


def _mark_crawl_failed(
    store: VideoStore,
    bvid: str,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    stage: Stage,
    stage_index: int,
    reason: str,
) -> None:
    """采集失败：把任务写成 ``failed``（带原因与 failed 的 phases），不卡在 crawling。"""
    _require(
        _apply(
            store, bvid, run_id, on_update,
            status="failed",
            progress=stage.start,
            message=failure_message(reason),
            error=reason,
            phases=phases_at(stage_index, failed=True),
            comment_count=0,
        ),
        bvid,
    )


def _apply(
    store: VideoStore,
    bvid: str,
    run_id: int | None,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    **changes: Any,
) -> bool:
    """带代次校验地更新任务；成功时回调测试钩子。"""
    changes.setdefault("updated_at", now_iso())
    ok = store.update_task(bvid, run_id, **changes)
    if ok and on_update is not None:
        on_update(store.get_task(bvid))
    return ok


def _require(ok: bool, bvid: str) -> None:
    """更新失败说明任务被删 / 被 retry 换代，当前这一轮应立即退出。"""
    if not ok:
        raise PipelineAborted(f"任务已被删除或重新提交，本轮流水线退出：{bvid}")


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sleep(seconds: float, stop_event: threading.Event | None) -> None:
    """可被关停信号打断的等待（优雅退出的关键，不用裸 ``time.sleep``）。"""
    if stop_event is None:
        if seconds > 0:
            time.sleep(seconds)
        return
    if stop_event.wait(seconds):
        raise PipelineAborted("收到关停信号")
