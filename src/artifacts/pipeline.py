"""数据项目的后台处理流水线：`queued → processing → completed`（失败则 `failed`）。

**复用既有实现，绝不复制逻辑**：

| 阶段 | 真实工作 | 复用来源 |
| --- | --- | --- |
| 文本清洗 | 对 `initial.csv` 跑 clean 模式管线，产出 `clean.csv` / `excluded.csv` | `src.preprocessing.pipeline.prepare_comments` |
| 情感推理 | 对 clean 阶段的**原文**逐条预测，写 `sentiment.csv` | `src.inference.service.predict_rule_based` |
| 聚合统计 | 占比 / 词频 / 长度分布 / 抽样，写 `analysis.json` | 本模块（纯统计，非模型逻辑） |

**占位声明（诚实标注）**：情感结果全部来自
`src.inference.service.predict_rule_based` —— 规则 / 词典**占位**预测器，
**不是训练模型**，准确率远低于真实模型，仅用于骨架演示与接口联调；
对外 `method` 字段固定为 ``rule_placeholder``。真实模型接入后只需替换本模块
情感阶段的数据来源（`predict_rule_based` → `src.inference.service.predict_text`），
接口结构与 `method` 字段语义不变。

三分类说明：占位预测器本身只给 positive / negative 两个方向，
当它给出的两类概率相等（``0.5 / 0.5``，即没有任何情感词命中、原始情感分为 0）时，
本模块如实标注为 ``neutral``（置信度 0.5），不凭空编造极性。

进度区间：清洗 ``0~45`` → 情感 ``45~85`` → 统计 ``85~100``，阶段时长可配
（见 :class:`~src.artifacts.config.ArtifactsConfig`），测试把时长设为 0 即可瞬时跑完。
"""

from __future__ import annotations

import csv
import shutil
import statistics
import tempfile
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..inference.service import predict_rule_based
from ..preprocessing.pipeline import prepare_comments
from . import preview
from .config import ArtifactsConfig
from .errors import PipelineAborted, PipelineFailure
from .messages import MESSAGE_COMPLETED, MESSAGE_CLEANING, MESSAGE_SENTIMENT, MESSAGE_STATS, failure_message
from .schemas import METHOD_RULE_PLACEHOLDER, STAGE_KEYS, STAGE_LABELS
from .store import ArtifactStore, initial_stages, now_iso

#: 情感阶段的展示名（同时用于模拟失败的阶段名）
FAILURE_STAGE = "情感推理"

#: 模拟失败的原因文案
SIMULATED_FAILURE_REASON = "占位流水线模拟的情感推理失败（ARTIFACT_SIMULATE_FAILURE=1）"

#: 词云取词条数上限（每个极性）
KEYWORD_LIMIT = 50

#: 聚合分析里的抽样条数
SAMPLE_LIMIT = 10

#: 落盘列（sentiment.csv）：把 probs 拆成两列，便于在 Excel 里直接阅读
SENTIMENT_COLUMNS: tuple[str, ...] = (
    "index",
    "text",
    "label",
    "confidence",
    "prob_positive",
    "prob_negative",
    "method",
)

#: 落盘列（analysis.json 额外字段，仅内部留痕；接口 response_model 会裁剪掉）
ANALYSIS_INTERNAL_FIELDS = ("generated_at", "placeholder")


@dataclass(frozen=True)
class Stage:
    """一个流水线阶段。

    Attributes:
        name: 阶段名（中文，与 `message` 对应）。
        start: 该阶段起始 `progress`。
        end: 该阶段结束 `progress`。
        message: 该阶段进行时的 `message`。
        seconds_attr: `ArtifactsConfig` 上控制该阶段节奏耗时的属性名。
    """

    name: str
    start: int
    end: int
    message: str
    seconds_attr: str


#: 流水线阶段定义（顺序即状态机顺序）
STAGES: tuple[Stage, ...] = (
    Stage("文本清洗", 0, 45, MESSAGE_CLEANING, "clean_seconds"),
    Stage("情感推理", 45, 85, MESSAGE_SENTIMENT, "sentiment_seconds"),
    Stage("聚合统计", 85, 100, MESSAGE_STATS, "stats_seconds"),
)


def stage_progress(stage: Stage, step: int, steps: int) -> int:
    """把阶段内的第 `step` 步（1..`steps`）换算成全局 `progress`（单调不减）。"""
    if steps <= 0:
        return stage.end
    ratio = min(max(step / steps, 0.0), 1.0)
    return int(round(stage.start + (stage.end - stage.start) * ratio))


def new_run_id(previous: Any) -> int:
    """重新分析时递增的代次编号（用于让旧的流水线自觉退出）。"""
    try:
        return int(previous) + 1
    except (TypeError, ValueError):
        return 1


