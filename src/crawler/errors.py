"""爬虫模块的异常层次。

设计原则：**可恢复的停止**与**不可恢复的配置错误**分开。所有「采集应该安全停止、
已抓到的数据必须保留」的情况都继承 :class:`CrawlStopped`；CLI 捕获它后打印原因并
以非 0 退出码结束，但不会删除已经落盘的 CSV 与断点文件。

异常与 HTTP/业务码的对应关系见 :mod:`src.crawler.bilibili`：

=====================================  ==========================================
异常                                    典型来源
=====================================  ==========================================
:class:`RobotsDenied`                   robots.txt 明确禁止抓取
:class:`HttpStatusError`                4xx（非风控）等不可重试的 HTTP 错误
:class:`RiskControlError`               HTTP 412 / 429 / 业务码 -352 / -509 重试耗尽
:class:`ApiError`                       业务码非 0 且不可重试（视频不存在、评论关闭等）
:class:`RetryExhausted`                 网络超时 / 5xx 重试次数耗尽
:class:`CommentClosed`                  评论区关闭或不可见（属于「正常结束」而非失败）
:class:`ConfigError`                    参数非法（延迟为负、输出列不兼容等）
=====================================  ==========================================
"""

from __future__ import annotations


class CrawlerError(Exception):
    """爬虫模块所有异常的基类。"""


class CrawlStopped(CrawlerError):
    """采集应安全停止：原因可解释，已落盘的数据与断点保留。"""


class ConfigError(CrawlStopped, ValueError):
    """配置非法（例如负延迟、输出 CSV 表头不兼容）。"""


class RobotsDenied(CrawlStopped):
    """目标 URL 被 robots.txt 禁止抓取。"""

    def __init__(self, url: str, detail: str = "") -> None:
        self.url = url
        self.detail = detail
        message = f"robots.txt 禁止抓取：{url}"
        if detail:
            message = f"{message}（{detail}）"
        super().__init__(message)


class HttpStatusError(CrawlStopped):
    """收到不可重试的 HTTP 状态码（4xx 等）。"""

    def __init__(self, status_code: int, url: str = "", detail: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.detail = detail
        message = f"HTTP {status_code}"
        if url:
            message = f"{message}：{url}"
        if detail:
            message = f"{message}（{detail}）"
        super().__init__(message)


class RiskControlError(CrawlStopped):
    """触发站点风控且退避重试后仍然失败。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


class RetryExhausted(CrawlStopped):
    """网络错误 / 5xx 重试次数耗尽。"""

    def __init__(self, message: str, *, attempts: int = 0, last_error: BaseException | None = None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(message)


class ApiError(CrawlStopped):
    """业务接口返回非 0 code，且明确不可重试。"""

    def __init__(self, code: int | None, message: str, *, url: str = "") -> None:
        self.code = code
        self.api_message = message
        self.url = url
        prefix = f"接口错误 {code}" if code is not None else "接口错误"
        super().__init__(f"{prefix}：{message}" + (f"（{url}）" if url else ""))


class CommentClosed(CrawlStopped):
    """评论区已关闭 / 视频不可见：按「正常结束」处理，保留已有数据。"""
