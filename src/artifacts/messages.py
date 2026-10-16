"""项目状态的中文文案（`project.message` 字段的唯一来源）。

与 `src/videos/messages.py` 一致：文案集中一处，避免散落在流水线各步里。
"""

from __future__ import annotations

MESSAGE_QUEUED = "已排队，等待处理"
MESSAGE_CLEANING = "正在清洗与分词（复用 src/preprocessing 的 clean 模式）"
MESSAGE_SENTIMENT = "正在做占位情感分析（规则 / 词典，不是训练模型）"
MESSAGE_STATS = "正在聚合统计（占比 / 词频 / 长度分布）"
MESSAGE_COMPLETED = "处理完成"


def failure_message(reason: str) -> str:
    """失败状态的中文文案（把原始原因带上，便于前端与排查）。"""
    return f"处理失败：{reason}"


def message_for(status: str, error: str | None = None) -> str:
    """按状态返回兜底文案（记录里已有 `message` 时以记录为准）。"""
    if status == "completed":
        return MESSAGE_COMPLETED
    if status == "failed":
        return failure_message(error or "未知原因")
    if status == "processing":
        return MESSAGE_CLEANING
    return MESSAGE_QUEUED