def run_pipeline(
    store: ArtifactStore,
    project_id: str,
    config: ArtifactsConfig,
    *,
    stop_event: threading.Event | None = None,
    on_update: Callable[[dict[str, Any] | None], None] | None = None,
) -> None:
    """同步跑完一个项目的整条流水线（由 `ArtifactService` 的后台线程调用）。

    Args:
        store: 项目落盘存储。
        project_id: 项目 id（项目必须已存在）。
        config: 阶段时长等配置（**测试用 0 秒配置即可瞬时跑完整个状态机**）。
        stop_event: 关停信号；置位后立刻抛 :class:`PipelineAborted`。
        on_update: 每次成功更新项目后回调（传入项目快照），供测试做确定性断言。

    Raises:
        PipelineAborted: 项目被删除 / 被重新分析换代 / 收到关停信号。
        PipelineFailure: 处理失败（项目已写成 `failed`，`error` 为原始原因）。
    """
    record = store.get_project(project_id)
    if record is None:
        raise PipelineAborted(f"项目不存在或已删除：{project_id}")
    run_id = record.get("run_id")
    directory = store.project_dir(project_id)

    work = {
        "文本清洗": lambda: _clean_stage(store, project_id, run_id, directory, on_update),
        "情感推理": lambda: _sentiment_stage(store, project_id, run_id, directory, on_update),
        "聚合统计": lambda: _stats_stage(store, project_id, run_id, directory, on_update),
    }

    try:
        for stage in STAGES:
            if _stopped(stop_event):
                raise PipelineAborted("收到关停信号")

            # ---- 模拟失败（仅测试 / 演示用）：在「情感推理」阶段失败 ----
            if config.simulate_failure and stage.name == FAILURE_STAGE:
                store.update_project(
                    project_id, run_id,
                    status="failed", progress=stage.start,
                    message=failure_message(SIMULATED_FAILURE_REASON), error=SIMULATED_FAILURE_REASON,
                )
                raise PipelineFailure(SIMULATED_FAILURE_REASON)

            # ---- 进入阶段 ----
            _require(
                _apply(
                    store, project_id, run_id, on_update,
                    status="processing", progress=stage.start, message=stage.message,
                ),
                project_id,
            )

            # ---- 逐步推进（第一步先做真实工作，其余步只按配置节奏推 progress）----
            seconds = float(getattr(config, stage.seconds_attr))
            steps = config.steps_per_stage
            for step in range(1, steps + 1):
                if _stopped(stop_event):
                    raise PipelineAborted("收到关停信号")
                if step == 1:
                    work[stage.name]()
                _sleep(seconds / steps, stop_event)
                _require(
                    _apply(store, project_id, run_id, on_update, progress=stage_progress(stage, step, steps)),
                    project_id,
                )

        # ---- 全部阶段跑完：置为 completed（放在循环之后，避免被阶段内的进度步覆盖）----
        _require(
            _apply(
                store, project_id, run_id, on_update,
                status="completed", progress=100, message=MESSAGE_COMPLETED, error=None,
            ),
            project_id,
        )
    except (PipelineAborted, PipelineFailure):
        raise
    except Exception as exc:  # noqa: BLE001 - 任何意外都必须落到 failed，不能卡在 processing
        reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        store.update_project(
            project_id, run_id,
            status="failed", message=failure_message(reason), error=reason,
        )
        raise PipelineFailure(reason) from exc


# --------------------------------------------------------------------------- #
# 阶段一：文本清洗（复用 src.preprocessing 的 clean 模式管线）
# --------------------------------------------------------------------------- #
def _clean_stage(
    store: ArtifactStore,
    project_id: str,
    run_id: Any,
    directory: Path,
    on_update: Callable[[dict[str, Any] | None], None] | None,
) -> None:
    """对 `initial.csv` 跑 clean 模式管线，产出 `clean.csv` / `excluded.csv`。

    实现方式：把 `prepare_comments` 的输出根指向临时目录（不改动仓库既有的
    `data/processed/` 约定、也不与别的项目抢同一份产物路径），跑完再把
    `comments_cleaned.csv` / `comments_excluded.csv` 搬进项目目录并改名。
    **清洗与分词逻辑全部来自 `src/preprocessing`，本模块一行都没有重写。**
    """
    initial_csv = store.stage_path(project_id, "initial")
    with tempfile.TemporaryDirectory(prefix="artifact-clean-") as tmp:
        result = prepare_comments(initial_csv, output_root=tmp, overwrite=True)
        processed = Path(tmp) / "data" / "processed"
        shutil.move(str(processed / "comments_cleaned.csv"), str(directory / "clean.csv"))
        shutil.move(str(processed / "comments_excluded.csv"), str(directory / "excluded.csv"))
        stats = result["stats"]

    stages = _stage_counts(store, project_id)
    _require(
        _apply(
            store, project_id, run_id, on_update,
            stages=stages,
            pipeline={
                "mode": "clean",
                "pipeline_version": stats.get("pipeline_version"),
                "input_rows": stats.get("input_rows"),
                "excluded_rows": stats.get("excluded_rows"),
                "exclusion_reasons": stats.get("exclusion_reasons") or {},
            },
        ),
        project_id,
    )


