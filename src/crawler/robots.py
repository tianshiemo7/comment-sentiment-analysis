"""robots.txt 校验（带主机级缓存）。

课程要求「采集前检查目标站点 robots.txt」。本模块把这件事做成可复用、可测试的
组件，而不是在每个爬虫里重复写一遍：

- 按 **scheme + host** 缓存 ``RobotFileParser``，同一站点只拉取一次 robots.txt；
- ``404 / 410`` 视为没有限制（允许）；
- ``401 / 403`` 视为整站禁止；
- 网络异常默认 **fail-closed（拒绝）**，避免「拿不到规则就默认允许」的合规风险；
  可通过 ``fail_closed=False`` 显式放宽（仅供本地测试使用）。

注意：``robots.txt`` 的 ``Allow`` / ``Disallow`` 是按 **URL 路径** 匹配的，
:meth:`RobotsChecker.check` 会返回完整判定结果（包含 robots 地址与原因），便于
写入采集报告与来源说明。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RobotsDecision:
    """一次 robots 判定结果。"""

    url: str
    allowed: bool
    robots_url: str = ""
    status: int | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "allowed": self.allowed,
            "robots_url": self.robots_url,
            "status": self.status,
            "reason": self.reason,
        }


class RobotsChecker:
    """带缓存与失败策略的 robots 检查器。

    Args:
        user_agent: 用于匹配 robots 规则与请求 robots.txt 的 UA。
        session: 可注入的会话（测试用）；默认延迟创建 ``requests.Session``。
        timeout: 拉取 robots.txt 的超时秒数。
        ttl: 缓存有效期（秒），默认 1 小时。
        fail_closed: 拉取失败时是否按「禁止」处理（默认是）。
        clock: 可注入的单调时钟。
    """

    def __init__(
        self,
        user_agent: str,
        *,
        session: Any = None,
        timeout: float = 5.0,
        ttl: float = 3600.0,
        fail_closed: bool = True,
        clock=time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self._session = session
        self.timeout = timeout
        self.ttl = ttl
        self.fail_closed = fail_closed
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[RobotFileParser, float]] = {}
        self.checks = 0
        self.denied = 0
        self.fetches = 0

    # ------------------------------------------------------------------ public
    def check(self, url: str) -> RobotsDecision:
        """返回 ``url`` 是否允许抓取。"""

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return RobotsDecision(url=url, allowed=False, reason="URL 非法，无法校验 robots.txt")

        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        parser, status, reason = self._load(parsed.scheme, parsed.netloc, robots_url)
        self.checks += 1
        allowed = bool(parser.can_fetch(self.user_agent, url))
        if not allowed:
            self.denied += 1
        return RobotsDecision(
            url=url,
            allowed=allowed,
            robots_url=robots_url,
            status=status,
            reason=reason if not allowed else "",
        )

    def is_allowed(self, url: str) -> bool:
        """``check`` 的布尔简写。"""

        return self.check(url).allowed

    def clear_cache(self) -> None:
        self._cache.clear()

    # ----------------------------------------------------------------- private
    def _load(
        self, scheme: str, netloc: str, robots_url: str
    ) -> tuple[RobotFileParser, int | None, str]:
        key = (scheme, netloc)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and now < cached[1]:
            parser, _ = cached
            return parser, None, ""

        parser = RobotFileParser()
        parser.set_url(robots_url)
        self.fetches += 1
        status, text = self._fetch(robots_url)

        if status in (404, 410):
            # 没有 robots.txt：按惯例视为无限制。
            parser.parse([])
            reason = "robots.txt 不存在（404/410），按允许处理"
        elif status in (401, 403):
            parser.parse(["User-agent: *", "Disallow: /"])
            reason = f"robots.txt 返回 {status}，按整站禁止处理"
        elif status is None:
            if self.fail_closed:
                parser.parse(["User-agent: *", "Disallow: /"])
                reason = "robots.txt 拉取失败，按禁止处理（fail-closed）"
            else:
                parser.parse([])
                reason = "robots.txt 拉取失败，fail_closed=False 按允许处理"
        else:
            parser.parse(text.splitlines())
            reason = ""
        self._cache[key] = (parser, now + self.ttl)
        return parser, status, reason

    def _fetch(self, robots_url: str) -> tuple[int | None, str]:
        session = self._session
        if session is None:
            try:
                from .transport import build_session

                session = build_session(self.user_agent)
                self._session = session
            except Exception as exc:  # pragma: no cover - requests 缺失
                logger.warning("无法创建会话拉取 robots.txt：%s", exc)
                return None, ""
        try:
            response = session.get(robots_url, timeout=self.timeout)
        except Exception as exc:  # 网络异常：交给 fail_closed 决定
            logger.warning("拉取 robots.txt 失败：%s（%s）", robots_url, exc)
            return None, ""
        status = int(getattr(response, "status_code", 0) or 0)
        text = ""
        try:
            text = response.text
        except Exception:  # pragma: no cover - 防御式
            try:
                text = (response.content or b"").decode("utf-8", errors="replace")
            except Exception:
                text = ""
        return status, text
