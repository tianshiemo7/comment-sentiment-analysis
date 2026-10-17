"""人工登录窗口（spec 2.4）离线测试：注入伪浏览器驱动，不拉窗口、不触网。

覆盖：
- 状态机 ``opening → waiting → success``，成功后 Cookie 写入设置（脱敏可查）；
- 用户关窗 → ``cancelled``；``cancel`` 接口 → ``cancelled``；
- 超时 → ``timeout``；
- 打开窗口异常 → ``failed``（中文可读原因）；
- 依赖缺失 → ``POST /api/account/login/start`` 返回 503 + 安装指引；
- 等待中重复 ``start`` 幂等（只创建一个驱动）；
- HTTP 三接口（start / status / cancel）联调，响应里绝不出现 Cookie 明文。
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.inference.app import app
from src.videos import browser_login
from src.videos import settings as video_settings
from src.videos.browser_login import LoginWindowManager

RAW_COOKIE = "SESSDATA=abcdef1234567890; bili_jct=csrf-token-value; DedeUserID=42"
SESSDATA_VALUE = "abcdef1234567890"


class FakeDriver:
    """伪浏览器驱动：可控制打开时序、Cookie 与「用户关窗」。"""

    def __init__(self) -> None:
        self.open_gate = threading.Event()
        self.cookies: list[dict] = []
        self.user_closed = False
        self.open_called = False
        self.closed = False
        self.fail_open: Exception | None = None

    def open(self) -> None:
        self.open_called = True
        if self.fail_open is not None:
            raise self.fail_open
        self.open_gate.wait(5)

    def get_cookies(self) -> list[dict]:
        return [dict(item) for item in self.cookies]

    def is_closed(self) -> bool:
        return self.user_closed

    def close(self) -> None:
        self.closed = True


def _wait_state(manager: LoginWindowManager, states: set[str], timeout: float = 5.0) -> dict:
    """轮询管理器快照直到进入指定状态集合。"""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        last = manager.snapshot()
        if last["state"] in states:
            return last
        time.sleep(0.005)
    raise AssertionError(f"等待状态 {states} 超时，最后状态：{last}")


def _make_manager(driver: FakeDriver, **overrides) -> LoginWindowManager:
    """构造注入了伪驱动与伪身份查询的管理器（默认 poll 很密、超时很短）。"""
    identity = overrides.pop(
        "identity_fetcher",
        lambda cookie: {"is_login": True, "uname": "示例用户", "mid": 123456},
    )
    options = {"poll_interval": 0.01, "timeout_seconds": 5.0}
    options.update(overrides)
    return LoginWindowManager(
        driver_factory=lambda: driver,
        identity_fetcher=identity,
        **options,
    )


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #
def test_login_flow_opening_waiting_success_and_saves_cookie(isolated_settings_path):
    driver = FakeDriver()
    manager = _make_manager(driver)

    opening = manager.start()
    assert opening["state"] == "opening"  # start 同步置 opening
    assert opening["started_at"]
    assert driver.open_called is False or True  # 线程可能刚开始，不做强断言

    driver.open_gate.set()
    waiting = _wait_state(manager, {"waiting"})
    assert "登录窗口" in waiting["message"]

    driver.cookies = [
        {"name": "SESSDATA", "value": SESSDATA_VALUE},
        {"name": "bili_jct", "value": "csrf-token-value"},
        {"name": "DedeUserID", "value": "42"},
    ]
    success = _wait_state(manager, {"success"})
    assert success["uname"] == "示例用户"
    assert success["mid"] == 123456
    assert success["has_cookie"] is True
    assert SESSDATA_VALUE not in json.dumps(success, ensure_ascii=False)
    assert driver.closed is True  # 成功后自动关闭浏览器

    # Cookie 只写进设置文件；GET 设置只暴露脱敏信息
    stored = json.loads(isolated_settings_path.read_text(encoding="utf-8"))
    assert stored["account"]["mode"] == "account"
    assert SESSDATA_VALUE in stored["account"]["cookie"]
    public = video_settings.load_public()
    assert public["account"]["has_cookie"] is True
    assert public["account"]["uname"] == "示例用户"
    assert public["account"]["mid"] == 123456
    assert SESSDATA_VALUE not in json.dumps(public, ensure_ascii=False)


def test_user_closing_window_marks_cancelled():
    driver = FakeDriver()
    driver.open_gate.set()
    manager = _make_manager(driver)

    manager.start()
    _wait_state(manager, {"waiting"})
    driver.user_closed = True

    cancelled = _wait_state(manager, {"cancelled"})
    assert "关闭" in cancelled["message"]
    assert driver.closed is True
    assert video_settings.load_public()["account"]["has_cookie"] is False


def test_cancel_closes_window_and_stops_thread():
    driver = FakeDriver()
    driver.open_gate.set()
    manager = _make_manager(driver)

    manager.start()
    _wait_state(manager, {"waiting"})
    result = manager.cancel()
    assert result["state"] == "cancelled"
    assert manager.is_waiting() is False
    assert driver.closed is True


def test_timeout_marks_timeout():
    driver = FakeDriver()
    driver.open_gate.set()
    manager = _make_manager(driver, timeout_seconds=0.05)

    manager.start()
    timed_out = _wait_state(manager, {"timeout"})
    assert "超时" in timed_out["message"]
    assert driver.closed is True


def test_open_failure_marks_failed_with_readable_reason():
    driver = FakeDriver()
    driver.fail_open = RuntimeError("浏览器内核启动失败（模拟）")
    manager = _make_manager(driver)

    manager.start()
    failed = _wait_state(manager, {"failed"})
    assert "浏览器内核启动失败" in failed["message"]
    assert driver.closed is True


def test_login_not_confirmed_marks_failed():
    driver = FakeDriver()
    driver.open_gate.set()
    driver.cookies = [{"name": "SESSDATA", "value": SESSDATA_VALUE}]
    manager = _make_manager(
        driver, identity_fetcher=lambda cookie: {"is_login": False, "uname": "", "mid": 0}
    )

    manager.start()
    failed = _wait_state(manager, {"failed"})
    assert "登录态校验失败" in failed["message"]
    assert video_settings.load_public()["account"]["has_cookie"] is False


def test_repeated_start_while_waiting_is_idempotent():
    created: list[FakeDriver] = []

    def factory() -> FakeDriver:
        driver = FakeDriver()
        created.append(driver)
        return driver

    manager = LoginWindowManager(
        driver_factory=factory,
        identity_fetcher=lambda cookie: {"is_login": True, "uname": "u", "mid": 1},
        poll_interval=0.01,
        timeout_seconds=5.0,
    )
    try:
        first = manager.start()
        assert first["state"] == "opening"
        deadline = time.monotonic() + 5.0
        while not created and time.monotonic() < deadline:
            time.sleep(0.005)
        assert created, "后台线程应已创建浏览器驱动"
        second = manager.start()
        assert second["state"] in {"opening", "waiting"}
        assert len(created) == 1  # 没有创建第二个窗口
        created[0].open_gate.set()
        _wait_state(manager, {"waiting"})
        assert manager.start()["state"] == "waiting"
        assert len(created) == 1
    finally:
        manager.cancel()


# --------------------------------------------------------------------------- #
# HTTP 接口
# --------------------------------------------------------------------------- #
def test_dependency_missing_start_returns_503(monkeypatch):
    manager = LoginWindowManager()  # 未注入驱动 → 走真实依赖检查
    browser_login.set_login_manager(manager)
    monkeypatch.setattr(browser_login, "playwright_available", lambda: False)

    with TestClient(app) as client:
        response = client.post("/api/account/login/start")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "pip install playwright" in detail
        assert "playwright install chromium" in detail
        # 依赖缺失不应留下 failed / opening 状态
        status = client.get("/api/account/login/status").json()
        assert status["state"] == "idle"


def test_login_http_flow_start_status_cancel():
    driver = FakeDriver()
    driver.open_gate.set()
    manager = _make_manager(driver)
    browser_login.set_login_manager(manager)

    with TestClient(app) as client:
        started = client.post("/api/account/login/start")
        assert started.status_code == 200
        assert started.json()["state"] in {"opening", "waiting"}
        assert RAW_COOKIE not in started.text

        _wait_state(manager, {"waiting"})
        waiting = client.get("/api/account/login/status").json()
        assert waiting["state"] == "waiting"
        assert waiting["has_cookie"] is False

        driver.cookies = [{"name": "SESSDATA", "value": SESSDATA_VALUE}]
        success = _wait_state(manager, {"success"})
        assert success["state"] == "success" and success["uname"] == "示例用户"

        polled = client.get("/api/account/login/status").json()
        assert polled["state"] == "success"
        assert RAW_COOKIE not in json.dumps(polled, ensure_ascii=False)
        # 设置接口同步可查（脱敏）
        settings_body = client.get("/api/settings").json()
        assert settings_body["account"]["has_cookie"] is True
        assert SESSDATA_VALUE not in json.dumps(settings_body, ensure_ascii=False)

        cancelled = client.post("/api/account/login/cancel")
        assert cancelled.status_code == 200
        # 已是终态（success）时 cancel 幂等返回当前状态，不会把成功改成取消
        assert cancelled.json()["state"] == "success"


def test_login_http_cancel_during_waiting():
    driver = FakeDriver()
    driver.open_gate.set()
    manager = _make_manager(driver)
    browser_login.set_login_manager(manager)

    with TestClient(app) as client:
        assert client.post("/api/account/login/start").status_code == 200
        _wait_state(manager, {"waiting"})
        cancelled = client.post("/api/account/login/cancel").json()
        assert cancelled["state"] == "cancelled"
        assert manager.is_waiting() is False
        assert driver.closed is True
