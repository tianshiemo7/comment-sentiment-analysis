"""系统设置与账号接口测试（`/api/settings`、`/api/crawler/session/check`）。

全部离线：设置文件由 `conftest.isolated_settings_path` 重定向到 ``tmp_path``，
登录态检查注入假客户端（`video_settings.set_session_client_factory`），**绝不触网**。

覆盖 spec 2.1 / 2.2 / 2.3 与第 4 节第 7 条的设置部分：

- 默认值、部分更新合并、Cookie 脱敏（响应里绝不出现明文）；
- 清除凭据（``cookie: ""`` / ``mode: "anonymous"``）与账号模式缺凭据 → 400；
- 数值越界 / 枚举非法 / 类型不对 → 400；
- 破坏的设置文件回退默认值；``CSA_SETTINGS_PATH`` 覆盖生效；原子写不留 ``.tmp``；
- session check：匿名 200、成功缓存 uname/mid、异常 502、错误信息脱敏。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.inference.app import app
from src.videos import settings as video_settings

RAW_COOKIE = "SESSDATA=abcdef1234567890; bili_jct=csrf-token-value; DedeUserID=42"
SESSDATA_VALUE = "abcdef1234567890"


@pytest.fixture
def client():
    """只测设置路由，不需要 lifespan（不启动后台流水线线程）。"""
    return TestClient(app)


class FakeSessionClient:
    """假的登录态客户端（不触网）。"""

    def __init__(self, *, is_login: bool = True, uname: str = "示例用户", mid: int = 123456):
        self._status = {"is_login": is_login, "uname": uname, "mid": mid}
        self.closed = False

    def login_status(self) -> dict:
        return dict(self._status)

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- #
# GET / PUT 默认值与部分更新
# --------------------------------------------------------------------------- #
def test_get_settings_returns_contract_defaults(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"account", "crawl", "pipeline", "updated_at"}
    assert set(body["account"]) == {
        "mode",
        "has_cookie",
        "masked_cookie",
        "uname",
        "mid",
    }
    assert body["account"] == {
        "mode": "anonymous",
        "has_cookie": False,
        "masked_cookie": "",
        "uname": "",
        "mid": 0,
    }
    # 默认值即 spec 第 2 节
    assert body["crawl"] == {
        "real_crawl": True,
        "max_pages": 1,
        "max_sub_pages": 1,
        "page_size": 20,
        "include_replies": False,
        "sort_mode": "time",
        "max_items": 200,
        "delay_seconds": 2.5,
        "timeout_seconds": 15.0,
        "require_login": False,
        "init_buvid": False,
    }
    assert body["pipeline"] == {"auto_clean": False, "auto_analyze": False}
    assert body["updated_at"] == ""


def test_put_settings_partial_update_merges(client):
    first = client.put(
        "/api/settings",
        json={"crawl": {"max_pages": 3, "include_replies": True}},
    )
    assert first.status_code == 200
    crawl = first.json()["crawl"]
    assert crawl["max_pages"] == 3 and crawl["include_replies"] is True
    assert crawl["page_size"] == 20  # 未提供的字段保持不变

    second = client.put("/api/settings", json={"pipeline": {"auto_clean": True}})
    assert second.status_code == 200
    body = second.json()
    assert body["pipeline"] == {"auto_clean": True, "auto_analyze": False}
    assert body["crawl"]["max_pages"] == 3  # 上一轮保存的值仍在
    assert body["updated_at"]  # 保存后带时间戳

    # GET 与 PUT 返回一致
    assert client.get("/api/settings").json() == body


def test_put_settings_cookie_is_masked_and_never_returned(client):
    response = client.put(
        "/api/settings",
        json={"account": {"mode": "account", "cookie": RAW_COOKIE}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["mode"] == "account"
    assert body["account"]["has_cookie"] is True
    masked = body["account"]["masked_cookie"]
    assert masked and "…" in masked
    assert masked == f"SESSD…{SESSDATA_VALUE[-4:]}"
    # 响应（GET / PUT）绝不能出现 Cookie 明文
    raw_text = response.text + client.get("/api/settings").text
    assert RAW_COOKIE not in raw_text
    assert SESSDATA_VALUE not in raw_text
    assert "csrf-token-value" not in raw_text


def test_put_settings_accepts_bare_sessdata_value(client):
    body = client.put("/api/settings", json={"account": {"cookie": SESSDATA_VALUE}}).json()
    assert body["account"]["mode"] == "account"  # 传了 Cookie 自动切账号模式
    assert body["account"]["has_cookie"] is True

    stored = json.loads(video_settings.settings_path().read_text(encoding="utf-8"))
    assert stored["account"]["cookie"] == f"SESSDATA={SESSDATA_VALUE}"
    assert stored["account"]["mode"] == "account"


def test_put_settings_cookie_rejects_value_without_sessdata(client):
    response = client.put(
        "/api/settings", json={"account": {"cookie": "bili_jct=csrf-only"}}
    )
    assert response.status_code == 400
    assert "SESSDATA" in response.json()["detail"]


def test_put_settings_clears_credentials_via_anonymous_and_empty_cookie(client):
    client.put("/api/settings", json={"account": {"cookie": RAW_COOKIE}})
    cleared = client.put("/api/settings", json={"account": {"mode": "anonymous"}})
    assert cleared.status_code == 200
    assert cleared.json()["account"] == {
        "mode": "anonymous",
        "has_cookie": False,
        "masked_cookie": "",
        "uname": "",
        "mid": 0,
    }

    client.put("/api/settings", json={"account": {"cookie": RAW_COOKIE}})
    cleared_by_empty = client.put("/api/settings", json={"account": {"cookie": ""}})
    assert cleared_by_empty.status_code == 200
    assert cleared_by_empty.json()["account"]["has_cookie"] is False
    # cookie 置空但模式仍要求账号 → 400
    rejected = client.put(
        "/api/settings", json={"account": {"cookie": "", "mode": "account"}}
    )
    assert rejected.status_code == 400


def test_put_settings_account_mode_without_cookie_returns_400(client):
    response = client.put("/api/settings", json={"account": {"mode": "account"}})
    assert response.status_code == 400
    assert "Cookie" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"crawl": {"max_pages": 51}}, "max_pages"),
        ({"crawl": {"max_pages": -1}}, "max_pages"),
        ({"crawl": {"max_sub_pages": 21}}, "max_sub_pages"),
        ({"crawl": {"page_size": 0}}, "page_size"),
        ({"crawl": {"page_size": 51}}, "page_size"),
        ({"crawl": {"max_items": -1}}, "max_items"),
        ({"crawl": {"delay_seconds": 1.9}}, "delay_seconds"),
        ({"crawl": {"delay_seconds": 61}}, "delay_seconds"),
        ({"crawl": {"timeout_seconds": 0}}, "timeout_seconds"),
        ({"crawl": {"sort_mode": "newest"}}, "sort_mode"),
        ({"crawl": {"include_replies": "yes"}}, "include_replies"),
        ({"pipeline": {"auto_clean": 1}}, "auto_clean"),
        ({"account": {"mode": "root"}}, "account.mode"),
        ({"account": "not-an-object"}, "account"),
        ({"crawl": "not-an-object"}, "crawl"),
    ],
)
def test_put_settings_invalid_value_returns_400(client, payload, field):
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 400, response.text
    assert field in response.json()["detail"]


def test_corrupted_settings_file_falls_back_to_defaults(client):
    path = video_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    body = client.get("/api/settings").json()
    assert body["account"]["mode"] == "anonymous"
    assert body["crawl"]["max_pages"] == 1

    # 越界字段写坏 → 该字段回退默认，其它字段仍生效
    path.write_text(
        json.dumps(
            {
                "account": {"mode": "anonymous", "cookie": ""},
                "crawl": {"max_pages": 999, "include_replies": True},
                "pipeline": {"auto_clean": True},
            }
        ),
        encoding="utf-8",
    )
    body = client.get("/api/settings").json()
    assert body["crawl"]["max_pages"] == 1
    assert body["crawl"]["include_replies"] is True
    assert body["pipeline"]["auto_clean"] is True


def test_settings_path_env_override_and_atomic_write(client):
    path = video_settings.settings_path()
    assert path == video_settings.settings_path()  # 由 CSA_SETTINGS_PATH 指向 tmp_path
    assert "settings.json" in str(path)

    client.put("/api/settings", json={"crawl": {"max_pages": 2}})
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()  # 原子写不留临时文件
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["crawl"]["max_pages"] == 2


# --------------------------------------------------------------------------- #
# 脱敏工具单元测试
# --------------------------------------------------------------------------- #
def test_mask_and_redact_helpers():
    assert video_settings.masked_cookie("") == ""
    assert video_settings.masked_cookie("SESSDATA=abcdef1234567890") == "SESSD…7890"
    # 过短的值只保留名字前缀，绝不暴露完整值
    assert video_settings.masked_cookie("SESSDATA=abc") == "SESSD…"
    assert video_settings.mask_secret("abc") == "ab…bc"

    text = video_settings.redact_secrets(
        f"SESSDATA={SESSDATA_VALUE} 在错误信息里", f"SESSDATA={SESSDATA_VALUE}"
    )
    assert SESSDATA_VALUE not in text
    assert "…" in text
    # 空 Cookie 时不改变原文
    assert video_settings.redact_secrets("普通错误", "") == "普通错误"


# --------------------------------------------------------------------------- #
# POST /api/crawler/session/check
# --------------------------------------------------------------------------- #
def test_session_check_without_credentials_is_anonymous_200(client):
    response = client.post("/api/crawler/session/check")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "mode": "anonymous",
        "has_credentials": False,
        "is_login": False,
        "uname": "",
        "mid": 0,
        "message": "未配置登录凭据，当前为匿名模式",
    }


def test_session_check_with_temporary_cookie_succeeds_and_caches_identity(client):
    fake = FakeSessionClient()
    video_settings.set_session_client_factory(lambda *, cookie: fake)

    response = client.post(
        "/api/crawler/session/check", json={"cookie": RAW_COOKIE}
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "mode": "account",
        "has_credentials": True,
        "is_login": True,
        "uname": "示例用户",
        "mid": 123456,
        "message": "已登录：示例用户",
    }
    assert fake.closed is True  # 检查完必须关闭客户端
    assert RAW_COOKIE not in response.text

    # 登录成功把 uname / mid 缓存进设置文件（只读字段）
    settings_body = client.get("/api/settings").json()
    assert settings_body["account"]["uname"] == "示例用户"
    assert settings_body["account"]["mid"] == 123456


def test_session_check_with_saved_cookie_uses_it(client):
    client.put("/api/settings", json={"account": {"cookie": RAW_COOKIE}})
    captured: dict = {}

    def factory(*, cookie):
        captured["cookie"] = cookie
        return FakeSessionClient(is_login=True, uname="已保存用户", mid=7)

    video_settings.set_session_client_factory(factory)
    body = client.post("/api/crawler/session/check").json()
    assert captured["cookie"] == RAW_COOKIE
    assert body["is_login"] is True and body["uname"] == "已保存用户"
    assert RAW_COOKIE not in json.dumps(body, ensure_ascii=False)


def test_session_check_invalid_login_returns_anonymous_200(client):
    video_settings.set_session_client_factory(
        lambda *, cookie: FakeSessionClient(is_login=False, uname="", mid=0)
    )
    body = client.post("/api/crawler/session/check", json={"cookie": RAW_COOKIE}).json()
    assert body["mode"] == "anonymous"
    assert body["has_credentials"] is True
    assert body["is_login"] is False
    assert body["uname"] == "" and body["mid"] == 0
    assert "未登录" in body["message"]


def test_session_check_network_error_returns_502_with_redacted_reason(client):
    def boom(*, cookie):
        raise RuntimeError(f"连接超时（Cookie: {cookie}）")

    video_settings.set_session_client_factory(boom)
    response = client.post(
        "/api/crawler/session/check", json={"cookie": RAW_COOKIE}
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "检查登录状态失败" in detail
    assert SESSDATA_VALUE not in detail  # 错误信息必须脱敏
    assert "…" in detail


def test_session_check_does_not_touch_network_when_no_credentials(client, monkeypatch):
    """没有凭据时不应创建任何客户端（离线保证）。"""

    def unexpected(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("没有凭据时不应创建客户端")

    video_settings.set_session_client_factory(unexpected)
    assert client.post("/api/crawler/session/check").status_code == 200
