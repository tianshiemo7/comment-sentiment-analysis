"""后台处理流水线：`queued → crawling → processing → completed`（失败则 `failed`）。

**默认仍是占位流水线**：不联网，按可配置时长推进状态机 + 生成占位 detail，
让前端把状态、进度条、子步骤走通，绝不让任务永远卡在 `crawling`。

**两种运行模式**：

1. **旧路径（不带 settings 提交）**：一次跑完四个阶段，状态只有
   ``queued → crawling → processing → completed``，输出与引入设置前逐字段一致
   （全部既有测试逐字段断言，必须零回归）；
2. **分步流水线（带 settings 提交，spec 第 3 节）**：在阶段边界暂停为
   ``crawled``（待清洗）/ ``cleaned``（待分析），由 ``POST /api/videos/{bvid}/continue``
   触发下一步；``auto_clean`` / ``auto_analyze`` 控制是否连续执行。中间产物落盘到
   ``outputs/videos/<bvid>/``（不覆盖 ``<bvid>.json``）。

真实采集是 opt-in：旧路径由 ``PipelineConfig.real_crawl``（环境变量
``VIDEO_REAL_CRAWL``）控制；分步流水线由**任务级设置快照**的 ``crawl.real_crawl``
控制。真实采集时「采集评论」阶段调用
:func:`src.videos.crawler_adapter.collect_video_comments`（底层是
``src.crawler.fetch_video_comments``），账号模式下把服务端已保存 Cookie **逐任务**
注入 ``BiliClient``（不读全局环境写回、不进日志与任务索引）。

真实接入点（后续只需替换各阶段的数据来源，结构不变）：

| 阶段 | 旧路径（占位） | 分步流水线 |
| --- | --- | --- |
| 采集评论 | 确定性种子「假装」爬到 N 条 | 真实爬虫 / 占位计数 → `crawl.json` |
| 文本清洗 | 无实际操作，只推时间 | `clean_text` 去空去重 → `clean.json` |
| 情感推理 | 无实际操作，只推时间 | 逐条 `predict_rule_based` → `analyze.json` |
| 统计分析 | 无实际操作，只推时间 | `placeholder.build_detail(comments=...)` 聚合 |
| 生成 detail | `placeholder.build_detail` | 同上（真实评论 → `placeholder: false`） |

并发模型：**单工作线程 + 队列**（`service.VideoService`），不引入 Celery / Redis /
APScheduler；`stop_event` 被置位时流水线立刻放弃当前阶段并抛
:class:`~src.videos.errors.PipelineAborted`，由 service 层把任务恢复成 `queued`，
保证优雅退出后重启能续跑（``crawled`` / ``cleaned`` 等待态保持不动）。

真实采集的关停语义：网络采集本身是同步阻塞的，`stop_event` 只会在采集返回后的
第一个检查点生效（不会强杀正在进行的 HTTP 请求）；关停后任务仍会被恢复成 `queued`。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from . import crawler_adapter, placeholder
from . import settings as video_settings
from .config import PipelineConfig
from .errors import PipelineAborted, PipelineFailure
from .messages import (
    MESSAGE_CLEANED,
    MESSAGE_COMPLETED,
    MESSAGE_CRAWLING,
    MESSAGE_CRAWLED,
    MESSAGE_PROCESSING,
    failure_message,
)
from .schemas import PHASE_NAMES
from .store import VideoStore, now_iso

#: 进度区间划分：`(起始 progress, 结束 progress)`
PHASE_PROGRESS_RANGES: tuple[tuple[int, int], ...] = ((0, 40), (40, 60), (60, 85), (85, 100))

#: 分步流水线在「采集完成 / 清洗完成」暂停时的 `progress`（阶段边界）
CRAWL_PAUSE_PROGRESS = PHASE_PROGRESS_RANGES[0][1]
CLEAN_PAUSE_PROGRESS = PHASE_PROGRESS_RANGES[1][1]

#: 分步流水线的阶段名（`resume_stage` 取值）
STAGE_CRAWL = "crawl"
STAGE_CLEAN = "clean"
STAGE_ANALYZE = "analyze"

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
    task_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """同步跑完一个视频的流水线，返回生成的 detail 字典。

    Args:
        store: 任务存储。
        bvid: BV 号（任务必须已存在）。
        config: 阶段时长等配置（**测试用毫秒级配置即可跑完整个状态机**）。
        stop_event: 关停信号；置位后立刻抛 :class:`PipelineAborted`。
        on_update: 每次成功更新任务后回调（传入任务快照），供测试做确定性断言。
        collector: 可注入的真实采集函数（默认
            :func:`src.videos.crawler_adapter.collect_video_comments`）；
            只有任务需要真实采集时才会被调用，测试用它完全离线。
        task_settings: 任务级设置快照；**只有带 settings 提交的任务才会传入**。
            为 ``None`` 时走旧路径（一次跑完四阶段，行为与既有测试逐字段一致）；
            非 ``None`` 时走分步流水线，在阶段边界可能返回 ``None`` 表示已暂停。

    Raises:
        PipelineAborted: 任务被删除 / 被 retry 换代 / 收到关停信号。
        PipelineFailure: 模拟失败、真实采集失败或流水线内部错误（任务已写成 `failed`）。
    """
    if task_settings is not None:
        return run_staged_pipeline(
            store,
            bvid,
            config,
            task_settings=task_settings,
            stop_event=stop_event,
            on_update=on_update,
            collector=collector,
        )

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
    """真实采集（旧路径）：「采集评论」阶段的数据来源（默认适配层，可注入假采集器）。

    适配层负责把底层异常统一包装成 :class:`CrawlAdapterError`（原因可读）；
    ``collector`` 仅供测试注入，生产路径传 ``None`` 即走默认适配器。
    """
    return _collect_comments(bvid, config.crawl_config(), collector)


def _collect_comments(
    bvid: str,
    crawl_config: crawler_adapter.VideoCrawlConfig,
    collector: Callable[[str, crawler_adapter.VideoCrawlConfig], list[dict[str, Any]]]
    | None = None,
) -> list[dict[str, Any]]:
    """按给定采集配置取评论（旧路径与分步流水线共用；可注入假采集器）。"""
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


# =========================================================================== #
# 分步流水线（带 settings 提交的任务，spec 第 3 节）
# =========================================================================== #
def _phases_done(count: int) -> list[dict[str, str]]:
    """暂停态的 phases：前 ``count`` 个 ``done``，其余 ``pending``（不出现 active）。"""
    return [
        {"name": name, "status": "done" if index < count else "pending"}
        for index, name in enumerate(PHASE_NAMES)
    ]


def initial_stage_state() -> dict[str, str]:
    """任务级阶段完成状态（内部字段，不对外暴露）。"""
    return {STAGE_CRAWL: "pending", STAGE_CLEAN: "pending", STAGE_ANALYZE: "pending"}


def _all_stages_done() -> dict[str, str]:
    """完成态的 `stage_state`：三个数据阶段全部 done。"""
    return {STAGE_CRAWL: "done", STAGE_CLEAN: "done", STAGE_ANALYZE: "done"}


def _progress_value(start: int, end: int, step: int, steps: int) -> int:
    """阶段内第 ``step`` 步（1..``steps``）的全局 progress（单调不减）。"""
    if steps <= 0:
        return end
    ratio = min(max(step / steps, 0.0), 1.0)
    return int(round(start + (end - start) * ratio))


def _run_stage_progress(
    store: VideoStore,
    bvid: str,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    *,
    status: str,
    message: str,
    phase_index: int,
    start: int,
    end: int,
    seconds: float,
    steps: int,
    stop_event: threading.Event | None,
    comment_count: int | None = None,
    animate_comment_count: bool = False,
) -> None:
    """进入一个阶段并分步推进 progress（可被 stop_event 打断）。

    ``comment_count`` 是本次阶段确定的评论数：

    - ``animate_comment_count=True``（仅「采集评论」阶段）时随 progress 从 0 增长到该值，
      让采集进度更直观；
    - 默认 ``False`` 时在进入阶段时**一次性写入并保持不变**，避免清洗 / 分析阶段
      把已入库评论数来回改写（真实数据下会出现 20→5→15 的闪烁，占位模式下
      甚至会把「待清洗」时显示的条数清成 0）。
    """
    entry_changes: dict[str, Any] = {
        "status": status,
        "progress": start,
        "message": message,
        "phases": phases_at(phase_index),
    }
    if comment_count is not None and not animate_comment_count:
        entry_changes["comment_count"] = int(comment_count)
    _require(_apply(store, bvid, run_id, on_update, **entry_changes), bvid)
    for step in range(1, max(steps, 1) + 1):
        if _stopped(stop_event):
            raise PipelineAborted("收到关停信号")
        if seconds > 0:
            _sleep(seconds / max(steps, 1), stop_event)
        changes: dict[str, Any] = {"progress": _progress_value(start, end, step, max(steps, 1))}
        if comment_count is not None and animate_comment_count:
            changes["comment_count"] = int(round(int(comment_count) * step / max(steps, 1)))
        _require(_apply(store, bvid, run_id, on_update, **changes), bvid)


def _build_staged_crawl_config(
    crawl_settings: Mapping[str, Any], cookie: str
) -> crawler_adapter.VideoCrawlConfig:
    """按任务级设置快照构造适配层采集配置（Cookie 逐任务注入，不进日志）。"""

    def _get(name: str, default: Any) -> Any:
        return crawl_settings.get(name, default)

    return crawler_adapter.VideoCrawlConfig(
        max_pages=int(_get("max_pages", crawler_adapter.DEFAULT_MAX_PAGES)),
        include_replies=bool(_get("include_replies", crawler_adapter.DEFAULT_INCLUDE_REPLIES)),
        max_sub_pages=int(_get("max_sub_pages", crawler_adapter.DEFAULT_MAX_SUB_PAGES)),
        max_items=int(_get("max_items", crawler_adapter.DEFAULT_MAX_ITEMS)),
        delay_seconds=float(_get("delay_seconds", crawler_adapter.DEFAULT_DELAY_SECONDS)),
        timeout=float(_get("timeout_seconds", crawler_adapter.DEFAULT_TIMEOUT)),
        real_crawl=True,
        page_size=int(_get("page_size", crawler_adapter.DEFAULT_PAGE_SIZE)),
        sort_mode=str(_get("sort_mode", crawler_adapter.DEFAULT_SORT_MODE)),
        init_buvid=bool(_get("init_buvid", crawler_adapter.DEFAULT_INIT_BUVID)),
        require_login=bool(_get("require_login", crawler_adapter.DEFAULT_REQUIRE_LOGIN)),
        cookie=cookie or None,
    )


def _first_video_title(records: list[Mapping[str, Any]]) -> str | None:
    """从真实采集记录里取视频标题（拿不到时返回 None，不伪造）。"""
    for record in records:
        title = str(record.get("video_title") or "").strip()
        if title:
            return title
    return None


def _clean_comment_records(
    records: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """清洗阶段：对每条评论执行 ``clean_text``，去掉清洗后为空的，再按文本去重。

    Returns:
        ``(cleaned_records, stats)``：``cleaned_records`` 保留原记录的全部字段
        （``text`` 替换为清洗后文本、原文本存 ``raw_text``）；``stats`` 记录
        原始条数 / 保留条数 / 空文本数 / 重复条数（审计用，写入 ``clean.json``）。
    """
    from ..preprocessing.clean import clean_text

    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped_empty = 0
    dropped_duplicate = 0
    for record in records:
        raw_text = str(record.get("text") or "")
        text = clean_text(raw_text)
        if not text:
            dropped_empty += 1
            continue
        if text in seen:
            dropped_duplicate += 1
            continue
        seen.add(text)
        item = dict(record)
        item["raw_text"] = raw_text
        item["text"] = text
        cleaned.append(item)
    return cleaned, {
        "raw_total": len(records),
        "kept": len(cleaned),
        "dropped_empty": dropped_empty,
        "dropped_duplicate": dropped_duplicate,
    }


def _predict_cleaned_records(
    records: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """分析阶段：对每条清洗后评论调用 ``predict_rule_based``（确定性规则占位预测）。

    单条预测异常时退化为中性（0.5），不影响整份 detail 的构建。
    """
    from ..inference.service import predict_rule_based

    predictions: list[dict[str, Any]] = []
    for record in records:
        text = str(record.get("text") or "")
        try:
            predictions.append(predict_rule_based(text))
        except Exception:  # noqa: BLE001 - 单条预测失败不能拖垮整个任务
            predictions.append(
                {
                    "text": text,
                    "label": "positive",
                    "confidence": 0.5,
                    "probs": {"positive": 0.5, "negative": 0.5},
                }
            )
    return predictions


def _pause_staged(
    store: VideoStore,
    bvid: str,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    *,
    status: str,
    progress: int,
    message: str,
    phases: list[dict[str, str]],
    resume_stage: str,
    stage_state: Mapping[str, str],
    comment_count: int,
    title: str | None = None,
) -> None:
    """把任务写成等待态（``crawled`` / ``cleaned``），保留已完成阶段与续跑位置。"""
    changes: dict[str, Any] = {
        "status": status,
        "progress": progress,
        "message": message,
        "phases": phases,
        "resume_stage": resume_stage,
        "stage_state": dict(stage_state),
        "comment_count": comment_count,
        "error": None,
    }
    if title:
        changes["title"] = title
    _require(_apply(store, bvid, run_id, on_update, **changes), bvid)


def _mark_staged_failed(
    store: VideoStore,
    bvid: str,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    phase_index: int,
    reason: str,
) -> None:
    """分步流水线失败：任务写成 ``failed``（带可读原因与 failed 的 phase）。"""
    index = min(max(int(phase_index), 0), len(PHASE_NAMES) - 1)
    _require(
        _apply(
            store, bvid, run_id, on_update,
            status="failed",
            progress=PHASE_PROGRESS_RANGES[index][0],
            message=failure_message(reason),
            error=reason,
            phases=phases_at(index, failed=True),
        ),
        bvid,
    )


def _staged_crawl_stage(
    store: VideoStore,
    bvid: str,
    config: PipelineConfig,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    *,
    crawl_settings: Mapping[str, Any],
    is_real: bool,
    cookie: str,
    stop_event: threading.Event | None,
    collector: Callable[[str, crawler_adapter.VideoCrawlConfig], list[dict[str, Any]]]
    | None,
) -> tuple[int, str | None]:
    """「采集评论」阶段（分步流水线）：真实爬虫 / 占位计数 → ``crawl.json``。"""
    records: list[dict[str, Any]] = []
    title: str | None = None
    if is_real:
        crawl_config = _build_staged_crawl_config(crawl_settings, cookie)
        try:
            records = _collect_comments(bvid, crawl_config, collector)
        except Exception as exc:  # noqa: BLE001 - 采集异常必须落 failed，不能卡在 crawling
            reason = video_settings.redact_secrets(_crawl_failure_reason(exc), cookie)
            _mark_staged_failed(store, bvid, run_id, on_update, 0, reason)
            raise PipelineFailure(reason) from exc
        if not records:
            reason = (
                f"真实采集没有返回任何评论（{bvid}）："
                "视频可能无评论、评论已关闭或接口无数据"
            )
            _mark_staged_failed(store, bvid, run_id, on_update, 0, reason)
            raise PipelineFailure(reason)
        total = len(records)
        title = _first_video_title(records)
        seconds = 0.0  # 真实采集已消耗网络时间，不再额外等待模拟时长
    else:
        total = _planned_comment_total(bvid)
        seconds = float(config.crawl_seconds)

    store.write_stage(
        bvid,
        STAGE_CRAWL,
        {
            "bvid": bvid,
            "mode": "real" if is_real else "placeholder",
            "comment_total": total,
            "records": records,
            "collected_at": now_iso(),
        },
    )
    _run_stage_progress(
        store, bvid, run_id, on_update,
        status="crawling",
        message=MESSAGE_CRAWLING,
        phase_index=0,
        start=0,
        end=CRAWL_PAUSE_PROGRESS,
        seconds=seconds,
        steps=config.steps_per_phase,
        stop_event=stop_event,
        comment_count=total,
        animate_comment_count=True,  # 采集阶段让条数随 progress 增长
    )
    return total, title


def _staged_clean_stage(
    store: VideoStore,
    bvid: str,
    config: PipelineConfig,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    *,
    stop_event: threading.Event | None,
) -> int:
    """「文本清洗」阶段（分步流水线）：``clean_text`` + 去空去重 → ``clean.json``。"""
    crawl_artifact = store.read_stage(bvid, STAGE_CRAWL)
    if not isinstance(crawl_artifact, Mapping):
        reason = "中间产物缺失（crawl.json），请重试任务"
        _mark_staged_failed(store, bvid, run_id, on_update, 1, reason)
        raise PipelineFailure(reason)
    raw_records = [
        record
        for record in (crawl_artifact.get("records") or [])
        if isinstance(record, Mapping)
    ]
    cleaned, clean_stats = _clean_comment_records(raw_records)
    # 占位模式没有真实评论记录，清洗是空操作：展示层沿用「采集到的计划条数」，
    # 避免「待清洗」→「待分析」期间评论数被清成 0（真实模式为实际保留条数）。
    display_total = len(cleaned) or int(crawl_artifact.get("comment_total") or 0)
    store.write_stage(
        bvid,
        STAGE_CLEAN,
        {
            "bvid": bvid,
            "mode": crawl_artifact.get("mode") or "placeholder",
            "comment_total": len(cleaned),
            "records": cleaned,
            "stats": clean_stats,
            "cleaned_at": now_iso(),
        },
    )
    _run_stage_progress(
        store, bvid, run_id, on_update,
        status="processing",
        message=MESSAGE_PROCESSING,
        phase_index=1,
        start=PHASE_PROGRESS_RANGES[1][0],
        end=CLEAN_PAUSE_PROGRESS,
        seconds=float(config.clean_seconds),
        steps=config.steps_per_phase,
        stop_event=stop_event,
        comment_count=display_total,
    )
    return display_total


def _staged_analyze_stage(
    store: VideoStore,
    bvid: str,
    config: PipelineConfig,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    *,
    stop_event: threading.Event | None,
) -> dict[str, Any]:
    """「情感推理 + 统计分析」阶段（分步流水线）：逐条预测 + 复用占位聚合生成 detail。"""
    clean_artifact = store.read_stage(bvid, STAGE_CLEAN)
    crawl_artifact = store.read_stage(bvid, STAGE_CRAWL)
    if not isinstance(clean_artifact, Mapping) or not isinstance(crawl_artifact, Mapping):
        reason = "中间产物缺失（clean.json / crawl.json），请重试任务"
        _mark_staged_failed(store, bvid, run_id, on_update, 2, reason)
        raise PipelineFailure(reason)

    cleaned = [
        record
        for record in (clean_artifact.get("records") or [])
        if isinstance(record, Mapping)
    ]
    predictions = _predict_cleaned_records(cleaned)
    total_for_progress = len(cleaned) or int(crawl_artifact.get("comment_total") or 0)
    store.write_stage(
        bvid,
        STAGE_ANALYZE,
        {
            "bvid": bvid,
            "comment_total": len(cleaned),
            "predictions": predictions,
            "analyzed_at": now_iso(),
        },
    )

    _run_stage_progress(
        store, bvid, run_id, on_update,
        status="processing",
        message=MESSAGE_PROCESSING,
        phase_index=2,
        start=PHASE_PROGRESS_RANGES[2][0],
        end=PHASE_PROGRESS_RANGES[2][1],
        seconds=float(config.sentiment_seconds),
        steps=config.steps_per_phase,
        stop_event=stop_event,
        comment_count=total_for_progress,
    )
    _run_stage_progress(
        store, bvid, run_id, on_update,
        status="processing",
        message=MESSAGE_PROCESSING,
        phase_index=3,
        start=PHASE_PROGRESS_RANGES[3][0],
        end=PHASE_PROGRESS_RANGES[3][1],
        seconds=float(config.stats_seconds),
        steps=config.steps_per_phase,
        stop_event=stop_event,
        comment_count=total_for_progress,
    )

    finished = datetime.now().astimezone()
    if cleaned:
        # 真实清洗后评论 → placeholder.build_detail 聚合（placeholder=False 如实标注）
        return placeholder.build_detail(
            bvid,
            finished_at=finished,
            comment_total=len(cleaned),
            comments=cleaned,
        )
    # 占位模式（real_crawl=false）：采集阶段只产生确定性计划条数，分析阶段生成占位 detail
    return placeholder.build_detail(
        bvid,
        finished_at=finished,
        comment_total=int(crawl_artifact.get("comment_total") or 0),
    )


def run_staged_pipeline(
    store: VideoStore,
    bvid: str,
    config: PipelineConfig,
    *,
    task_settings: Mapping[str, Any],
    stop_event: threading.Event | None = None,
    on_update: Callable[[dict[str, Any] | None], None] | None = None,
    collector: Callable[[str, crawler_adapter.VideoCrawlConfig], list[dict[str, Any]]]
    | None = None,
) -> dict[str, Any] | None:
    """分步流水线：按 ``resume_stage`` 从断点续跑，在阶段边界暂停为等待态。

    状态迁移（spec 3.1）::

        queued → crawling → [采集完成]
                                ├─ auto_clean=true  → processing（清洗）→ ...
                                └─ auto_clean=false → crawled（暂停）
        crawled →（continue）→ queued → processing（清洗）→ [清洗完成]
                                ├─ auto_analyze=true  → processing（情感推理→统计分析）→ completed
                                └─ auto_analyze=false → cleaned（暂停）
        cleaned →（continue）→ queued → processing（情感推理→统计分析）→ completed

    Returns:
        完成时返回 detail 字典；在 ``crawled`` / ``cleaned`` 暂停时返回 ``None``。
    """
    task = store.get_task(bvid)
    if task is None:
        raise PipelineAborted(f"任务不存在或已删除：{bvid}")
    run_id = task.get("run_id")

    snapshot = video_settings.snapshot_for_task(
        task_settings if isinstance(task_settings, Mapping) else {}
    )
    auto_clean = bool(snapshot["pipeline"]["auto_clean"])
    auto_analyze = bool(snapshot["pipeline"]["auto_analyze"])
    crawl_settings = snapshot["crawl"]
    is_real = bool(crawl_settings.get("real_crawl"))
    cookie = video_settings.account_cookie_for_mode(str(snapshot["account"]["mode"]))

    stage_state = initial_stage_state()
    saved_state = task.get("stage_state")
    if isinstance(saved_state, Mapping):
        for name in stage_state:
            if saved_state.get(name) == "done":
                stage_state[name] = "done"

    resume = task.get("resume_stage")
    if resume not in (STAGE_CRAWL, STAGE_CLEAN, STAGE_ANALYZE):
        resume = STAGE_CRAWL

    phase_index = 0
    try:
        if resume == STAGE_CRAWL:
            phase_index = 0
            total, title = _staged_crawl_stage(
                store, bvid, config, run_id, on_update,
                crawl_settings=crawl_settings,
                is_real=is_real,
                cookie=cookie,
                stop_event=stop_event,
                collector=collector,
            )
            stage_state[STAGE_CRAWL] = "done"
            changes: dict[str, Any] = {
                "stage_state": dict(stage_state),
                "resume_stage": STAGE_CLEAN,
                "comment_count": total,
            }
            if title:
                changes["title"] = title
            _require(_apply(store, bvid, run_id, on_update, **changes), bvid)
            if not auto_clean:
                _pause_staged(
                    store, bvid, run_id, on_update,
                    status="crawled",
                    progress=CRAWL_PAUSE_PROGRESS,
                    message=MESSAGE_CRAWLED,
                    phases=_phases_done(1),
                    resume_stage=STAGE_CLEAN,
                    stage_state=stage_state,
                    comment_count=total,
                    title=title,
                )
                return None

        if resume in (STAGE_CRAWL, STAGE_CLEAN):
            phase_index = 1
            cleaned_count = _staged_clean_stage(
                store, bvid, config, run_id, on_update, stop_event=stop_event
            )
            stage_state[STAGE_CLEAN] = "done"
            _require(
                _apply(
                    store, bvid, run_id, on_update,
                    stage_state=dict(stage_state),
                    resume_stage=STAGE_ANALYZE,
                    comment_count=cleaned_count,
                ),
                bvid,
            )
            if not auto_analyze:
                _pause_staged(
                    store, bvid, run_id, on_update,
                    status="cleaned",
                    progress=CLEAN_PAUSE_PROGRESS,
                    message=MESSAGE_CLEANED,
                    phases=_phases_done(2),
                    resume_stage=STAGE_ANALYZE,
                    stage_state=stage_state,
                    comment_count=cleaned_count,
                )
                return None

        phase_index = 2
        detail = _staged_analyze_stage(
            store, bvid, config, run_id, on_update, stop_event=stop_event
        )
        store.write_detail(bvid, detail)
        _require(
            _apply(
                store, bvid, run_id, on_update,
                status="completed",
                progress=100,
                message=MESSAGE_COMPLETED,
                phases=completed_phases(),
                comment_count=int(detail["video"]["comment_total"]),
                title=detail["video"]["title"],
                owner=detail["video"]["owner"],
                cover_url=detail["video"]["cover_url"],
                error=None,
                stage_state=_all_stages_done(),
                resume_stage=STAGE_ANALYZE,
            ),
            bvid,
        )
        return detail
    except (PipelineAborted, PipelineFailure):
        raise
    except Exception as exc:  # noqa: BLE001 - 任何意外都要落 failed，不能卡在中间态
        reason = video_settings.redact_secrets(
            f"流水线内部错误：{type(exc).__name__}: {exc}", cookie
        )
        _mark_staged_failed(store, bvid, run_id, on_update, phase_index, reason)
        raise PipelineFailure(reason) from exc
