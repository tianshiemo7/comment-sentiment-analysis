"""`/api/videos` 各状态下的 `message` 文案（契约锁定，集中一处便于对齐前端）。

对应 `docs/API契约.md` 第一节的状态表：列表接口与详情接口都用这些文案，
`progress` 由前端自行拼接显示（如「正在爬取评论… 43%」）。
"""

from __future__ import annotations

#: queued
MESSAGE_QUEUED = "排队中…"

#: crawling
MESSAGE_CRAWLING = "正在爬取评论…"

#: processing
MESSAGE_PROCESSING = "正在清洗与情感分析…"

#: completed
MESSAGE_COMPLETED = "分析完成"

#: 状态 → 文案（failed 的文案带具体原因，由 `failure_message` 生成）
STATUS_MESSAGES: dict[str, str] = {
    "queued": MESSAGE_QUEUED,
    "crawling": MESSAGE_CRAWLING,
    "processing": MESSAGE_PROCESSING,
    "completed": MESSAGE_COMPLETED,
}


def message_for(status: str, error: str | None = None) -> str:
    """按状态取文案；``failed`` 时把失败原因带进 `message`。"""
    if status == "failed":
        return f"分析失败：{error}" if error else "分析失败"
    return STATUS_MESSAGES.get(status, MESSAGE_QUEUED)


def failure_message(reason: str) -> str:
    """`failed` 状态的 `message`。"""
    return f"分析失败：{reason}"
