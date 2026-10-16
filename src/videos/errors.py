"""`/api/videos` 系列的业务异常（HTTP 层据此映射状态码）。

约定（`docs/API契约.md` 第一节「接口清单」的 1 号接口）：

- :class:`InvalidBvidError` → HTTP **400**，响应体 ``{"detail": "BV 号格式不正确，应形如 BV1xx411c7mD"}``
- :class:`VideoBusyError` → HTTP **409**，响应体 ``{"detail": "该视频已在分析队列中"}``
- :class:`VideoNotFoundError` → HTTP **404**，响应体 ``{"detail": "..."}``

异常文案在这里集中定义：契约里锁定的两句错误文案**只能改这一处**。
"""

from __future__ import annotations

#: 400 场景的固定文案（契约锁定，不得改写）
MESSAGE_INVALID_BVID = "BV 号格式不正确，应形如 BV1xx411c7mD"

#: 409 场景的固定文案（契约锁定，不得改写）
MESSAGE_BUSY = "该视频已在分析队列中"


class VideoError(Exception):
    """`/api/videos` 业务错误基类。"""


class InvalidBvidError(VideoError):
    """BV 号不符合 ``^BV[0-9A-Za-z]{10}$``（HTTP 400）。"""


class VideoBusyError(VideoError):
    """同一 BV 号已在排队 / 爬取 / 处理中（HTTP 409）。"""


class VideoNotFoundError(VideoError):
    """任务不存在（HTTP 404）。"""


class PipelineAborted(Exception):
    """后台流水线收到关停信号或被 retry / delete 打断（不是失败，不写 failed 状态）。"""


class PipelineFailure(RuntimeError):
    """后台流水线自身出错，任务应落到 ``failed`` 并带 ``error``。"""
