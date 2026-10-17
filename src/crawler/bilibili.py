"""B 站视频评论采集（接口版，含反爬稳健性、标准化、断点续爬与来源登记）。

原实现（本文件旧版）已经能跑通 WBI 签名 + 分页 + 去重，但存在以下问题，本版逐项修复：

====================================  ==========================================
原问题                                 本版改进
====================================  ==========================================
固定 ``sleep``、无抖动                  令牌式最小间隔 + 30% 抖动 + 风控后自适应降速
412 / -352 / -509 直接终止             识别风控 → 刷新 WBI 密钥（可选刷新 buvid）
                                       → 指数退避重试；重试耗尽才安全停止并保留数据
429 忽略 ``Retry-After``                传输层尊重 ``Retry-After``，再做指数退避
WBI 密钥只在进程内取一次                带 TTL 缓存；风控 / 签名失败时强制刷新
无设备指纹，匿名请求易触发 -352          支持 buvid3/buvid4（``BILI_INIT_BUVID=1`` 或
                                       ``--init-buvid`` 开启）。**默认关闭**：实测
                                       携带 buvid3 时匿名账号只能看到 3 条评论
                                       （见 README「实测效率」），与采集目标冲突
评论原文直接落盘                        标准化：HTML 实体、NFKC、零宽字符、控制
                                       字符、空白；纯符号/空文本过滤
无 robots 校验                          采集前校验视频页 robots；API 主机单独判定
                                       并如实记录（``Disallow: /`` 时默认告警）
无来源登记                              自动写入 ``data/raw/sources.json`` 与
                                       ``data/raw/来源说明.md``
不可被其它模块复用                     提供 ``BiliClient`` / ``crawl_video`` /
                                       ``crawl_bilibili`` / ``fetch_video_comments``
                                       等可导入 API，CLI 只是薄封装
无效率指标                             统计请求数、重试、延迟分位、字节数、
                                       评论吞吐与风控恢复次数，供 README 实测表
====================================  ==========================================

合规约定：本模块不内置任何 Cookie/账号凭据；如需登录态必须由使用者通过环境变量
``BILI_SESSDATA`` 显式提供。默认请求间隔 2.5 秒、单进程串行、不抓取登录后受限内容。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlencode, urlparse

from . import sources
from .errors import (
    ApiError,
    CommentClosed,
    ConfigError,
    CrawlStopped,
    RiskControlError,
    RobotsDenied,
)
from .normalize import normalize_comment, normalize_oneline, to_int, utc_iso
from .rate_limit import RateLimiter
from .robots import RobotsChecker
from .sources import SourceRecord
from .transport import (
    DEFAULT_USER_AGENT,
    RequestMetrics,
    RetryPolicy,
    build_session,
    request_with_retry,
)

logger = logging.getLogger("bili_crawler")

API = "https://api.bilibili.com"
WEB = "https://www.bilibili.com"

ROOT = Path(__file__).resolve().parents[2]
SAVE_DIR = ROOT / "data" / "raw"

DEFAULT_DELAY_SECONDS = float(os.getenv("CRAWL_DELAY_SECONDS", "2.5"))
DEFAULT_TIMEOUT = float(os.getenv("CRAWL_TIMEOUT", "15"))

#: 楼中楼每页条数（对应接口 ``ps`` 参数）。显式传递，避免依赖服务端默认值；
#: 结束判断同样以它（或服务端返回的 ``page.size``）为基准，不再写死 20。
SUB_COMMENT_PAGE_SIZE = 20

#: 断点 ``sub_done`` 的最大长度：纯防御性上限，超限丢弃最早的根评论记录并告警，
#: 避免异常情况下 state 文件无限膨胀（正常采集每页完成后会清空，远达不到此上限）。
MAX_SUB_DONE_ENTRIES = 5000

#: 大规模采集的硬上限（仅防死循环/误配置；0 表示「不限」时使用它们兜底）。
MAX_PAGES_HARD_LIMIT = 100_000
MAX_SUB_PAGES_HARD_LIMIT = 1_000

#: 单视频默认页数（保守默认；大规模采集显式传 ``--max-pages 0`` 表示不限）
DEFAULT_MAX_PAGES = 5
DEFAULT_MAX_SUB_PAGES = 3

#: WBI 签名混淆表（官方前端固定表，非本项目可配置项）
MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
)

#: 原始 CSV 列（保持稳定，下游 ``src/preprocessing`` 按这些列名做映射）
COLUMNS = [
    "bvid",
    "aid",
    "video_title",
    "rpid",
    "level",
    "root_rpid",
    "parent_rpid",
    "text",
    "likes",
    "reply_count",
    "ctime_utc",
    "collected_at_utc",
]

BVID_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])")

#: 风控类业务码：应退避、刷新密钥后重试，而不是直接终止
RISK_CODES = {
    -352: "风控校验失败",
    -509: "请求过于频繁",
    -799: "请求过于频繁",
}
#: 不可重试的业务码（视频不存在 / 评论关闭等）
FATAL_CODES = {
    -101: "未登录或凭据失效",
    -400: "请求参数错误",
    -403: "访问权限不足",
    -404: "视频不存在",
    12002: "评论区已关闭",
    62002: "视频不可见",
    62004: "视频审核中",
}

#: ``Retry-After`` 之外的风控冷却时间（秒），按重试次数递增
DEFAULT_RISK_COOLDOWN = 8.0


# =========================================================================
# 基础工具
# =========================================================================

def parse_bvid(value: str) -> str:
    """解析 BV 号，支持直接输入视频 URL。"""

    value = (value or "").strip()
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if parsed.hostname not in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
            raise ValueError("不是有效的 B站视频网址")
        value = parsed.path
    match = BVID_RE.search(value)
    if not match:
        raise ValueError(f"无法识别 BV 号：{value}")
    return match.group()


def validate_page_limits(max_pages: int, max_sub_pages: int) -> tuple[int, int]:
    """校验页数配置：``0`` 表示不限（内部仍用硬上限防死循环）。"""

    try:
        pages = int(max_pages)
        sub_pages = int(max_sub_pages)
    except (TypeError, ValueError) as exc:
        raise ConfigError("max_pages / max_sub_pages 必须是整数") from exc
    if pages < 0 or pages > MAX_PAGES_HARD_LIMIT:
        raise ConfigError(f"max_pages 必须在 0~{MAX_PAGES_HARD_LIMIT} 之间（0=不限）")
    if sub_pages < 0 or sub_pages > MAX_SUB_PAGES_HARD_LIMIT:
        raise ConfigError(
            f"max_sub_pages 必须在 0~{MAX_SUB_PAGES_HARD_LIMIT} 之间（0=不限）"
        )
    return pages, sub_pages


def read_bvid_list(path: str | Path) -> list[str]:
    """从文本文件读取 BV 清单（一行一个，支持 ``#`` 注释与空行）。

    行内可以包含 URL 或 ``BV...,备注``，解析交给 :func:`parse_bvid`；
    去重与非法项记录由 :func:`crawl_bilibili` 统一处理。
    """

    target = Path(path)
    if not target.exists():
        raise ConfigError(f"BV 列表文件不存在：{target}")
    try:
        text = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"读取 BV 列表失败：{target}（{exc}）") from exc
    values: list[str] = []
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        values.append(value)
    return values


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """由 ``img_key + sub_key`` 生成本次会话的 WBI 混合密钥。"""

    raw = img_key + sub_key
    if len(raw) != 64:
        raise CrawlStopped("WBI 密钥格式异常")
    return "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi(
    params: Mapping[str, Any],
    mixin_key: str,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    """对请求参数做 WBI 签名，返回包含 ``wts`` 与 ``w_rid`` 的新字典。"""

    cleaned = {
        str(key): "".join(char for char in str(value) if char not in "!'()*")
        for key, value in params.items()
    }
    cleaned["wts"] = str(int(time.time()) if timestamp is None else int(timestamp))
    ordered = dict(sorted(cleaned.items()))
    query = urlencode(ordered, quote_via=quote, safe="")
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    ordered["w_rid"] = w_rid
    return ordered


# =========================================================================
# B 站 API 客户端
# =========================================================================

class BiliClient:
    """B 站评论接口客户端。

    Args:
        delay: 两次请求的最小间隔（秒）。CLI 会强制 ≥ 2，直接调用时可传 0 做
            本地 mock 基准。
        timeout: 单次请求超时。
        max_attempts: 网络 / 5xx 重试次数。
        user_agent: 请求 UA，默认项目标识 UA（可通过环境变量覆盖）。
        api_base: API 根地址；测试/基准可指向本地 mock 服务。
        web_base: 网页根地址（仅用于 robots 校验）。
        session: 可注入的会话（测试用）。
        respect_robots: 是否校验 robots.txt（默认开启）。
        api_robots_policy: ``api.bilibili.com`` 的 robots 处理策略：
            ``"warn"``（默认，记录并告警后继续）、``"enforce"``（禁止则拒绝）、
            ``"ignore"``（不检查）。
        risk_control_retries: 风控触发的额外重试次数。
        risk_control_cooldown: 首次风控冷却秒数，后续按次数递增。
        wbi_ttl: WBI 密钥缓存有效期（秒）。
        clock / sleep / rng / time_fn: 可注入，便于离线基准与单元测试。
    """

    def __init__(
        self,
        *,
        delay: float = DEFAULT_DELAY_SECONDS,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = 3,
        user_agent: str | None = None,
        api_base: str = API,
        web_base: str = WEB,
        session: Any = None,
        robots_session: Any = None,
        respect_robots: bool = True,
        api_robots_policy: str | None = None,
        risk_control_retries: int = 2,
        risk_control_cooldown: float = DEFAULT_RISK_COOLDOWN,
        wbi_ttl: float = 6 * 3600,
        init_buvid: bool | None = None,
        jitter_ratio: float = 0.3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 20.0,
        clock=time.monotonic,
        sleep=time.sleep,
        rng: random.Random | None = None,
        time_fn=time.time,
    ) -> None:
        if delay < 0:
            raise ConfigError("delay 不能为负数（限速是硬性要求）")
        if timeout <= 0:
            raise ConfigError("timeout 必须为正数")
        if api_robots_policy is None:
            api_robots_policy = os.getenv("BILI_API_ROBOTS_POLICY", "warn").strip().lower()
        if api_robots_policy not in {"warn", "enforce", "ignore"}:
            raise ConfigError("api_robots_policy 只能是 warn / enforce / ignore")
        if init_buvid is None:
            init_buvid = os.getenv("BILI_INIT_BUVID", "").strip().lower() in {"1", "true", "yes", "on"}

        self.api_base = api_base.rstrip("/")
        self.web_base = web_base.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.respect_robots = respect_robots
        self.api_robots_policy = api_robots_policy
        self.init_buvid = bool(init_buvid)
        self.risk_control_retries = max(0, int(risk_control_retries))
        self.risk_control_cooldown = max(0.0, float(risk_control_cooldown))
        self.wbi_ttl = max(0.0, float(wbi_ttl))

        self._clock = clock
        self._sleep = sleep
        self._time_fn = time_fn
        self._rng = rng or random.Random()
        self._session = session or build_session(self.user_agent)
        self._session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Referer": f"{self.web_base}/",
                "Origin": self.web_base,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )
        self._limiter = RateLimiter(
            delay,
            jitter=jitter_ratio,
            clock=clock,
            sleep=sleep,
            rng=random.Random(self._rng.random()),
        )
        self._robots_session = (
            robots_session
            or (build_session(self.user_agent) if session is None else session)
        )
        self._robots = RobotsChecker(self.user_agent, session=self._robots_session)
        self._policy = RetryPolicy(
            max_attempts=max_attempts,
            base_delay=retry_base_delay,
            max_delay=retry_max_delay,
            jitter=jitter_ratio,
        )
        self.metrics = RequestMetrics()
        self.risk_control_hits = 0
        self.wbi_refreshes = 0
        self.robots_checks: list[dict[str, Any]] = []
        self.api_robots_decision: dict[str, Any] | None = None
        self._wbi_key: str | None = None
        self._wbi_fetched_at: float = 0.0
        self._buvid_ready = False
        self._refreshing = False
        self._login_cache: dict[str, Any] | None = None
        self._credential_present = False

        self._load_env_cookies()

    # ------------------------------------------------------------------ setup
    def _load_env_cookies(self) -> None:
        """仅从环境变量读取凭据，绝不把 Cookie 写进源码。

        支持两种方式（后者优先）：
        - 单个变量：``BILI_SESSDATA`` / ``BILI_BILI_JCT`` / ``BILI_DEDEUSERID`` / ``BILI_BUVID3``；
        - 完整 Cookie 串：``BILI_COOKIE="SESSDATA=...; bili_jct=...; DedeUserID=..."``。
        """

        cookies = {
            "SESSDATA": os.getenv("BILI_SESSDATA", "").strip(),
            "bili_jct": os.getenv("BILI_BILI_JCT", "").strip(),
            "DedeUserID": os.getenv("BILI_DEDEUSERID", "").strip(),
            "buvid3": os.getenv("BILI_BUVID3", "").strip(),
        }
        cookie_header = os.getenv("BILI_COOKIE", "").strip()
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if name and value:
                cookies[name] = value
        for name, value in cookies.items():
            if not value:
                continue
            try:
                self._session.cookies.set(name, value, domain=".bilibili.com")
            except Exception:  # pragma: no cover - 注入的假会话可能不支持
                pass
        if cookies["buvid3"]:
            self._buvid_ready = True
        self._credential_present = bool(cookies.get("SESSDATA") or cookie_header)

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if callable(close):
            close()
        if self._robots_session is not self._session:
            robots_close = getattr(self._robots_session, "close", None)
            if callable(robots_close):
                robots_close()

    # ------------------------------------------------------------------ public
    @property
    def has_credentials(self) -> bool:
        """是否配置了登录态（``SESSDATA`` 或完整 ``BILI_COOKIE``）。"""

        return self._credential_present

    def login_status(self, *, force: bool = False) -> dict[str, Any]:
        """查询当前登录态（``/x/web-interface/nav``）。

        匿名时接口返回 ``code=-101``，这里按「未登录」正常返回而不是抛错，
        便于大规模采集前做一次快速检查（``--check-login`` / ``--require-login``）。
        """

        if self._login_cache is not None and not force:
            return dict(self._login_cache)
        result = self._api("/x/web-interface/nav", allow_nav=True)
        data = result.get("data") or {}
        status = {
            "is_login": bool(data.get("isLogin")),
            "uname": str(data.get("uname") or ""),
            "mid": to_int(data.get("mid")),
            "has_credentials": self._credential_present,
        }
        self._login_cache = status
        return dict(status)

    def check_content_page_robots(self, bvid: str) -> dict[str, Any] | None:
        """校验视频网页的 robots.txt 判定（API 作为该页面数据通道另行判定）。"""

        if not self.respect_robots:
            return None
        url = f"{self.web_base}/video/{bvid}"
        decision = self._robots.check(url)
        self.robots_checks.append(decision.as_dict())
        if not decision.allowed:
            raise RobotsDenied(url, decision.reason or "视频页被 robots.txt 禁止")
        return decision.as_dict()

    def check_api_robots(self) -> dict[str, Any] | None:
        """判定 API 主机的 robots 规则，并如实记录（默认 warn，不静默忽略）。"""

        if not self.respect_robots or self.api_robots_policy == "ignore":
            return None
        if self.api_robots_decision is not None:
            return self.api_robots_decision
        url = f"{self.api_base}/x/web-interface/nav"
        decision = self._robots.check(url)
        self.api_robots_decision = decision.as_dict()
        self.robots_checks.append(decision.as_dict())
        if not decision.allowed:
            message = (
                f"api.bilibili.com/robots.txt 当前禁止通用抓取（{decision.reason or 'Disallow: /'}）。"
                "本项目把它视为视频页的数据通道，仅在课程/研究用途下匿名、低速、小规模访问；"
                "设置 BILI_API_ROBOTS_POLICY=enforce 可改为直接拒绝。"
            )
            if self.api_robots_policy == "enforce":
                raise RobotsDenied(url, "api.bilibili.com robots.txt Disallow: /（enforce 模式）")
            logger.warning(message)
        return self.api_robots_decision

    def get_video_meta(self, bvid: str) -> dict[str, Any]:
        """获取视频元信息（aid / 标题 / UP 主 / 评论总数等），供下游详情页适配。"""

        result = self._api("/x/web-interface/view", {"bvid": bvid})
        data = result.get("data") or {}
        aid = data.get("aid")
        if not aid:
            raise ApiError(None, "无法获取视频 aid", url="/x/web-interface/view")
        stat = data.get("stat") or {}
        owner = data.get("owner") or {}
        return {
            "bvid": bvid,
            "aid": int(aid),
            "title": normalize_oneline(data.get("title")),
            "owner": normalize_oneline(owner.get("name")),
            "cover_url": str(data.get("pic") or ""),
            "comment_count": to_int(stat.get("reply")),
            "view_count": to_int(stat.get("view")),
            "pubdate": utc_iso(data.get("pubdate")),
        }

    def get_video_info(self, bvid: str) -> tuple[int, str]:
        """兼容旧接口：返回 ``(aid, title)``。"""

        meta = self.get_video_meta(bvid)
        return int(meta["aid"]), str(meta["title"])

    def get_wbi_key(self, *, force: bool = False) -> str:
        """获取并缓存 WBI 混合密钥。"""

        now = self._clock()
        if (
            not force
            and self._wbi_key
            and (now - self._wbi_fetched_at) < self.wbi_ttl
        ):
            return self._wbi_key
        result = self._api("/x/web-interface/nav", allow_nav=True)
        data = result.get("data") or {}
        images = data.get("wbi_img") or {}
        img_url = str(images.get("img_url") or "")
        sub_url = str(images.get("sub_url") or "")
        if not img_url or not sub_url:
            raise CrawlStopped("无法获取 WBI 密钥，请检查访问权限")
        img_key = Path(urlparse(img_url).path).stem
        sub_key = Path(urlparse(sub_url).path).stem
        self._wbi_key = get_mixin_key(img_key, sub_key)
        self._wbi_fetched_at = self._clock()
        self.wbi_refreshes += 1
        return self._wbi_key

    def get_main_comments(self, aid: int | str, offset: str = "", mode: int = 2) -> dict[str, Any]:
        """获取一页一级评论（WBI 签名）。"""

        params: dict[str, Any] = {"type": 1, "oid": aid, "mode": mode}
        if offset:
            params["pagination_str"] = json.dumps(
                {"offset": offset}, separators=(",", ":")
            )
        signed = sign_wbi(params, self.get_wbi_key(), timestamp=int(self._time_fn()))
        result = self._api("/x/v2/reply/wbi/main", signed)
        return result.get("data") or {}

    def get_sub_comments(
        self,
        aid: int | str,
        root: str,
        page: int = 1,
        page_size: int = SUB_COMMENT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """获取一页楼中楼评论（``ps`` 显式传，不依赖服务端默认值）。"""

        params = {"type": 1, "oid": aid, "root": root, "pn": page, "ps": page_size}
        result = self._api("/x/v2/reply/reply", params)
        return result.get("data") or {}

    def stats_summary(self) -> dict[str, Any]:
        """返回客户端级效率指标（供报告与 CLI 汇总）。"""

        summary = self.metrics.summary()
        summary.update(
            {
                "risk_control_hits": self.risk_control_hits,
                "wbi_refreshes": self.wbi_refreshes,
                "limiter_interval": round(self._limiter.interval, 3),
                "limiter_total_wait": round(self._limiter.total_wait, 2),
                "robots_fetches": self._robots.fetches,
                "robots_checks": self._robots.checks,
                "buvid_enabled": self.init_buvid,
            }
        )
        return summary

    # ----------------------------------------------------------------- private
    def _ensure_buvid(self) -> None:
        """可选地初始化匿名设备指纹（buvid3/buvid4）。

        默认关闭：实测携带 buvid3 时 B 站匿名账号只能看到 3 条评论（且
        ``is_end=true``），不携带时同接口可正常分页。需要时可用
        ``BILI_INIT_BUVID=1`` / ``--init-buvid`` 显式开启。
        """

        if not self.init_buvid or self._buvid_ready:
            return
        try:
            response = request_with_retry(
                self._session,
                "GET",
                f"{self.api_base}/x/frontend/finger/spi",
                limiter=self._limiter,
                policy=self._policy,
                metrics=self.metrics,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent, "Referer": f"{self.web_base}/"},
                sleep=self._sleep,
                clock=self._clock,
            )
            payload = response.json()
            data = payload.get("data") or {}
            buvid3 = str(data.get("b_3") or "")
            buvid4 = str(data.get("b_4") or "")
            if buvid3:
                self._session.cookies.set("buvid3", buvid3, domain=".bilibili.com")
            if buvid4:
                self._session.cookies.set("buvid4", buvid4, domain=".bilibili.com")
            self._buvid_ready = bool(buvid3)
        except Exception as exc:  # 设备指纹是增强项，失败不阻塞采集
            logger.warning("获取 buvid 失败（继续匿名请求）：%s", exc)

    def _refresh_session(self) -> None:
        """风控后刷新 WBI 密钥（可选刷新设备指纹）。

        风控可能发生在刷新密钥的 nav 请求上；用 ``_refreshing`` 防止
        「刷新 → 风控 → 再刷新」的递归，最终由重试次数耗尽安全停止。
        """

        self._wbi_key = None
        self._wbi_fetched_at = 0.0
        if self._refreshing:
            return
        self._refreshing = True
        try:
            if self.init_buvid:
                self._buvid_ready = False
                try:
                    for cookie in ("buvid3", "buvid4"):
                        try:
                            self._session.cookies.clear(domain=".bilibili.com", path="/", name=cookie)
                        except Exception:
                            pass
                except Exception:  # pragma: no cover - 防御式
                    pass
                self._ensure_buvid()
            self.get_wbi_key(force=True)
        finally:
            self._refreshing = False

    def _cooldown(self, attempt: int, reason: str) -> None:
        """风控冷却：按重试次数递增，并同步拉长限速间隔。"""

        self.risk_control_hits += 1
        self._limiter.penalize(1.5)
        cooldown = self.risk_control_cooldown * (attempt + 1)
        logger.warning("触发风控（%s），冷却 %.1fs 并刷新会话", reason, cooldown)
        if cooldown > 0:
            self._sleep(cooldown)

    def _api(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        allow_nav: bool = False,
    ) -> dict[str, Any]:
        """调用一个 API 并统一处理传输重试、风控与业务码。"""

        url = f"{self.api_base}{path}"
        self._ensure_buvid()
        self.check_api_robots()

        risk_attempt = 0
        while True:
            response = request_with_retry(
                self._session,
                "GET",
                url,
                limiter=self._limiter,
                policy=self._policy,
                metrics=self.metrics,
                timeout=self.timeout,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": f"{self.web_base}/",
                    "Origin": self.web_base,
                },
                params=params,
                passthrough_statuses=(401, 403, 412),
                sleep=self._sleep,
                clock=self._clock,
            )
            status = int(getattr(response, "status_code", 0) or 0)

            if status in (401, 403, 412):
                if risk_attempt >= self.risk_control_retries:
                    raise RiskControlError(
                        f"HTTP {status}：访问受限，已重试 {risk_attempt} 次",
                        status_code=status,
                    )
                self._cooldown(risk_attempt, f"HTTP {status}")
                self._refresh_session()
                risk_attempt += 1
                continue

            try:
                payload = response.json()
            except ValueError:
                if risk_attempt >= self.risk_control_retries:
                    raise CrawlStopped("接口返回非 JSON 数据（可能被风控/拦截）") from None
                self._cooldown(risk_attempt, "响应不是 JSON")
                self._refresh_session()
                risk_attempt += 1
                continue

            if not isinstance(payload, dict):
                raise CrawlStopped("返回数据格式异常")

            code = payload.get("code")
            if code == 0 or (allow_nav and code == -101):
                return payload

            if code in RISK_CODES:
                reason = f"业务码 {code} {RISK_CODES[code]}"
                if risk_attempt >= self.risk_control_retries:
                    raise RiskControlError(
                        f"{reason}，已重试 {risk_attempt} 次", code=code
                    )
                self._cooldown(risk_attempt, reason)
                self._refresh_session()
                risk_attempt += 1
                continue

            message = str(payload.get("message") or "")
            if code == 12002 or code in (62002, 62004):
                raise CommentClosed(f"{FATAL_CODES.get(code, message)}（code={code}）")
            if code in FATAL_CODES:
                raise ApiError(code, FATAL_CODES[code] or message, url=path)
            raise ApiError(code, message or "未知业务错误", url=path)


# =========================================================================
# 评论数据整理（兼容旧函数名）
# =========================================================================

def normalize_comment_item(
    item: Mapping[str, Any],
    bvid: str,
    aid: int | str,
    title: str,
    level: int = 1,
    forced_root: str = "",
    *,
    strip_urls: bool = False,
    collected_at: str | None = None,
) -> dict[str, Any] | None:
    """兼容旧代码的薄封装，实际实现在 :mod:`src.crawler.normalize`。"""

    return normalize_comment(
        item,
        bvid,
        aid,
        title,
        level,
        forced_root,
        strip_urls=strip_urls,
        collected_at=collected_at,
    )


# =========================================================================
# CSV 保存、断点续爬
# =========================================================================

class CsvStore:
    """按 ``rpid`` 去重的增量 CSV 存储（UTF-8 BOM，Excel 可直接打开）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ids: set[str] = set()
        self.duplicates = 0
        self.empty = 0

        if self.path.exists() and self.path.stat().st_size > 0:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                if header and header != COLUMNS:
                    raise ConfigError(
                        f"CSV 表头与当前版本不一致：{self.path}\n"
                        f"  期望：{','.join(COLUMNS)}\n"
                        f"  实际：{','.join(header)}\n"
                        "请改名或删除旧文件后重试（旧数据可先备份）。"
                    )
                self.ids = {row["rpid"] for row in reader if row.get("rpid")}
        else:
            self._write_rows([], header_only=True)

    def _write_rows(self, rows: list[dict[str, Any]], *, header_only: bool = False) -> None:
        if header_only:
            with self.path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=COLUMNS)
                writer.writeheader()
            return
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writerows(rows)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:  # pragma: no cover - 某些文件系统不支持
                pass

    def add(self, records: Iterable[dict[str, Any] | None]) -> int:
        """追加保存并返回新增条数；重复 rpid 与空记录会被跳过。"""

        new_rows: list[dict[str, Any]] = []
        for row in records:
            if row is None:
                self.empty += 1
                continue
            rpid = str(row.get("rpid") or "")
            if not rpid:
                self.empty += 1
                continue
            if rpid in self.ids:
                self.duplicates += 1
                continue
            self.ids.add(rpid)
            new_rows.append(row)
        if new_rows:
            self._write_rows(new_rows)
        return len(new_rows)

    @property
    def total(self) -> int:
        return len(self.ids)