def _stage_counts(store: ArtifactStore, project_id: str) -> list[dict[str, Any]]:
    """读取三个阶段 CSV 的行列数（stage 只接受固定枚举）。"""
    stages = []
    for key in STAGE_KEYS:
        rows, columns = preview.count_rows_columns(store.stage_path(project_id, key))
        stages.append({"key": key, "label": STAGE_LABELS[key], "rows": rows, "columns": columns})
    return stages


# --------------------------------------------------------------------------- #
# 阶段二：占位情感（复用 src.inference.service.predict_rule_based）
# --------------------------------------------------------------------------- #
def _sentiment_stage(
    store: ArtifactStore,
    project_id: str,
    run_id: Any,
    directory: Path,
    on_update: Callable[[dict[str, Any] | None], None] | None,
) -> None:
    """对 clean 阶段的原文逐条预测，写 `sentiment.csv`（UTF-8 BOM）。"""
    clean_path = directory / "clean.csv"
    sentiment_path = directory / "sentiment.csv"
    rows = list(_iter_clean_rows(clean_path))
    with sentiment_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SENTIMENT_COLUMNS))
        writer.writeheader()
        for index, row in enumerate(rows):
            prediction = predict_comment(row["text"])
            writer.writerow(
                {
                    "index": index,
                    "text": row["text"],
                    "label": prediction["label"],
                    "confidence": prediction["confidence"],
                    "prob_positive": prediction["probs"].get("positive", 0.0),
                    "prob_negative": prediction["probs"].get("negative", 0.0),
                    "method": METHOD_RULE_PLACEHOLDER,
                }
            )
    _require(
        _apply(store, project_id, run_id, on_update, sentiment_total=len(rows)),
        project_id,
    )


def predict_comment(text: str) -> dict[str, Any]:
    """对一条原文做**占位**情感预测（唯一预测入口，供流水线与测试共用）。

    直接复用 :func:`src.inference.service.predict_rule_based`（**不是训练模型**）；
    当占位预测器给出 ``0.5 / 0.5``（没有任何情感词命中、原始情感分为 0）时，
    如实标注为三分类里的 ``neutral``，不凭空编造极性。
    """
    result = predict_rule_based(text)
    probs = dict(result["probs"])
    if probs.get("positive") == probs.get("negative"):
        return {"label": "neutral", "confidence": 0.5, "probs": probs}
    return {"label": result["label"], "confidence": result["confidence"], "probs": probs}


# --------------------------------------------------------------------------- #
# 阶段三：聚合统计
# --------------------------------------------------------------------------- #
def _stats_stage(
    store: ArtifactStore,
    project_id: str,
    run_id: Any,
    directory: Path,
    on_update: Callable[[dict[str, Any] | None], None] | None,
) -> None:
    """汇总占比 / 词频 / 长度分布 / 抽样，写 `analysis.json` 并回填计数。

    注意：**不在这里改成 `completed`** —— 收尾由 `run_pipeline` 在全部阶段跑完后统一置位，
    否则阶段内的进度步会把 `progress` 从 100 覆盖回 89 之类的中间值。
    """
    clean_path = directory / "clean.csv"
    if not clean_path.is_file():
        raise ValueError("缺少 clean.csv，无法聚合统计（清洗阶段应已产出）")
    clean_rows = list(_iter_clean_rows(clean_path))
    sentiments = preview.load_sentiment(directory / "sentiment.csv")
    predictions = []
    for index in range(len(clean_rows)):
        sentiment = sentiments.get(index)
        if not sentiment or not sentiment.get("label"):
            # 缺行时直接失败，绝不把「没预测到」伪装成 neutral 结果
            raise ValueError(f"sentiment.csv 缺少第 {index} 行的情感结果，无法聚合统计")
        predictions.append(sentiment)

    analysis = build_analysis(clean_rows, predictions)
    store.write_artifact_json(project_id, "analysis.json", analysis)

    counts = analysis["sentiment"]
    stages = _stage_counts(store, project_id)

    _require(
        _apply(
            store, project_id, run_id, on_update,
            stages=stages,
            sentiment={
                "total": analysis["total"],
                "positive": counts["positive"],
                "negative": counts["negative"],
                "neutral": counts["neutral"],
            },
            method=METHOD_RULE_PLACEHOLDER,
        ),
        project_id,
    )


