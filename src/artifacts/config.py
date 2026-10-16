"""`/api/artifacts` 流水线的可配置参数（**阶段时长必须可配，测试不要真等**）。

与 `src/videos/config.py` 同一套做法：阶段时长是「推进进度条的节奏」，
真实工作（清洗 / 分词 / 逐条占位预测 / 聚合统计）在阶段第一步就已跑完，
因此默认值取得很小（合计约 1.2 秒），测试直接把时长设成 0 即可瞬时跑完状态机。

环境变量（全部可选；非法值回退默认值，绝不因配置写错而启动失败）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `ARTIFACT_CLEAN_SECONDS` | `0.4` | 「文本清洗」阶段节奏耗时（秒） |
| `ARTIFACT_SENTIMENT_SECONDS` | `0.5` | 「情感推理」阶段节奏耗时（秒） |
| `ARTIFACT_STATS_SECONDS` | `0.3` | 「聚合统计」阶段节奏耗时（秒） |
| `ARTIFACT_STEPS_PER_STAGE` | `4` | 每个阶段拆成几步推进 `progress` |
| `ARTIFACTS_DIR` | `data/uploads` | 项目与单独评论的落盘根目录（见 `store.py`） |
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_CLEAN_SECONDS = "ARTIFACT_CLEAN_SECONDS"
ENV_SENTIMENT_SECONDS = "ARTIFACT_SENTIMENT_SECONDS"
ENV_STATS_SECONDS = "ARTIFACT_STATS_SECONDS"
ENV_STEPS_PER_STAGE = "ARTIFACT_STEPS_PER_STAGE"
ENV_SIMULATE_FAILURE = "ARTIFACT_SIMULATE_FAILURE"

#: 默认阶段节奏耗时（秒）：合计约 1.2 秒，够前端把进度条走顺
DEFAULT_CLEAN_SECONDS = 0.4
DEFAULT_SENTIMENT_SECONDS = 0.5
DEFAULT_STATS_SECONDS = 0.3
DEFAULT_STEPS_PER_STAGE = 4


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量；缺失或非法时回退默认值。"""
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
class ArtifactsConfig:
    """流水线时序配置（不可变；测试直接用构造参数覆盖，无需改环境变量）。

    Attributes:
        clean_seconds: 「文本清洗」阶段节奏耗时。
        sentiment_seconds: 「情感推理」阶段节奏耗时。
        stats_seconds: 「聚合统计」阶段节奏耗时。
        steps_per_stage: 每阶段推进 `progress` 的步数（≥1）。
        simulate_failure: 为 ``True`` 时在「情感推理」阶段主动失败（验证 `failed` 分支）。
    """

    clean_seconds: float = DEFAULT_CLEAN_SECONDS
    sentiment_seconds: float = DEFAULT_SENTIMENT_SECONDS
    stats_seconds: float = DEFAULT_STATS_SECONDS
    steps_per_stage: int = DEFAULT_STEPS_PER_STAGE
    simulate_failure: bool = False

    def __post_init__(self) -> None:
        for name in ("clean_seconds", "sentiment_seconds", "stats_seconds"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负数（阶段时长必须可配且非负）")
        if self.steps_per_stage < 1:
            raise ValueError("steps_per_stage 必须 ≥ 1")

    @property
    def total_seconds(self) -> float:
        """三段节奏耗时合计（默认约 1.2 秒）。"""
        return self.clean_seconds + self.sentiment_seconds + self.stats_seconds

    @classmethod
    def from_env(cls) -> "ArtifactsConfig":
        """按环境变量构造配置（未设置的项用默认值）。"""
        return cls(
            clean_seconds=_env_float(ENV_CLEAN_SECONDS, DEFAULT_CLEAN_SECONDS),
            sentiment_seconds=_env_float(ENV_SENTIMENT_SECONDS, DEFAULT_SENTIMENT_SECONDS),
            stats_seconds=_env_float(ENV_STATS_SECONDS, DEFAULT_STATS_SECONDS),
            steps_per_stage=_env_int(ENV_STEPS_PER_STAGE, DEFAULT_STEPS_PER_STAGE),
            simulate_failure=_env_bool(ENV_SIMULATE_FAILURE, False),
        )