def read_csv_records(path: str | Path) -> list[dict[str, Any]]:
    """读取爬虫 CSV，返回 ``dict`` 列表（供下游适配器使用）。"""

    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _count_csv_rows(path: str | Path) -> int:
    """流式统计 CSV 数据行数（不把整份文件读进内存）。"""

    target = Path(path)
    if not target.exists():
        return 0
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            return sum(1 for _ in csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error):
        return 0


def _first_csv_title(path: str | Path) -> str:
    """读取 CSV 第一行的 ``video_title``（快速跳过已完成视频时补标题）。"""

    target = Path(path)
    if not target.exists():
        return ""
    try:
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                return str(row.get("video_title") or "")
    except (OSError, UnicodeDecodeError, csv.Error):
        return ""
    return ""


@dataclass
class MergeReport:
    """``merge_video_csvs`` 的合并统计（供大规模采集后的统一导出）。"""

    output: str
    files: int
    rows: int
    videos: int
    bytes: int
    skipped_files: int = 0
    deduplicated: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def merge_video_csvs(
    input_dir: str | Path,
    output_path: str | Path,
    *,
    pattern: str = "*_comments.csv",
    dedupe_rpid: bool = False,
    strict: bool = True,
) -> MergeReport:
    """把目录下按视频拆分的 CSV **流式合并**成一份大 CSV。

    大规模采集时每个视频一个 CSV（便于断点/重跑），分析前通常需要一份总表。
    本函数逐文件流式读取、只写一份输出，内存占用与「单文件行数」无关；默认
    不按 rpid 去重（B 站 rpid 全局唯一，且每个视频文件内部已去重），
    需要时传 ``dedupe_rpid=True``（内存里保留 rpid 集合）。

    Args:
        input_dir: 爬虫输出目录（``--out-dir``）。
        output_path: 合并输出路径（UTF-8 BOM，Excel 可直接打开）。
        pattern: 匹配视频 CSV 的 glob。
        dedupe_rpid: 是否跨文件按 rpid 去重（大目录会占用较多内存）。
        strict: 遇到表头不一致/损坏文件时是否失败（False 时跳过并记录）。
    """

    source_dir = Path(input_dir)
    if not source_dir.is_dir():
        raise ConfigError(f"输入目录不存在：{source_dir}")
    output = Path(output_path)
    if output.parent == source_dir and output.name.endswith("_comments.csv"):
        # 防止把合并输出再次当成输入
        pass
    files = sorted(
        path
        for path in source_dir.glob(pattern)
        if path.is_file() and path.resolve() != output.resolve()
    )
    if not files:
        raise ConfigError(f"在 {source_dir} 下没有找到匹配 {pattern!r} 的 CSV")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".part", dir=str(output.parent)
    )
    temp_path = Path(temp_name)
    rows = 0
    skipped = 0
    deduplicated = 0
    errors: list[str] = []
    videos: set[str] = set()
    seen_rpids: set[str] = set()
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as out_handle:
            writer = csv.DictWriter(out_handle, fieldnames=COLUMNS)
            writer.writeheader()
            for path in files:
                try:
                    with path.open("r", encoding="utf-8-sig", newline="") as handle:
                        reader = csv.DictReader(handle)
                        header = reader.fieldnames or []
                        if header != COLUMNS:
                            raise ConfigError(
                                f"{path} 表头与当前版本不一致：{header}"
                            )
                        for row in reader:
                            if dedupe_rpid:
                                rpid = str(row.get("rpid") or "")
                                if rpid and rpid in seen_rpids:
                                    deduplicated += 1
                                    continue
                                if rpid:
                                    seen_rpids.add(rpid)
                            writer.writerow({column: row.get(column, "") for column in COLUMNS})
                            rows += 1
                            bvid = str(row.get("bvid") or "")
                            if bvid:
                                videos.add(bvid)
                except (OSError, UnicodeDecodeError, csv.Error, ConfigError) as exc:
                    if strict:
                        raise
                    skipped += 1
                    errors.append(f"{path.name}: {exc}")
                    logger.warning("跳过无法合并的文件 %s：%s", path, exc)
            out_handle.flush()
            try:
                os.fsync(out_handle.fileno())
            except OSError:  # pragma: no cover - 某些文件系统不支持
                pass
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:  # pragma: no cover
                pass

    report = MergeReport(
        output=str(output),
        files=len(files) - skipped,
        rows=rows,
        videos=len(videos),
        bytes=output.stat().st_size,
        skipped_files=skipped,
        deduplicated=deduplicated,
        errors=errors,
    )
    logger.info(
        "已合并 %d 个视频 CSV → %s（%d 行 / %d 个视频 / %.1f MB）",
        report.files, output, report.rows, report.videos, report.bytes / (1024 * 1024),
    )
    return report


