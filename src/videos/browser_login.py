"""人工登录窗口（spec 2.4，2026-10-17 追加）：由人在真实浏览器里完成 B 站登录。

背景：B 站密码登录会触发极验点选验证码，**不允许 / 不适合由后端自动破解**。
因此「登录账号」的推荐交互是：

1. 前端点「登录 B 站账号」→ ``POST /api/account/login/start``；
2. 后端在**运行服务的那台机器**的桌面上弹出一个真实浏览器窗口（daemon 线程 +
   Playwright，``headless=False``），打开 ``https://passport.bilibili.com/login`` 并置前；
3. 由人完成扫码 / 账号密码 + 验证码登录；
4. 后端每 2 秒读一次浏览器 Cookie，出现 ``SESSDATA`` 后调用
   ``https://api.bilibili.com/x/web-interface/nav`` 取 ``uname`` / ``mid``，
   把 Cookie **只写入** ``outputs/settings.json``（``account.mode="account"`` +
   ``uname`` / ``mid`` 只读缓存），状态置 ``success``，随后自动关闭浏览器；
5. 前端 2 秒轮询 ``GET /api/account/login/status`` 直到终态。

状态机::

    idle → opening → waiting → success
                        ├─ 用户关窗       → cancelled
                        ├─ 超过 300 秒    → timeout
                        └─ 任意异常       → failed（中文可读原因）

安全与兼容：

- Cookie 明文只存在设置文件；**接口 / 日志绝不出现明文**（状态对象只有
  ``has_cookie`` / ``uname`` / ``mid``）；
- Playwright **延迟导入**：未安装不影响应用启动；``start`` 会先做可用性检查并抛
  :class:`LoginDependencyError`（HTTP 503 + 安装指引）；
- 优先使用系统 Edge（``channel="msedge"``，Windows 通常已装），失败再回退内置
  Chromium；两者都不可用时状态 ``failed``，消息带安装命令；
- 同一时间只允许一个登录窗口：``opening`` / ``waiting`` 期间重复 ``start`` 直接返回
  当前状态（幂等）；
- 测试通过构造参数注入**伪浏览器驱动**（``driver_factory``）与伪身份查询
  （``identity_fetcher``），不真正拉起窗口、不触网。
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from . import settings as video_settings
from .store import now_iso

logger = logging.getLogger("src.videos.browser_login")

#: B 站登录页（由人在窗口内完成登录）
LOGIN_URL = "https://passport.bilibili.com/login"

#: 轮询浏览器 Cookie 的间隔（秒，spec 2.4）
POLL_INTERVAL_SECONDS = 2.0

#: 登录窗口最长等待时间（秒，spec 2.4）
TIMEOUT_SECONDS = 300.0

#: 取消时等待后台线程收尾的最长时间（秒）
CANCEL_JOIN_TIMEOUT = 5.0

#: 状态枚举（与 spec 2.4 的状态对象一致）
LOGIN_STATES: tuple[str, ...] = (
    "idle",
    "opening",
    "waiting",
    "success",
    "failed",
    "cancelled",
    "timeout",
)

#: 依赖缺失（HTTP 503）时给前端 / 用户的安装指引
PLAYWRIGHT_INSTALL_HINT = (
    "未安装 Playwright，无法打开人工登录窗口。请在后端所在环境执行："
    "pip install playwright 然后 python -m playwright install chromium"
    "（Windows 通常已装 Edge，也可直接用系统 Edge 打开，无需额外下载）；"
    "若不想安装，可在设置页「高级：手动粘贴 Cookie」中直接填写 Cookie。"
)

LoginState = Literal["idle", "opening", "waiting", "success", "failed", "cancelled", "timeout"]


# --------------------------------------------------------------------------- #
# 异常与状态模型
# --------------------------------------------------------------------------- #
class LoginDependencyError(RuntimeError):
    """Playwright 未安装 / 不可用（HTTP 503，``detail`` 带安装指引）。"""


class LoginWindowError(RuntimeError):
    """打开或操作登录窗口失败（状态落到 ``failed``，原因中文可读）。"""


class AccountLoginStatus(BaseModel):
    """``/api/account/login/*`` 的通用响应状态对象（spec 2.4）。"""

    state: LoginState = "idle"
    message: str = "尚未开始登录"
    uname: str = ""
    mid: int = Field(0, ge=0)
    has_cookie: bool = False
    started_at: str | None = None
    updated_at: str = Field("", description="最近一次状态更新时间（ISO8601）")


# --------------------------------------------------------------------------- #
# 浏览器驱动接口（默认 Playwright 实现；测试注入伪实现）
# --------------------------------------------------------------------------- #
class BrowserDriver(Protocol):
    """登录窗口驱动协议：打开窗口、读 Cookie、判断窗口是否被用户关闭、关闭。"""

    def open(self) -> None:
        """打开登录窗口并置前（阻塞到窗口已打开）。"""

    def get_cookies(self) -> list[dict[str, Any]]:
        """返回当前浏览器上下文的 Cookie 列表（``[{name, value, ...}]``）。"""

    def is_closed(self) -> bool:
        """窗口是否已被用户关闭。"""

    def close(self) -> None:
        """关闭浏览器并释放资源（必须幂等、不抛异常）。"""


def playwright_available() -> bool:
    """Playwright 是否已安装（只查模块是否存在，不触发导入）。"""
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):  # pragma: no cover - 极端打包环境
        return False


def ensure_playwright_available() -> None:
    """依赖检查：未安装时抛 :class:`LoginDependencyError`（HTTP 503）。"""
    if not playwright_available():
        raise LoginDependencyError(PLAYWRIGHT_INSTALL_HINT)


def _extract_cookie(cookies: list[Mapping[str, Any]]) -> str:
    """把 Playwright 的 Cookie 列表拼成 ``name=value; ...`` 串（只保留非空项）。"""
    parts: list[str] = []
    for item in cookies or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _cookie_has_sessdata(cookies: list[Mapping[str, Any]]) -> bool:
    """Cookie 列表里是否已经出现非空 ``SESSDATA``（判断登录完成的唯一依据）。"""
    for item in cookies or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("name") or "").strip() == "SESSDATA" and str(
            item.get("value") or ""
        ).strip():
            return True
    return False


class PlaywrightBrowserDriver:
    """默认驱动：用 Playwright 打开**真实**浏览器窗口（优先系统 Edge，其次 Chromium）。

    注意：Playwright 的同步 API 必须在创建它的线程里使用，因此本类只在后台登录
    线程中构造 / 使用；Playwright 的 import 也延迟到 :meth:`open`（未安装不影响启动）。
    """

    def __init__(self, *, login_url: str = LOGIN_URL, headless: bool = False) -> None:
        self.login_url = login_url
        self.headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def open(self) -> None:
        """启动浏览器、打开登录页并置前。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - start 已做前置检查
            raise LoginDependencyError(PLAYWRIGHT_INSTALL_HINT) from exc

        self._playwright = sync_playwright().start()
        launch_errors: list[str] = []
        for launch_options in (
            {"channel": "msedge", "headless": self.headless},
            {"headless": self.headless},
        ):
            try:
                self._browser = self._playwright.chromium.launch(**launch_options)
                break
            except Exception as exc:  # noqa: BLE001 - 逐个尝试 Edge / 内置 Chromium
                launch_errors.append(f"{launch_options.get('channel', 'chromium')}: {exc}")
        if self._browser is None:
            self.close()
            raise LoginWindowError(
                "无法启动浏览器（系统 Edge 与内置 Chromium 均失败）："
                + "；".join(launch_errors)
                + "。可执行 python -m playwright install chromium 后重试。"
            )

        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        try:
            self._page.goto(self.login_url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - 网络异常也给出可读原因
            raise LoginWindowError(f"打开 B 站登录页失败：{exc}") from exc
        try:
            self._page.bring_to_front()
        except Exception:  # pragma: no cover - 部分环境不支持置前，不影响登录
            pass

    def get_cookies(self) -> list[dict[str, Any]]:
        """读取当前上下文的 Cookie（窗口已关闭 / 异常时返回空列表）。"""
        try:
            if self._context is None:
                return []
            return list(self._context.cookies())
        except Exception:  # noqa: BLE001 - 窗口关闭瞬间读取可能失败，按无 Cookie 处理
            return []

    def is_closed(self) -> bool:
        """窗口是否已被用户关闭（页面 / 浏览器对象失效即视为关闭）。"""
        if self._page is None or self._browser is None:
            return True
        try:
            return bool(self._page.is_closed()) or not bool(self._browser.is_connected())
        except Exception:  # pragma: no cover - 驱动异常按已关闭处理
            return True

    def close(self) -> None:
        """关闭浏览器并释放 Playwright（幂等、绝不抛异常）。"""
        for attribute in ("_page", "_context", "_browser"):
            target = getattr(self, attribute, None)
            close = getattr(target, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响状态机
                    pass
            setattr(self, attribute, None)
        stop = getattr(self._playwright, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                pass
        self._playwright = None


#: 默认身份查询：用 BiliClient 调 nav 接口（测试可注入假实现，避免触网）
def _default_identity_fetcher(cookie: str) -> dict[str, Any]:
    """用已捕获的 Cookie 调 B 站 nav 接口，返回 ``{is_login, uname, mid}``。"""
    from ..crawler.bilibili import BiliClient

    client = BiliClient(
        cookie=cookie,
        delay=0.0,
        timeout=video_settings.SESSION_CHECK_TIMEOUT,
        max_attempts=2,
        respect_robots=False,
        api_robots_policy="ignore",
    )
    try:
        return dict(client.login_status() or {})
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 单例管理器
# --------------------------------------------------------------------------- #
class LoginWindowManager:
    """人工登录窗口的状态机与后台线程（进程内单例）。

    Args:
        driver_factory: 浏览器驱动工厂（``() -> BrowserDriver``）；默认使用
            :class:`PlaywrightBrowserDriver`，测试注入伪驱动。
        identity_fetcher: 身份查询函数（``cookie -> {is_login, uname, mid}``）；
            默认调 B 站 nav 接口，测试注入假实现。
        poll_interval: 轮询 Cookie 的间隔（秒）。
        timeout_seconds: 最长等待时间（秒）。
    """

    def __init__(
        self,
        *,
        driver_factory: Callable[[], BrowserDriver] | None = None,
        identity_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._driver_factory = driver_factory
        self._identity_fetcher = identity_fetcher
        self._poll_interval = max(float(poll_interval), 0.0)
        self._timeout_seconds = max(float(timeout_seconds), 0.0)
        self._state: str = "idle"
        self._message = "尚未开始登录"
        self._started_at: str | None = None
        self._updated_at = now_iso()
        self._uname = ""
        self._mid = 0
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._driver: BrowserDriver | None = None

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        """当前状态对象（含设置里是否已有可用 Cookie；**不含 Cookie 明文**）。"""
        with self._lock:
            has_cookie = bool(video_settings.load_settings()["account"].get("cookie"))
            return {
                "state": self._state,
                "message": self._message,
                "uname": self._uname,
                "mid": self._mid,
                "has_cookie": has_cookie,
                "started_at": self._started_at,
                "updated_at": self._updated_at,
            }

    def start(self) -> dict[str, Any]:
        """打开登录窗口（幂等：等待中重复调用直接返回当前状态）。

        Raises:
            LoginDependencyError: Playwright 未安装（HTTP 503 + 安装指引）。
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._state in (
                "opening",
                "waiting",
            ):
                return self.snapshot()

            factory = self._driver_factory
            if factory is None:
                # 真实浏览器路径：先做同步依赖检查，缺失直接 503（不进入 failed）
                ensure_playwright_available()
                factory = _default_driver_factory

            self._cancel.clear()
            self._uname = ""
            self._mid = 0
            self._started_at = now_iso()
            self._set_state("opening", "正在打开登录窗口…")
            self._thread = threading.Thread(
                target=self._run,
                args=(factory,),
                name="bilibili-login-window",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def cancel(self) -> dict[str, Any]:
        """取消本次登录并关闭窗口（非等待态时幂等返回当前状态）。"""
        with self._lock:
            thread = self._thread
            active = thread is not None and thread.is_alive() and self._state in (
                "opening",
                "waiting",
            )
            self._cancel.set()
            if active:
                self._set_state("waiting", "正在关闭登录窗口…")
        if thread is not None and thread.is_alive():
            thread.join(timeout=CANCEL_JOIN_TIMEOUT)
        return self.snapshot()

    def is_waiting(self) -> bool:
        """是否有正在进行的登录窗口（lifespan 关停 / 测试清理用）。"""
        with self._lock:
            return (
                self._thread is not None
                and self._thread.is_alive()
                and self._state in ("opening", "waiting")
            )

    def shutdown(self) -> None:
        """关停时取消并等待后台线程退出（幂等）。"""
        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=CANCEL_JOIN_TIMEOUT)

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _set_state(self, state: str, message: str) -> None:
        """更新状态与更新时间（调用方须持有 ``self._lock``）。"""
        self._state = state
        self._message = message
        self._updated_at = now_iso()

    def _resolve_identity_fetcher(self) -> Callable[[str], Mapping[str, Any]]:
        return self._identity_fetcher or _default_identity_fetcher

    def _run(self, driver_factory: Callable[[], BrowserDriver]) -> None:
        """后台线程主循环：open → 轮询 Cookie → 成功 / 取消 / 超时 / 失败。"""
        driver: BrowserDriver | None = None
        captured_cookie = ""
        try:
            with self._lock:
                self._set_state("opening", "正在打开登录窗口…")
            driver = driver_factory()
            with self._lock:
                self._driver = driver
            driver.open()
            with self._lock:
                self._set_state(
                    "waiting",
                    "已打开登录窗口，请在运行后端的电脑上完成扫码 / 登录",
                )

            deadline = _deadline_after(self._timeout_seconds)
            cookies: list[Mapping[str, Any]] = []
            while True:
                if self._cancel.wait(self._poll_interval):
                    with self._lock:
                        self._set_state("cancelled", "登录已取消")
                    return
                if driver.is_closed():
                    with self._lock:
                        self._set_state("cancelled", "登录窗口已关闭，登录未完成")
                    return
                if deadline is not None and _now_seconds() >= deadline:
                    with self._lock:
                        self._set_state(
                            "timeout",
                            f"登录窗口等待超时（超过 {int(self._timeout_seconds)} 秒），请重试",
                        )
                    return
                cookies = driver.get_cookies()
                if not _cookie_has_sessdata(cookies):
                    continue

                cookie_header = _extract_cookie(cookies)
                captured_cookie = cookie_header
                status = dict(self._resolve_identity_fetcher()(cookie_header) or {})
                if not status.get("is_login"):
                    with self._lock:
                        self._set_state(
                            "failed",
                            "已获取 Cookie，但登录态校验失败（登录可能未完成或已过期），请重试",
                        )
                    return
                uname = str(status.get("uname") or "")
                try:
                    mid = max(int(status.get("mid") or 0), 0)
                except (TypeError, ValueError):
                    mid = 0
                video_settings.save_account_cookie(cookie_header, uname=uname, mid=mid)
                with self._lock:
                    self._uname = uname
                    self._mid = mid
                    self._set_state("success", f"已登录：{uname}" if uname else "已登录")
                logger.info("人工登录成功并已保存 Cookie（uname=%s, mid=%s）", uname, mid)
                return
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落到 failed（可读原因）
            reason = video_settings.redact_secrets(
                f"打开或操作登录窗口失败：{type(exc).__name__}: {exc}", captured_cookie
            )
            logger.warning("人工登录窗口失败：%s", reason)
            with self._lock:
                self._set_state("failed", reason)
        finally:
            close = getattr(driver, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响状态
                    pass
            with self._lock:
                self._driver = None
                if self._state in ("opening", "waiting"):
                    # 防御：线程意外退出但状态仍是进行中时，落到 failed 而不是永远卡住
                    self._set_state("failed", "登录窗口意外结束，请重试")


def _default_driver_factory() -> BrowserDriver:
    """默认驱动工厂（真实 Playwright 浏览器）。"""
    return PlaywrightBrowserDriver()


def _now_seconds() -> float:
    """单调时钟（秒），用于超时判断。"""
    import time

    return time.monotonic()


def _deadline_after(seconds: float) -> float | None:
    """返回 ``秒`` 之后的单调时钟截止时间（``<=0`` 表示不限制）。"""
    if seconds <= 0:
        return None
    return _now_seconds() + seconds


# --------------------------------------------------------------------------- #
# 进程内单例
# --------------------------------------------------------------------------- #
_manager: LoginWindowManager | None = None
_manager_lock = threading.Lock()


def get_login_manager() -> LoginWindowManager:
    """返回进程内单例登录窗口管理器（首次调用时惰性创建）。"""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = LoginWindowManager()
    return _manager


def peek_login_manager() -> LoginWindowManager | None:
    """返回当前单例但不创建（测试清理用）。"""
    return _manager


def set_login_manager(manager: LoginWindowManager | None) -> None:
    """替换进程内单例（测试注入伪驱动 / 短超时配置；传 ``None`` 即清除）。"""
    global _manager
    with _manager_lock:
        _manager = manager


def shutdown() -> None:
    """关停登录窗口单例（lifespan 退出时调用；幂等）。"""
    manager = peek_login_manager()
    if manager is not None:
        manager.shutdown()
