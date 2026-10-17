"""HTTP 传输层：会话复用、统一重试、限速、请求指标。

只做「传输」这一层的事，不关心业务接口返回的 JSON 结构：

- 连接池复用（``requests.Session`` + ``HTTPAdapter``），减少 TLS 握手；
- 统一超时；
- 网络异常 / 5xx / 429 自动重试，指数退避 + 抖动，429 尊重 ``Retry-After``；
- 412 / 401 / 403 等「业务上需要特殊处理」的状态码**原样返回**，交给调用方
  （B 站风控 412 需要刷新 WBI / buvid 后再试，不能在传输层盲目重试）；
- 记录每次尝试的耗时、状态码、响应字节数与重试次数，供效率报告使用。

所有请求都会先经过 :class:`~src.crawler.rate_limit.RateLimiter`，包括重试。
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from .errors import CrawlStopped, HttpStatusError, RetryExhausted
from .rate_limit import RateLimiter, Sleeper, backoff_delay, parse_retry_after

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "comment-sentiment-analysis-course/0.1 "
    "(+https://github.com/tianshiemo7/comment-sentiment-analysis)"
)

#: 默认请求头：声明项目身份，不伪装浏览器；站点可通过 UA 识别并联系我们。
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

#: 传输层默认自动重试的状态码（429 会额外尊重 Retry-After）
DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)


def build_session(
    user_agent: str = DEFAULT_USER_AGENT,
    *,
    pool_size: int = 8,
    headers: Optional[Mapping[str, str]] = None,
) -> "requests.Session":
    """创建复用了连接池的 requests 会话（延迟导入 requests，便于无依赖环境导入包）。"""

    import requests
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=max(1, pool_size),
        pool_maxsize=max(1, pool_size),
        max_retries=0,  # 重试逻辑由本模块统一控制，避免与 urllib3 叠加
        pool_block=False,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    session.headers.update({"User-Agent": user_agent})
    if headers:
        session.headers.update(dict(headers))
    return session


@dataclass(frozen=True)
class RetryPolicy:
    """重试策略。"""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.25
    retry_statuses: tuple[int, ...] = DEFAULT_RETRY_STATUSES

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")
        if self.base_delay <= 0 or self.max_delay <= 0:
            raise ValueError("base_delay / max_delay 必须为正数")


@dataclass
class RequestMetrics:
    """一次采集任务的请求指标（可序列化为 JSON 写入效率报告）。"""

    attempts: int = 0
    logical_requests: int = 0
    retries: int = 0
    response_bytes: int = 0
    statuses: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    latencies: list[float] = field(default_factory=list)

    def record(
        self,
        *,
        status: int | None,
        latency: float,
        size: int = 0,
        error: str | None = None,
    ) -> None:
        self.attempts += 1
        self.latencies.append(float(latency))
        if size:
            self.response_bytes += int(size)
        if status is not None:
            self.statuses[str(status)] += 1
        if error:
            self.errors[error] += 1

    def summary(self) -> dict[str, Any]:
        """返回可直接进报告 / JSON 的统计（耗时单位毫秒）。"""

        latencies = sorted(self.latencies)
        count = len(latencies)

        def percentile(ratio: float) -> float:
            if not count:
                return 0.0
            index = min(count - 1, max(0, int(round(ratio * (count - 1)))))
            return round(latencies[index] * 1000, 2)

        return {
            "attempts": self.attempts,
            "requests": self.logical_requests,
            "retries": self.retries,
            "retry_rate": round(self.retries / self.logical_requests, 4)
            if self.logical_requests
            else 0.0,
            "response_bytes": self.response_bytes,
            "latency_ms": {
                "avg": round(statistics.mean(latencies) * 1000, 2) if latencies else 0.0,
                "p50": percentile(0.50),
                "p95": percentile(0.95),
                "max": round(latencies[-1] * 1000, 2) if latencies else 0.0,
            },
            "status_counts": dict(sorted(self.statuses.items())),
            "error_counts": dict(sorted(self.errors.items())),
        }


def request_with_retry(
    session: Any,
    method: str,
    url: str,
    *,
    limiter: RateLimiter,
    policy: RetryPolicy,
    metrics: RequestMetrics,
    timeout: float,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    passthrough_statuses: Iterable[int] = (),
    sleep: Sleeper = time.sleep,
    clock=time.monotonic,
) -> Any:
    """发送请求并在传输层做有限次重试，返回 ``requests.Response``。

    Args:
        session: ``requests.Session`` 或实现了 ``request(method, url, ...)`` 的对象。
        passthrough_statuses: 这些状态码不抛异常也不重试，原样返回给调用方
            （B 站用 412 表示风控、403 表示 WAF 拦截，需要刷新密钥后重试）。
        sleep / clock: 可注入，单元测试无需真实等待。

    Raises:
        RetryExhausted: 网络错误或 5xx / 429 重试次数耗尽。
        HttpStatusError: 其余 4xx 等不可重试状态码。
    """

    passthrough = set(passthrough_statuses)
    last_error: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        limiter.wait()
        started = clock()
        try:
            response = session.request(
                method,
                url,
                timeout=timeout,
                headers=dict(headers) if headers else None,
                params=dict(params) if params else None,
            )
        except Exception as exc:  # requests 异常类型在延迟导入场景下用基类兜底
            latency = clock() - started
            error_name = type(exc).__name__
            # 只有网络类错误才重试；URL 非法等编程错误直接失败。
            if not _is_retryable_exception(exc):
                metrics.logical_requests += 1
                metrics.record(status=None, latency=latency, error=error_name)
                raise CrawlStopped(f"请求失败：{error_name}：{exc}") from exc
            metrics.logical_requests += 1
            metrics.record(status=None, latency=latency, error=error_name)
            last_error = exc
            if attempt >= policy.max_attempts:
                break
            delay = backoff_delay(
                attempt,
                base=policy.base_delay,
                cap=policy.max_delay,
                jitter=policy.jitter,
            )
            metrics.retries += 1
            logger.warning(
                "网络异常 %s（第 %d/%d 次），%.2fs 后重试：%s",
                error_name, attempt, policy.max_attempts, delay, exc,
            )
            sleep(delay)
            continue

        latency = clock() - started
        size = _response_size(response)
        status = int(getattr(response, "status_code", 0) or 0)
        metrics.logical_requests += 1
        metrics.record(status=status, latency=latency, size=size)

        if status in passthrough:
            return response

        if status in policy.retry_statuses:
            if attempt >= policy.max_attempts:
                raise RetryExhausted(
                    f"HTTP {status} 重试 {policy.max_attempts} 次后仍失败：{url}",
                    attempts=policy.max_attempts,
                )
            retry_after = parse_retry_after(_header(response, "Retry-After"))
            delay = backoff_delay(
                attempt,
                base=policy.base_delay,
                cap=policy.max_delay,
                jitter=policy.jitter,
            )
            if retry_after is not None:
                delay = max(delay, retry_after)
            metrics.retries += 1
            logger.warning(
                "HTTP %d（第 %d/%d 次），%.2fs 后重试 %s",
                status, attempt, policy.max_attempts, delay, url,
            )
            sleep(delay)
            continue

        if status >= 400:
            raise HttpStatusError(status, url, _body_snippet(response))

        return response

    message = f"请求重试 {policy.max_attempts} 次后仍失败：{url}"
    if last_error is not None:
        message = f"{message}（最后错误：{type(last_error).__name__}: {last_error}）"
    raise RetryExhausted(message, attempts=policy.max_attempts, last_error=last_error)


def _is_retryable_exception(exc: BaseException) -> bool:
    """网络类异常才重试；InvalidURL / MissingSchema 等配置错误直接失败。"""

    try:
        import requests

        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.RequestException):
            # ChunkedEncodingError / ContentDecodingError 等通常也与网络有关
            return not isinstance(exc, (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema))
    except Exception:  # pragma: no cover - requests 理论上一定可用
        pass
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _response_size(response: Any) -> int:
    content = getattr(response, "content", b"")
    try:
        return len(content or b"")
    except TypeError:  # pragma: no cover - 防御式
        return 0


def _header(response: Any, name: str) -> Optional[str]:
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return headers.get(name)
    except AttributeError:  # pragma: no cover
        return None


def _body_snippet(response: Any, limit: int = 200) -> str:
    content = getattr(response, "content", b"") or b""
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover
        return ""
    text = " ".join(text.split())
    return text[:limit]