def load_state(
    path: Path,
    mode: int,
    include_replies: bool,
    max_sub_pages: int,
    reset: bool = False,
) -> dict[str, Any]:
    """读取断点；配置不一致时拒绝续爬（避免把两种口径的数据混在一起）。

    兼容旧断点：旧版本没有 ``sub_done`` 字段，读取时按空列表处理，
    相当于重新请求这些根评论的楼中楼（CSV 按 rpid 去重，不会重复落盘）。
    """

    if path.exists() and not reset:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        if (
            state.get("mode") != mode
            or state.get("include_replies") != include_replies
            or state.get("max_sub_pages") != max_sub_pages
        ):
            raise ConfigError(
                "本次配置与断点配置不同，如需重新扫描请使用 --reset-state"
            )
        if not isinstance(state.get("sub_done"), list):
            state["sub_done"] = []
        return state
    return {
        "mode": mode,
        "include_replies": include_replies,
        "max_sub_pages": max_sub_pages,
        "offset": "",
        "pages_done": 0,
        "finished": False,
        "sub_done": [],
    }


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    """原子写入断点文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(dict(state), handle, ensure_ascii=False, indent=2)
    temp_path.replace(path)


# =========================================================================
# 采集结果与核心流程
# =========================================================================

@dataclass
class VideoCrawlResult:
    """单个视频的采集结果。"""

    bvid: str
    aid: int | None = None
    title: str = ""
    owner: str = ""
    comment_total_on_site: int = 0
    pages_crawled: int = 0
    comments_new: int = 0
    comments_total: int = 0
    duplicates: int = 0
    empty: int = 0
    elapsed_seconds: float = 0.0
    csv_path: str | None = None
    state_path: str | None = None
    requests: int = 0
    retries: int = 0
    risk_control_hits: int = 0
    finished: bool = False
    skipped: bool = False
    timed_out: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "bvid": self.bvid,
            "aid": self.aid,
            "title": self.title,
            "owner": self.owner,
            "comment_total_on_site": self.comment_total_on_site,
            "pages_crawled": self.pages_crawled,
            "comments_new": self.comments_new,
            "comments_total": self.comments_total,
            "duplicates": self.duplicates,
            "empty": self.empty,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "comments_per_second": round(self.comments_new / self.elapsed_seconds, 3)
            if self.elapsed_seconds > 0
            else 0.0,
            "csv_path": self.csv_path,
            "state_path": self.state_path,
            "requests": self.requests,
            "retries": self.retries,
            "risk_control_hits": self.risk_control_hits,
            "finished": self.finished,
            "skipped": self.skipped,
            "timed_out": self.timed_out,
            "error": self.error,
        }


@dataclass
class CrawlReport:
    """一次批量采集的汇总报告（含效率数据）。"""

    videos: list[VideoCrawlResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    source_note_path: str | None = None
    robots: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_new(self) -> int:
        return sum(video.comments_new for video in self.videos)

    @property
    def total_saved(self) -> int:
        return sum(video.comments_total for video in self.videos)

    @property
    def timed_out(self) -> bool:
        """本次批量采集中是否有视频因 ``max_seconds`` 时间预算提前停止。"""

        return any(video.timed_out for video in self.videos)

    @property
    def skipped(self) -> int:
        """本次批量中「已完成、零请求跳过」的视频数。"""

        return sum(1 for video in self.videos if video.skipped)

    @property
    def failed(self) -> int:
        """本次批量中带 error 的视频数（含非法 BV、评论关闭、风控停止）。"""

        return sum(1 for video in self.videos if video.error)

    @property
    def completed(self) -> int:
        return sum(
            1
            for video in self.videos
            if video.finished and not video.error and not video.skipped
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "video_count": len(self.videos),
            "completed_count": self.completed,
            "skipped_count": self.skipped,
            "failed_count": self.failed,
            "total_new": self.total_new,
            "total_saved": self.total_saved,
            "timed_out": self.timed_out,
            "comments_per_second": round(self.total_new / self.elapsed_seconds, 3)
            if self.elapsed_seconds > 0
            else 0.0,
            "videos": [video.as_dict() for video in self.videos],
            "metrics": self.metrics,
            "source_note_path": self.source_note_path,
            "robots": list(self.robots),
        }


def _sub_page_reached_end(
    sub_replies: list[Any],
    page_info: Mapping[str, Any],
    page_number: int,
    requested_page_size: int,
) -> bool:
    """判断楼中楼是否已到最后一页（不再假设服务端每页固定 20 条）。

    结束判断优先使用服务端返回的 ``page.count``（总数）与 ``page.size``
    （服务端实际页大小），两者缺失或为 0 时退回请求页大小：

    * ``count`` 明确（> 0）时以总数为准：已取条数达到总数即结束；否则继续
      翻页（本页偏短也可能只是服务端页大小与请求不同，由 ``max_sub_pages``
      兜底，避免把未采完误判为结束）；
    * ``count`` 缺失或为 0（接口未给出，视为不确定）时，只能用「短页」推断
      结束：不满一页即结束，满页继续翻页；
    * 空页一律视为结束（同时防止接口异常时空页死循环）。
    """

    if not sub_replies:
        return True
    server_page_size = to_int(page_info.get("size"))
    effective_page_size = (
        server_page_size if server_page_size > 0 else requested_page_size
    )
    total = to_int(page_info.get("count"))
    if total > 0:
        return page_number * effective_page_size >= total
    return len(sub_replies) < effective_page_size


def _collect_page_rows(
    client: BiliClient,
    replies: list[Mapping[str, Any]],
    bvid: str,
    aid: int | str,
    title: str,
    *,
    include_replies: bool,
    max_sub_pages: int,
    max_items: int,
    collected_at: str,
    strip_urls: bool,
    budget_remaining: int,
    sub_done: set[str] | None = None,
    on_root_done: Any = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], int, int, int, bool]:
    """整理一页一级评论（含可选的楼中楼）。

    返回 ``(记录, 新增候选, 空, 子页请求数, 是否因时间预算提前停止)``。
    ``sub_done`` 中的根评论会跳过楼中楼请求（断点续爬）；某条根评论的楼中楼
    处理完（自然结束或达到 ``max_sub_pages`` 上限）后通过 ``on_root_done``
    回调，供调用方立即写入断点。
    """

    rows: list[dict[str, Any]] = []
    empty = 0
    sub_pages = 0
    stopped_early = False
    done_roots = sub_done if sub_done is not None else set()

    for item in replies:
        if max_items and len(rows) >= max_items:
            break
        if not isinstance(item, Mapping):
            empty += 1
            continue
        row = normalize_comment(
            item,
            bvid,
            aid,
            title,
            level=1,
            strip_urls=strip_urls,
            collected_at=collected_at,
        )
        if row is None:
            empty += 1
        else:
            rows.append(row)

        if include_replies:
            root_id = str(item.get("rpid_str") or item.get("rpid") or "")
            reply_count = to_int(item.get("rcount"))
            inline_replies = item.get("replies")
            if (
                root_id
                and root_id not in done_roots
                and (reply_count > 0 or inline_replies)
            ):
                root_completed = True
                sub_page_limit = (
                    max_sub_pages if max_sub_pages else MAX_SUB_PAGES_HARD_LIMIT
                )
                for sub_page in range(1, sub_page_limit + 1):
                    if max_items and len(rows) >= max_items:
                        root_completed = False
                        break
                    if budget_remaining and len(rows) >= budget_remaining:
                        root_completed = False
                        break
                    try:
                        sub_data = client.get_sub_comments(
                            aid,
                            root_id,
                            page=sub_page,
                            page_size=SUB_COMMENT_PAGE_SIZE,
                        )
                    except CommentClosed:
                        break
                    sub_pages += 1
                    sub_replies = sub_data.get("replies") or []
                    if not isinstance(sub_replies, list):
                        raise CrawlStopped("二级评论格式异常")
                    truncated = False
                    for sub_item in sub_replies:
                        if max_items and len(rows) >= max_items:
                            truncated = True
                            break
                        sub_row = normalize_comment(
                            sub_item,
                            bvid,
                            aid,
                            title,
                            level=2,
                            forced_root=root_id,
                            strip_urls=strip_urls,
                            collected_at=collected_at,
                        )
                        if sub_row is None:
                            empty += 1
                        else:
                            rows.append(sub_row)
                    if truncated:
                        root_completed = False
                        break
                    page_info = sub_data.get("page") or {}
                    if not isinstance(page_info, Mapping):
                        page_info = {}
                    if _sub_page_reached_end(
                        sub_replies, page_info, sub_page, SUB_COMMENT_PAGE_SIZE
                    ):
                        break
                if root_completed:
                    # 先回调（由调用方把根评论记入 sub_done 并立即保存断点），
                    # 再加入本页的去重集合（两者可能是同一个 set，顺序不能反）。
                    if on_root_done is not None:
                        try:
                            on_root_done(root_id)
                        except Exception:  # pragma: no cover - 断点写入失败不应中断采集
                            logger.exception("写入楼中楼断点失败（根评论 %s）", root_id)
                    done_roots.add(root_id)

        if deadline is not None and time.monotonic() >= deadline:
            stopped_early = True
            break

    return rows, len(rows), empty, sub_pages, stopped_early


def crawl_video(
    client: BiliClient,
    bvid: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    include_replies: bool = True,
    max_sub_pages: int = DEFAULT_MAX_SUB_PAGES,
    mode: int = 2,
    max_items: int = 0,
    max_seconds: float = 0.0,
    out_dir: str | Path = SAVE_DIR,
    reset_state: bool = False,
    skip_finished: bool = True,
    strip_urls: bool = False,
    write_source_note: bool = True,
    on_page: Any = None,
) -> VideoCrawlResult:
    """采集单个视频的一级评论与（可选）楼中楼，支持断点续爬。

    Args:
        max_pages: 一级评论最多页数；``0`` 表示不限（用
            :data:`MAX_PAGES_HARD_LIMIT` 防死循环）。
        max_sub_pages: 每条根评论楼中楼最多页数；``0`` 表示不限。
        skip_finished: 断点已标记完成且 CSV 有数据时**直接跳过、不发起任何网络
            请求**（大规模批量重跑的关键优化）；``False`` 时强制重扫。
        max_seconds: 单个视频的时间预算（秒，0 表示不限制，默认不改变原有
            行为）。在页之间、根评论之间检查 ``time.monotonic()`` 截止时间；
            到时安全停止并保存断点/CSV（不丢已采数据、不把未采完的页算作
            完成），结果标记 ``timed_out=True``。
    """

    max_pages, max_sub_pages = validate_page_limits(max_pages, max_sub_pages)
    started = time.monotonic()
    budget_seconds = max(0.0, float(max_seconds or 0.0))
    deadline = started + budget_seconds if budget_seconds > 0 else None
    started_requests = client.metrics.logical_requests
    started_retries = client.metrics.retries
    started_risk = client.risk_control_hits
    result = VideoCrawlResult(bvid=bvid)

    out_dir = Path(out_dir)
    csv_path = out_dir / f"{bvid}_comments.csv"
    state_path = out_dir / ".checkpoints" / f"{bvid}.json"
    result.csv_path = str(csv_path)
    result.state_path = str(state_path)
    state = load_state(
        state_path, mode, include_replies, max_sub_pages, reset=reset_state
    )

    # 大 规 模 采 集 的 关 键 优 化：已完成且 CSV 有数据时，不请求 robots/meta/评论，
    # 直接按断点恢复标题与条数返回。这样重跑上万条 BV 清单时，已采视频几乎零成本。
    if state.get("finished") and skip_finished and csv_path.exists():
        total = _count_csv_rows(csv_path)
        if total > 0:
            result.finished = True
            result.skipped = True
            result.comments_total = total
            result.title = str(state.get("title") or _first_csv_title(csv_path))
            result.aid = to_int(state.get("aid"), default=0) or None
            result.comment_total_on_site = to_int(
                state.get("comment_total_on_site"), default=0
            )
            result.elapsed_seconds = time.monotonic() - started
            return result

    if client.respect_robots:
        client.check_content_page_robots(bvid)
    client.check_api_robots()

    try:
        meta = client.get_video_meta(bvid)
    except CommentClosed as exc:
        result.error = f"{exc}"
        result.elapsed_seconds = time.monotonic() - started
        return result

    result.aid = meta["aid"]
    result.title = meta["title"]
    result.owner = meta["owner"]
    result.comment_total_on_site = meta["comment_count"]
    store = CsvStore(csv_path)

    offset = state.get("offset", "")
    pages_done = int(state.get("pages_done") or 0)
    total_new = 0
    collected_at = utc_iso()

    # `sub_done`：当前这一页中楼中楼已完整处理过的根评论 rpid（断点恢复时跳过）。
    # 每处理完一条根评论就写入断点；整页完成后清空——此时 offset 已推进到下一页，
    # 旧页不会被回访，清空可避免 state 随长跑无限增长。
    sub_done_order: list[str] = []
    for raw_root in state.get("sub_done") or []:
        root_key = str(raw_root)
        if root_key and root_key not in sub_done_order:
            sub_done_order.append(root_key)
    sub_done: set[str] = set(sub_done_order)
    sub_done_capped = False

    def _save_checkpoint(current_offset: str, done_pages: int, finished: bool) -> None:
        save_state(
            state_path,
            {
                "mode": mode,
                "include_replies": include_replies,
                "max_sub_pages": max_sub_pages,
                "offset": current_offset,
                "pages_done": done_pages,
                "finished": finished,
                "sub_done": list(sub_done_order),
                # 供「已完成视频零请求跳过」使用：恢复时无需再请求 meta
                "title": result.title,
                "aid": result.aid,
                "comment_total_on_site": result.comment_total_on_site,
            },
        )

    def _mark_root_done(root_id: str) -> None:
        """记录一条根评论的楼中楼已处理完，并立即保存断点（offset 不推进）。"""

        nonlocal sub_done_capped
        if root_id in sub_done:
            return
        sub_done.add(root_id)
        sub_done_order.append(root_id)
        while len(sub_done_order) > MAX_SUB_DONE_ENTRIES:
            evicted = sub_done_order.pop(0)
            sub_done.discard(evicted)
            if not sub_done_capped:
                logger.warning(
                    "断点 sub_done 超过 %d 条，已丢弃最早的根评论记录"
                    "（仍可安全续爬，只会重新请求这些根评论的楼中楼）",
                    MAX_SUB_DONE_ENTRIES,
                )
                sub_done_capped = True
        _save_checkpoint(offset, pages_done, False)

    def _notify_page(page_number: int, added: int) -> None:
        if on_page is None:
            return
        try:
            on_page(result, page_number, added, store.total)
        except Exception:  # pragma: no cover - 回调不应影响采集
            logger.exception("on_page 回调异常")

    try:
        page_limit = max_pages if max_pages else MAX_PAGES_HARD_LIMIT
        for page_index in range(page_limit):
            if deadline is not None and time.monotonic() >= deadline:
                # 页间检查：时间预算用尽，保存断点后安全停止（默认 max_seconds=0 不触发）
                result.timed_out = True
                _save_checkpoint(offset, pages_done, False)
                break
            if max_items and total_new >= max_items:
                break
            data = client.get_main_comments(result.aid, offset=offset, mode=mode)
            replies = data.get("replies") or []
            cursor = data.get("cursor") or {}
            if not isinstance(replies, list):
                raise CrawlStopped("一级评论格式异常")
            if not replies and not cursor.get("is_end"):
                raise CrawlStopped("当前页没有评论，但未收到结束标记（可能被风控）")

            remaining = max_items - total_new if max_items else 0
            rows, _candidates, _empty, _sub_pages, page_stopped = _collect_page_rows(
                client,
                replies,
                bvid,
                result.aid,
                result.title,
                include_replies=include_replies,
                max_sub_pages=max_sub_pages,
                max_items=remaining,
                collected_at=collected_at,
                strip_urls=strip_urls,
                budget_remaining=remaining,
                sub_done=sub_done,
                on_root_done=_mark_root_done,
                deadline=deadline,
            )
            added = store.add(rows)
            total_new += added
            result.pages_crawled += 1

            pagination = cursor.get("pagination_reply") or {}
            next_offset = pagination.get("next_offset")
            is_end = bool(cursor.get("is_end"))
            if page_stopped:
                # 根评论间检查到时间预算用尽：当前页只采了一部分，offset 不推进，
                # 下次从本页继续（CSV 去重 + sub_done 保证不重复请求楼中楼）。
                result.timed_out = True
                _save_checkpoint(offset, pages_done, False)
                _notify_page(page_index + 1, added)
                break
            if not is_end and (not next_offset or next_offset == offset):
                raise CrawlStopped("无法获取下一页游标，已保存的数据不会丢失")

            pages_done += 1
            sub_done.clear()
            sub_done_order.clear()
            offset = "" if is_end else next_offset
            _save_checkpoint(offset, pages_done, is_end)
            _notify_page(page_index + 1, added)
            if is_end:
                result.finished = True
                break
            if max_items and total_new >= max_items:
                break
    finally:
        result.comments_new = total_new
        result.comments_total = store.total
        result.duplicates = store.duplicates
        result.empty = store.empty
        result.elapsed_seconds = time.monotonic() - started
        result.requests = client.metrics.logical_requests - started_requests
        result.retries = client.metrics.retries - started_retries
        result.risk_control_hits = client.risk_control_hits - started_risk

    if write_source_note and result.comments_total:
        reply_part = (
            f"楼中楼=开（每条最多 {max_sub_pages} 页）"
            if include_replies
            else "楼中楼=关"
        )
        note = (
            f"mode={'time' if mode == 2 else 'hot'}；"
            f"一级 {result.pages_crawled} 页；{reply_part}；"
            f"min-delay={client._limiter.base_interval:g}s+抖动；"
            f"请求 {result.requests} 次（重试 {result.retries}）；"
            f"风控 {result.risk_control_hits} 次；耗时 {result.elapsed_seconds:.2f}s；"
            f"robots：视频页允许，api.bilibili.com="
            f"{'允许' if (client.api_robots_decision or {}).get('allowed') else 'Disallow（warn 模式，见 README 合规说明）'}"
        )
        sources.register_source(
            SourceRecord(
                name="B站视频评论",
                url=f"{client.web_base}/video/{bvid}",
                collected_at=utc_iso(),
                count=result.comments_total,
                labeled=False,
                note=note,
            )
        )

    return result


def crawl_bilibili(
    bvids: Iterable[str],
    *,
    client: BiliClient | None = None,
    delay: float = DEFAULT_DELAY_SECONDS,
    timeout: float = DEFAULT_TIMEOUT,
    max_pages: int = DEFAULT_MAX_PAGES,
    include_replies: bool = True,
    max_sub_pages: int = DEFAULT_MAX_SUB_PAGES,
    mode: int = 2,
    max_items: int = 0,
    max_seconds: float = 0.0,
    out_dir: str | Path = SAVE_DIR,
    reset_state: bool = False,
    skip_finished: bool = True,
    require_login: bool = False,
    strip_urls: bool = False,
    respect_robots: bool = True,
    api_robots_policy: str | None = None,
    write_source_note: bool = True,
    on_page: Any = None,
    **client_kwargs: Any,
) -> CrawlReport:
    """批量采集多个视频，返回汇总报告；面向大规模清单的可续采设计。

    - 按输入顺序对 BV 号去重，非法 BV 逐条记录错误；
    - ``skip_finished=True``（默认）时，断点已完成且有 CSV 的视频**不再发起任何请求**，
      适合把上万条 BV 清单反复重跑直到全部完成；
    - ``max_pages=0`` / ``max_sub_pages=0`` 表示不限页数（内部有硬上限防死循环）；
    - 遇到 :class:`RiskControlError` 立即停止后续视频（避免继续触发风控），已经
      落盘的 CSV/断点保留，重跑即续采；其它 :class:`CrawlStopped` 记录后继续下一个；
    - ``require_login=True`` 时先检查登录态，未登录直接抛 :class:`ConfigError`；
      默认匿名模式下会提示「评论可见量有限」；
    - 批量采集只在**结束时登记一条汇总来源**，避免逐视频重写 sources.json/Markdown
      造成 O(N²) 磁盘写。

    ``max_seconds`` 是「单个视频」的时间预算（0 表示不限），透传给
    :func:`crawl_video`；任何视频因预算停止时 ``report.timed_out`` 为 True。
    """

    max_pages, max_sub_pages = validate_page_limits(max_pages, max_sub_pages)
    report = CrawlReport(started_at=utc_iso())
    started = time.monotonic()
    owned_client = client is None
    if client is None:
        client = BiliClient(
            delay=delay,
            timeout=timeout,
            respect_robots=respect_robots,
            api_robots_policy=api_robots_policy,
            **client_kwargs,
        )
    try:
        if require_login:
            status = client.login_status()
            if not status.get("is_login"):
                raise ConfigError(
                    "require_login=True 但当前未登录。请设置 BILI_SESSDATA 或 "
                    "BILI_COOKIE 后重试（匿名模式只能看到少量评论，无法大规模采集）"
                )
            logger.info("登录态检查通过：%s（mid=%s）", status.get("uname"), status.get("mid"))
        elif not getattr(client, "has_credentials", True):
            logger.warning(
                "未配置 BILI_SESSDATA / BILI_COOKIE：匿名模式评论可见量随接口策略波动"
                "（实测单视频 3~200+ 条，未必能到全量），大规模采集请先配置登录态。"
            )

        seen_bvids: set[str] = set()
        for value in bvids:
            try:
                bvid = parse_bvid(value)
            except ValueError as exc:
                report.videos.append(
                    VideoCrawlResult(bvid=str(value), error=str(exc))
                )
                continue
            if bvid in seen_bvids:
                logger.warning("跳过重复输入的 BV 号（只采集一次）：%s", bvid)
                continue
            seen_bvids.add(bvid)
            try:
                result = crawl_video(
                    client,
                    bvid,
                    max_pages=max_pages,
                    include_replies=include_replies,
                    max_sub_pages=max_sub_pages,
                    mode=mode,
                    max_items=max_items,
                    max_seconds=max_seconds,
                    out_dir=out_dir,
                    reset_state=reset_state,
                    skip_finished=skip_finished,
                    strip_urls=strip_urls,
                    # 批量模式由下面统一登记一条汇总来源，避免逐视频重写来源文件
                    write_source_note=False,
                    on_page=on_page,
                )
            except RiskControlError as exc:
                result = VideoCrawlResult(bvid=bvid, error=f"风控停止：{exc}")
                report.videos.append(result)
                logger.error("触发风控，停止后续视频采集：%s", exc)
                break
            except CrawlStopped as exc:
                result = VideoCrawlResult(bvid=bvid, error=str(exc))
                logger.warning("视频 %s 采集停止：%s", bvid, exc)
            except Exception as exc:  # noqa: BLE001 - 单视频意外错误不应中断整批
                result = VideoCrawlResult(
                    bvid=bvid, error=f"意外错误：{type(exc).__name__}: {exc}"
                )
                logger.exception("视频 %s 发生意外错误，跳过并继续后续视频", bvid)
            report.videos.append(result)
            done = len(report.videos)
            if done % 100 == 0:
                logger.info(
                    "进度：已处理 %d 个视频（新增 %d / 累计 %d 条）",
                    done, report.total_new, report.total_saved,
                )

        if write_source_note and report.total_saved:
            collected = [video for video in report.videos if video.comments_total]
            first = collected[0] if collected else report.videos[0]
            mode_name = "time" if mode == 2 else "hot"
            reply_part = (
                f"楼中楼=开（每条最多 {max_sub_pages or '不限'} 页）"
                if include_replies
                else "楼中楼=关"
            )
            logger.info("正在登记批量来源：%d 个视频 / %d 条评论", len(collected), report.total_saved)
            note = (
                f"批量采集 {len(collected)} 个视频；mode={mode_name}；{reply_part}；"
                f"max_pages={max_pages or '不限'}；min-delay={client._limiter.base_interval:g}s+抖动；"
                f"请求 {report.metrics.get('requests', 0)} 次（重试 {report.metrics.get('retries', 0)}）；"
                f"风控 {report.metrics.get('risk_control_hits', 0)} 次；"
                f"输出目录={Path(out_dir)}；robots：视频页允许，api.bilibili.com="
                f"{'允许' if (client.api_robots_decision or {}).get('allowed') else 'Disallow（warn 模式，见 README 合规说明）'}"
            )
            sources.register_source(
                SourceRecord(
                    name="B站视频评论（批量）",
                    url=f"{client.web_base}/video/{first.bvid}",
                    collected_at=utc_iso(),
                    count=report.total_saved,
                    labeled=False,
                    note=note,
                )
            )
            report.source_note_path = str(sources.SOURCE_NOTE_PATH)
    finally:
        report.finished_at = utc_iso()
        report.elapsed_seconds = time.monotonic() - started
        report.metrics = client.stats_summary()
        report.robots = list(client.robots_checks)
        if owned_client:
            client.close()
    return report


def fetch_video_comments(
    bvid: str,
    *,
    client: BiliClient | None = None,
    max_pages: int = 1,
    include_replies: bool = False,
    max_sub_pages: int = 1,
    mode: int = 2,
    max_items: int = 0,
    **client_kwargs: Any,
) -> list[dict[str, Any]]:
    """无持久化地抓取一个视频的评论，供 ``src/videos`` / ``src/artifacts`` 适配。

    与 :func:`crawl_video` 使用相同的客户端、限速、robots 校验与标准化逻辑，
    但不写 ``data/raw`` 与来源说明（调用方自行决定如何落盘）。
    """

    import tempfile

    owned_client = client is None
    if client is None:
        client = BiliClient(**client_kwargs)
    try:
        with tempfile.TemporaryDirectory(prefix="bili-crawl-") as temp_dir:
            result = crawl_video(
                client,
                bvid,
                max_pages=max_pages,
                include_replies=include_replies,
                max_sub_pages=max_sub_pages,
                mode=mode,
                max_items=max_items,
                out_dir=temp_dir,
                write_source_note=False,
            )
            if result.error:
                raise CrawlStopped(result.error)
            return read_csv_records(result.csv_path) if result.csv_path else []
    finally:
        if owned_client:
            client.close()


# =========================================================================
# 命令行入口
# =========================================================================

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "B站视频评论采集工具（WBI 签名 / 风控退避 / 断点续爬 / 来源登记）；"
            "大规模采集建议使用 --bvid-file + --max-pages 0 + 登录态。"
        )
    )
    parser.add_argument("--bvid", nargs="+", help="一个或多个视频 BV 号或链接；省略时提示输入")
    default_bvid_files = [value for value in [os.getenv("BV_LIST_FILE", "").strip()] if value]
    parser.add_argument("--bvid-file", action="append", default=default_bvid_files,
                        help="从文本文件批量读取 BV（一行一个，支持 # 注释；可多次指定）")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"每个视频最多采集多少页一级评论；0=不限（默认 {DEFAULT_MAX_PAGES}，"
                             f"硬上限 {MAX_PAGES_HARD_LIMIT}）")
    parser.add_argument("--max-sub-pages", type=int, default=DEFAULT_MAX_SUB_PAGES,
                        help=f"每条根评论最多采集多少页楼中楼；0=不限（默认 {DEFAULT_MAX_SUB_PAGES}，"
                             f"硬上限 {MAX_SUB_PAGES_HARD_LIMIT}）")
    parser.add_argument("--max-items", type=int, default=0, help="每个视频最多新增多少条（0 表示不限制）")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="单个视频的最长采集时间（秒，0 表示不限制；到时保存断点后安全停止）")
    parser.add_argument("--include-replies", action="store_true", help="是否采集楼中楼")
    parser.add_argument("--mode", choices=["time", "hot"], default="time", help="一级评论排序：time=按时间，hot=按热度")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="请求最小间隔（秒，不得小于 2）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次请求超时（秒）")
    parser.add_argument("--out-dir", default=str(SAVE_DIR), help="CSV 与断点输出目录")
    parser.add_argument("--skip-finished", dest="skip_finished", action="store_true",
                        default=_env_flag("BILI_SKIP_FINISHED", True),
                        help="断点已完成且有 CSV 的视频直接跳过、不发起请求（默认开启）")
    parser.add_argument("--rescan-finished", dest="skip_finished", action="store_false",
                        help="强制重扫已完成的视频（保留 CSV 去重）")
    parser.add_argument("--require-login", action="store_true",
                        default=_env_flag("BILI_REQUIRE_LOGIN", False),
                        help="未登录时直接失败（大规模采集建议开启，避免匿名配额限制）")
    parser.add_argument("--check-login", action="store_true",
                        help="只检查登录态并退出（0=已登录，2=未登录）")
    parser.add_argument("--merge-output", default=None,
                        help="采集后把 out-dir 下所有视频 CSV 流式合并到该文件（也可只传此项做纯合并）")
    parser.add_argument("--merge-pattern", default="*_comments.csv", help="合并时的文件匹配模式")
    parser.add_argument("--merge-dedupe", action="store_true", help="合并时按 rpid 跨文件去重（更耗内存）")
    parser.add_argument("--merge-lenient", action="store_true", help="合并时跳过坏文件而不是失败")
    parser.add_argument("--strip-urls", action="store_true", help="标准化时删除评论中的 URL")
    parser.add_argument("--api-robots-policy", choices=["warn", "enforce", "ignore"], default=None,
                        help="api.bilibili.com 的 robots 处理策略（默认读 BILI_API_ROBOTS_POLICY 或 warn）")
    parser.add_argument("--init-buvid", action="store_true",
                        help="初始化 buvid3/buvid4 设备指纹（默认关闭；实测会限制匿名可见评论数）")
    parser.add_argument("--reset-state", action="store_true", help="从第一页重新扫描，保留 CSV 去重")
    parser.add_argument("--no-source-note", action="store_true", help="不写入 data/raw/来源说明.md（测试用）")
    parser.add_argument("--log-file", default=None, help="把日志同时写入该文件（长跑建议开启）")
    parser.add_argument("--quiet", action="store_true", help="只输出警告/错误与最终汇总")
    parser.add_argument("--json", action="store_true", help="向 stdout 输出机器可读 JSON 报告")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def _print_merge(report: "MergeReport") -> None:
    print(f"合并完成：{report.files} 个视频 CSV → {report.output}")
    print(f"  行数 {report.rows}；视频数 {report.videos}；"
          f"大小 {report.bytes / (1024 * 1024):.2f} MB"
          + (f"；按 rpid 去重 {report.deduplicated}" if report.deduplicated else ""))
    if report.skipped_files:
        print(f"  跳过坏文件 {report.skipped_files}")
    for error in report.errors:
        print(f"  [跳过] {error}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        validate_page_limits(args.max_pages, args.max_sub_pages)
    except ConfigError as exc:
        parser.error(str(exc))
    if args.delay < 2:
        parser.error("--delay 不能小于 2 秒（限速是硬性要求）")
    if args.max_items < 0:
        parser.error("--max-items 不能为负数")
    if args.max_seconds < 0:
        parser.error("--max-seconds 不能为负数")

    _setup_logging(args)

    if not args.json and not args.quiet:
        print("B站评论采集工具已启动", flush=True)

    # 纯检查登录态：不采集，方便大规模采集前确认凭据。
    if args.check_login:
        client = BiliClient(
            delay=args.delay,
            timeout=args.timeout,
            api_robots_policy=args.api_robots_policy,
            init_buvid=args.init_buvid,
        )
        try:
            status = client.login_status(force=True)
        finally:
            client.close()
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            state = "已登录" if status["is_login"] else "未登录（匿名）"
            print(f"登录态：{state}；用户：{status['uname'] or '-'}；mid：{status['mid'] or '-'}；"
                  f"是否配置凭据：{status['has_credentials']}")
        return 0 if status["is_login"] else 2

    values: list[str] = list(args.bvid or [])
    try:
        for path in args.bvid_file:
            values.extend(read_bvid_list(path))
    except ConfigError as exc:
        print(f"读取 BV 清单失败：{exc}", file=sys.stderr)
        return 1

    # 只做合并（不采集）时允许没有 BV。
    if not values and args.merge_output:
        try:
            merge_report = merge_video_csvs(
                args.out_dir,
                args.merge_output,
                pattern=args.merge_pattern,
                dedupe_rpid=args.merge_dedupe,
                strict=not args.merge_lenient,
            )
        except ConfigError as exc:
            print(f"合并失败：{exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(merge_report.as_dict(), ensure_ascii=False, indent=2))
        else:
            _print_merge(merge_report)
        return 0

    if not values:
        try:
            value = input("请输入 BV 号或视频链接（多个用空格分隔）：").strip()
        except (EOFError, OSError):
            parser.error(
                "当前运行窗口无法接收输入。请在 VS Code 终端运行，或通过 --bvid/--bvid-file 指定视频。"
            )
        except KeyboardInterrupt:
            print("\n已取消采集", flush=True)
            return 130
        if not value:
            parser.error("未输入 BV 号或视频链接")
        values = value.split()

    def _progress(result: VideoCrawlResult, page: int, added: int, total: int) -> None:
        if not args.quiet:
            logger.info("[%s] 第 %d 页 +%d，累计 %d", result.bvid, page, added, total)

    try:
        report = crawl_bilibili(
            values,
            delay=args.delay,
            timeout=args.timeout,
            max_pages=args.max_pages,
            include_replies=args.include_replies,
            max_sub_pages=args.max_sub_pages,
            mode=2 if args.mode == "time" else 3,
            max_items=args.max_items,
            max_seconds=args.max_seconds,
            out_dir=args.out_dir,
            reset_state=args.reset_state,
            skip_finished=args.skip_finished,
            require_login=args.require_login,
            strip_urls=args.strip_urls,
            api_robots_policy=args.api_robots_policy,
            init_buvid=args.init_buvid,
            write_source_note=not args.no_source_note,
            on_page=_progress,
        )
    except KeyboardInterrupt:
        print("\n已中断：已采数据与断点已保存，重新运行同一命令即可续采", file=sys.stderr)
        return 130
    except ConfigError as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 1

    merge_report: MergeReport | None = None
    if args.merge_output:
        try:
            merge_report = merge_video_csvs(
                args.out_dir,
                args.merge_output,
                pattern=args.merge_pattern,
                dedupe_rpid=args.merge_dedupe,
                strict=not args.merge_lenient,
            )
        except ConfigError as exc:
            print(f"合并失败：{exc}", file=sys.stderr)
            return 1

    payload = report.as_dict()
    if merge_report is not None:
        payload["merge"] = merge_report.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("\n" + "-" * 55)
        if len(report.videos) <= 50 or args.verbose:
            for video in report.videos:
                if video.error:
                    print(f"[{video.bvid}] 停止：{video.error}")
                    continue
                if video.skipped:
                    print(f"[{video.bvid}] 已完成，跳过（{video.comments_total} 条）")
                    continue
                print(
                    f"[{video.bvid}] {video.title}\n"
                    f"  页数 {video.pages_crawled}；本次新增 {video.comments_new}；"
                    f"累计 {video.comments_total}；耗时 {video.elapsed_seconds:.2f}s"
                    + ("；时间预算用尽，已保存断点" if video.timed_out else "")
                )
        print(
            f"视频：{len(report.videos)} 个（完成 {report.completed} / "
            f"跳过 {report.skipped} / 失败 {report.failed}）"
        )
        print(f"合计新增 {report.total_new} 条；累计 {report.total_saved} 条")
        print(f"总耗时 {report.elapsed_seconds:.2f}s；"
              f"请求 {report.metrics.get('requests', 0)} 次；"
              f"重试 {report.metrics.get('retries', 0)} 次；"
              f"风控 {report.metrics.get('risk_control_hits', 0)} 次")
        if report.source_note_path:
            print(f"来源说明：{report.source_note_path}")
        if merge_report is not None:
            _print_merge(merge_report)
        print("-" * 55)

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