def build_analysis(clean_rows: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """由 clean 行与逐条占位情感构造 `analysis.json` 的内容（契约 4.5.1）。

    ``sentiment`` 里的三个占比之和恒为 1（``total > 0`` 时）；``total == 0`` 时全为 0。
    """
    total = len(clean_rows)
    counter = Counter(prediction["label"] for prediction in predictions)
    positive = int(counter.get("positive", 0))
    negative = int(counter.get("negative", 0))
    neutral = int(counter.get("neutral", 0))

    def ratio(value: int) -> float:
        return round(value / total, 4) if total else 0.0

    keywords = _build_keywords(clean_rows, predictions)
    lengths = [len(row["text"]) for row in clean_rows]
    sample = [
        {
            "index": index,
            "text": clean_rows[index]["text"],
            "label": predictions[index]["label"],
            "confidence": predictions[index]["confidence"],
        }
        for index in range(min(SAMPLE_LIMIT, total))
    ]
    return {
        "method": METHOD_RULE_PLACEHOLDER,
        "total": total,
        "sentiment": {
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "positive_ratio": ratio(positive),
            "negative_ratio": ratio(negative),
            "neutral_ratio": ratio(neutral),
        },
        "keywords": keywords,
        "length": {
            "mean": round(statistics.fmean(lengths), 2) if lengths else 0.0,
            "median": round(statistics.median(lengths), 2) if lengths else 0.0,
            "p90": _percentile(lengths, 0.9),
            "max": max(lengths) if lengths else 0,
        },
        "sample": sample,
        # 内部留痕字段（接口 response_model 会裁剪掉）
        "generated_at": now_iso(),
        "placeholder": True,
    }


def _build_keywords(
    clean_rows: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """按极性统计分词词频（词来自 `src/preprocessing` 产出的 `tokens` 列）。"""
    buckets: dict[str, Counter[str]] = {"positive": Counter(), "negative": Counter()}
    for row, prediction in zip(clean_rows, predictions):
        bucket = buckets.get(prediction["label"])
        if bucket is None:
            continue  # neutral 不进词云（契约只给 positive / negative 两组）
        for token in str(row.get("tokens") or "").split():
            bucket[token] += 1
    result: dict[str, list[dict[str, Any]]] = {}
    for label, counter in buckets.items():
        top = counter.most_common(KEYWORD_LIMIT)
        peak = top[0][1] if top else 0
        result[label] = [
            {"word": word, "count": count, "weight": round(count / peak, 4) if peak else 0.0}
            for word, count in top
        ]
    return result


def _percentile(values: list[int], quantile: float) -> float:
    """线性插值分位数（`statistics.quantiles` 在小样本下的兜底实现）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 2)


def _iter_clean_rows(path: Path):
    """逐行读取 `clean.csv`，返回 ``{"text": 原文, "tokens": str}``（原文取 text_raw 优先）。"""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {"text": preview.text_of(row), "tokens": row.get("tokens") or ""}


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _apply(
    store: ArtifactStore,
    project_id: str,
    run_id: Any,
    on_update: Callable[[dict[str, Any] | None], None] | None,
    **changes: Any,
) -> bool:
    """带代次校验地更新项目；成功时回调测试钩子。"""
    changes.setdefault("updated_at", now_iso())
    ok = store.update_project(project_id, run_id, **changes)
    if ok and on_update is not None:
        on_update(store.get_project(project_id))
    return ok


def _require(ok: bool, project_id: str) -> None:
    """更新失败说明项目被删 / 被重新分析换代，当前这一轮应立即退出。"""
    if not ok:
        raise PipelineAborted(f"项目已被删除或重新分析，本轮流水线退出：{project_id}")


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sleep(seconds: float, stop_event: threading.Event | None) -> None:
    """可被关停信号打断的等待（优雅退出的关键，不用裸 ``time.sleep``）。"""
    if seconds <= 0:
        return
    if stop_event is None:
        time.sleep(seconds)
        return
    if stop_event.wait(seconds):
        raise PipelineAborted("收到关停信号")


#: 供 service 复用的阶段清单构造（`queued` 状态）
def queued_stages() -> list[dict[str, Any]]:
    """`queued` 状态的阶段清单（三段行列数均为 0）。"""
    return initial_stages()


__all__ = [
    "FAILURE_STAGE",
    "SENTIMENT_COLUMNS",
    "STAGES",
    "Stage",
    "build_analysis",
    "new_run_id",
    "predict_comment",
    "queued_stages",
    "run_pipeline",
    "stage_progress",
]
