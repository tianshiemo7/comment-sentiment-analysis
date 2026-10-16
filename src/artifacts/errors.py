"""`/api/artifacts` 与 `/api/solo-comments` 的业务异常。

分层约定：**业务层抛本文件的异常，HTTP 层（`src/inference/app.py`）负责映射状态码**，
业务层不 import FastAPI，保持与 Web 框架解耦（与 `src/videos/errors.py` 一致）。

| 异常 | HTTP |
| --- | --- |
| :class:`ArtifactNotFoundError` | 404 |
| :class:`ArtifactValidationError` | 400 |
| :class:`ArtifactTooLargeError` | 413 |
| :class:`OpenUnsupportedError` | 400（平台不支持在本机打开文件） |
| :class:`PipelineAborted` / :class:`PipelineFailure` | 不对外，仅后台线程内部使用 |
"""

from __future__ import annotations


class ArtifactError(Exception):
    """本包所有业务异常的基类。"""


class ArtifactNotFoundError(ArtifactError):
    """项目 / 单条评论不存在，或 id 形态非法（一律按不存在处理，避免探测）。"""


class ArtifactValidationError(ArtifactError):
    """请求内容不合法：格式不支持、阶段枚举非法、字段校验失败等（HTTP 400）。"""


class ArtifactTooLargeError(ArtifactError):
    """上传文件超过大小上限（HTTP 413）。"""


class OpenUnsupportedError(ArtifactError):
    """当前平台没有可用的本机打开方式（HTTP 400）。"""


class PipelineAborted(ArtifactError):
    """流水线被中断：项目被删除、被重新分析（代次变化）或服务关停。

    由 service 层把项目恢复成 `queued`，下次启动 / 重新分析时续跑。
    """


class PipelineFailure(ArtifactError):
    """流水线正常地失败（状态与 `error` 已写入 `project.json`）。"""
