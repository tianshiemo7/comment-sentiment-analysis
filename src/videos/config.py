"""`/api/videos` 流水线的可配置参数（**阶段时长必须可配**）。

默认四段耗时合计 **30 秒**，与前端 mock 的节奏一致（`docs/API契约.md` 第四节）。
测试与联调时用环境变量把时长压到毫秒级即可跑完整个状态机，无需真等 30 秒。

环境变量（全部可选；非法值回退默认值，绝不抛异常）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_CRAWL_SECONDS` | `12` | 「采集评论」阶段耗时（秒） |
| `VIDEO_CLEAN_SECONDS` | `6` | 「文本清洗」阶段耗时（秒） |
| `VIDEO_SENTIMENT_SECONDS` | `8` | 「情感推理」阶段耗时（秒） |
| `VIDEO_STATS_SECONDS` | `4` | 「统计分析」阶段耗时（秒） |
| `VIDEO_STEPS_PER_PHASE` | `4` | 每个阶段拆成几步推进 `progress` |
| `VIDEO_SIMULATE_FAILURE` | `0` | 置 `1` 时流水线在「情感推理」阶段失败（用于验证 `failed` + retry） |
| `VIDEOS_DIR` | `outputs/videos` | 任务落盘根目录（见 `store.py`） |
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_CRAWL_SECONDS = "VIDEO_CRAWL_SECONDS"
ENV_CLEAN_SECONDS = "VIDEO_CLEAN_SECONDS"
ENV_SENTIMENT_SECONDS = "VIDEO_SENTIMENT_SECONDS"
ENV_STATS_SECONDS = "VIDEO_STATS_SECONDS"
ENV_STEPS_PER_PHASE = "VIDEO_STEPS_PER_PHASE"
ENV_SIMULATE_FAILURE = "VIDEO_SIMULATE_FAILURE"

#: 默认阶段时长（秒）：合计 30 秒，与前端 mock 一致
DEFAULT_CRAWL_SECONDS = 12.0
DEFAULT_CLEAN_SECONDS = 6.0
DEFAULT_SENTIMENT_SECONDS = 8.0
DEFAULT_STATS_SECONDS = 4.0
DEFAULT_STEPS_PER_PHASE = 4


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量；缺失或非法时回退默认值（绝不因配置写错而启动失败）。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；缺失、非法或 < 1 时回退默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量：``1/true/yes/on`` 为真，其余为假。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PipelineConfig:
    """流水线时序配置（不可变；测试直接用构造参数覆盖，无需改环境变量）。

    Attributes:
        crawl_seconds: 「采集评论」阶段耗时。
        clean_seconds: 「文本清洗」阶段耗时。
        sentiment_seconds: 「情感推理」阶段耗时。
        stats_seconds: 「统计分析」阶段耗时。
        steps_per_phase: 每个阶段推进 `progress` 的步数（≥1，步数越大进度越平滑）。
        simulate_failure: 为 ``True`` 时在「情感推理」阶段抛错，用于验证 `failed` / retry。
    """

    crawl_seconds: float = DEFAULT_CRAWL_SECONDS
    clean_seconds: float = DEFAULT_CLEAN_SECONDS
    sentiment_seconds: float = DEFAULT_SENTIMENT_SECONDS
    stats_seconds: float = DEFAULT_STATS_SECONDS
    steps_per_phase: int = DEFAULT_STEPS_PER_PHASE
    simulate_failure: bool = False

    def __post_init__(self) -> None:
        for name in ("crawl_seconds", "clean_seconds", "sentiment_seconds", "stats_seconds"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} 不能为负数（阶段时长必须可配且非负）")
        if self.steps_per_phase < 1:
            raise ValueError("steps_per_phase 必须 ≥ 1")

    @property
    def total_seconds(self) -> float:
        """四段耗时合计（默认 30 秒）。"""
        return (
            self.crawl_seconds
            + self.clean_seconds
            + self.sentiment_seconds
            + self.stats_seconds
        )

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """按环境变量构造配置（未设置的项用默认值）。"""
        return cls(
            crawl_seconds=_env_float(ENV_CRAWL_SECONDS, DEFAULT_CRAWL_SECONDS),
            clean_seconds=_env_float(ENV_CLEAN_SECONDS, DEFAULT_CLEAN_SECONDS),
            sentiment_seconds=_env_float(ENV_SENTIMENT_SECONDS, DEFAULT_SENTIMENT_SECONDS),
            stats_seconds=_env_float(ENV_STATS_SECONDS, DEFAULT_STATS_SECONDS),
            steps_per_phase=_env_int(ENV_STEPS_PER_PHASE, DEFAULT_STEPS_PER_PHASE),
            simulate_failure=_env_bool(ENV_SIMULATE_FAILURE, False),
        )
