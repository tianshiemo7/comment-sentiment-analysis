"""系统设置（账号 / 爬虫参数 / 自动流程）的服务端唯一真相。

设计依据：`docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md`
第 2 节。要点：

1. **落盘**：默认 ``outputs/settings.json``（已被 ``.gitignore`` 的 ``outputs/``
   规则覆盖），可用环境变量 ``CSA_SETTINGS_PATH`` 覆盖；原子写入（先写 ``.tmp``
   再 :func:`os.replace`），写盘失败只记日志、**不抛给 HTTP**；
2. **Cookie 明文只存在该文件**：``GET /api/settings`` 只返回 ``has_cookie`` /
   ``masked_cookie``（形如 ``SESSD…9f2a``）；任何日志、错误与任务索引都不得出现
   完整 Cookie（本模块提供 :func:`redact_secrets` 兜底）；
3. **默认值**：设置文件不存在 / 损坏 / 字段非法时一律回退默认值（匿名、真实采集、
   关闭自动连续），绝不因为设置文件写坏导致服务起不来；
4. **PUT 部分更新**：只做浅层嵌套合并，缺省字段保持不变；数值越界 / 枚举非法 /
   账号模式缺凭据统一抛 :class:`SettingsValidationError`（HTTP 层映射为 **400**）；
5. **登录态检查**：:func:`check_session` 调用 ``BiliClient.login_status``；未配置
   凭据直接返回匿名态（不报错）；网络 / 接口异常抛 :class:`SessionCheckError`
   （HTTP 502）。测试可注入假客户端工厂，**全程离线**。

文件结构（``GET /api/settings`` 的响应即内部结构去掉 Cookie 明文）::

    {
      "account": {"mode": "anonymous", "cookie": "", "uname": "", "mid": 0},
      "crawl": {...},
      "pipeline": {"auto_clean": false, "auto_analyze": false},
      "updated_at": "2026-10-17T18:00:00+08:00"
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .store import now_iso

logger = logging.getLogger("src.videos.settings")

# --------------------------------------------------------------------------- #
# 常量与默认值（默认值即契约，见 spec 第 2 节）
# --------------------------------------------------------------------------- #
#: 设置文件路径的环境变量覆盖名
ENV_SETTINGS_PATH = "CSA_SETTINGS_PATH"

#: 默认落盘位置（`outputs/` 整体不入库，见 .gitignore）
DEFAULT_SETTINGS_PATH = Path("outputs") / "settings.json"

#: 账号模式枚举
ACCOUNT_MODES: tuple[str, ...] = ("anonymous", "account")

#: 一级评论排序模式枚举（映射到爬虫 mode：time→2 / hot→3）
SORT_MODES: tuple[str, ...] = ("time", "hot")

#: 登录态检查的单次请求超时（秒；检查接口要快速失败，不跟大规模采集共用 15s）
SESSION_CHECK_TIMEOUT = 10.0

#: 爬虫参数默认值（spec 第 2 节，逐字段锁定）
DEFAULT_CRAWL: dict[str, Any] = {
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

#: 自动流程默认值（关闭自动连续：在阶段边界暂停等待用户操作）
DEFAULT_PIPELINE: dict[str, Any] = {
    "auto_clean": False,
    "auto_analyze": False,
}

#: `crawl` 块每个字段的取值范围（用于校验与错误文案）
CRAWL_INT_RANGES: dict[str, tuple[int, int | None]] = {
    "max_pages": (0, 50),
    "max_sub_pages": (0, 20),
    "page_size": (1, 50),
    "max_items": (0, None),
}

#: `crawl` 块布尔字段
CRAWL_BOOL_FIELDS: tuple[str, ...] = ("real_crawl", "include_replies", "require_login", "init_buvid")

#: `crawl` 块枚举字段
CRAWL_ENUM_FIELDS: dict[str, tuple[str, ...]] = {"sort_mode": SORT_MODES}


# --------------------------------------------------------------------------- #
# 异常与 Pydantic 响应模型
# --------------------------------------------------------------------------- #
class SettingsValidationError(ValueError):
    """设置请求非法（HTTP 400，``detail`` 为中文可读提示）。"""


class SessionCheckError(RuntimeError):
    """登录态检查失败（网络 / 接口异常，HTTP 502）。"""


class AccountView(BaseModel):
    """``GET/PUT /api/settings`` 的 ``account`` 块（Cookie 已脱敏，只读字段）。"""

    mode: Literal["anonymous", "account"] = "anonymous"
    has_cookie: bool = Field(False, description="是否已保存 Cookie（只读）")
    masked_cookie: str = Field("", description="脱敏后的 Cookie，形如 SESSD…9f2a（只读）")
    uname: str = Field("", description="最近一次检查登录态成功的昵称（只读缓存）")
    mid: int = Field(0, ge=0, description="最近一次检查登录态成功的 mid（只读缓存）")


class CrawlSettingsModel(BaseModel):
    """``GET/PUT /api/settings`` 的 ``crawl`` 块（默认值即 spec 第 2 节）。"""

    real_crawl: bool = True
    max_pages: int = Field(1, ge=0, le=50, description="0~50，0=不限")
    max_sub_pages: int = Field(1, ge=0, le=20, description="0~20，0=不限")
    page_size: int = Field(20, ge=1, le=50)
    include_replies: bool = False
    sort_mode: Literal["time", "hot"] = "time"
    max_items: int = Field(200, ge=0, description="≥0，0=不限")
    delay_seconds: float = Field(2.5, ge=2.0, le=60.0)
    timeout_seconds: float = Field(15.0, gt=0.0)
    require_login: bool = False
    init_buvid: bool = False


class PipelineSettingsModel(BaseModel):
    """``GET/PUT /api/settings`` 的 ``pipeline`` 块（自动连续开关）。"""

    auto_clean: bool = False
    auto_analyze: bool = False


class SettingsResponse(BaseModel):
    """``GET /api/settings`` 与 ``PUT /api/settings`` 的响应（Cookie 脱敏）。"""

    account: AccountView
    crawl: CrawlSettingsModel
    pipeline: PipelineSettingsModel
    updated_at: str = Field("", description="最近一次保存时间（ISO8601；文件不存在时为空串）")


class SessionCheckRequest(BaseModel):
    """``POST /api/crawler/session/check`` 请求体（可省略）。"""

    cookie: str | None = Field(None, description="可选：用临时凭据检查（尚未保存时使用）")


class SessionCheckResponse(BaseModel):
    """``POST /api/crawler/session/check`` 响应。"""

    mode: Literal["anonymous", "account"]
    has_credentials: bool
    is_login: bool
    uname: str = ""
    mid: int = 0
    message: str


# --------------------------------------------------------------------------- #
# 校验工具（错误信息中文可读，字段名带 `crawl.` / `pipeline.` 前缀）
# --------------------------------------------------------------------------- #
def _as_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SettingsValidationError(f"{path} 必须是对象")
    return value


def _as_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsValidationError(f"{path} 必须是布尔值（true / false）")
    return value


def _as_int(value: Any, path: str, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsValidationError(f"{path} 必须是整数")
    if isinstance(value, float):
        if not value.is_integer():
            raise SettingsValidationError(f"{path} 必须是整数")
        value = int(value)
    value = int(value)
    if value < minimum:
        if maximum is None:
            raise SettingsValidationError(f"{path} 不能小于 {minimum}")
        raise SettingsValidationError(f"{path} 必须在 {minimum}~{maximum} 之间")
    if maximum is not None and value > maximum:
        raise SettingsValidationError(f"{path} 必须在 {minimum}~{maximum} 之间")
    return value


def _as_float(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: bool = False,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsValidationError(f"{path} 必须是数字")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        raise SettingsValidationError(f"{path} 必须是有限数字")
    if minimum is not None:
        if exclusive_minimum and number <= minimum:
            raise SettingsValidationError(f"{path} 必须大于 {minimum:g}")
        if not exclusive_minimum and number < minimum:
            raise SettingsValidationError(f"{path} 必须在 {minimum:g}~{maximum:g}" if maximum is not None else f"{path} 不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise SettingsValidationError(f"{path} 必须在 {minimum:g}~{maximum:g} 之间")
    return number


def _as_enum(value: Any, path: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SettingsValidationError(
            f"{path} 只能是 {' / '.join(allowed)} 之一（当前：{value!r}）"
        )
    return value


def _validate_crawl_value(name: str, value: Any) -> Any:
    """按字段名校验一个 `crawl` 字段，返回规范化后的值（非法抛 400 异常）。"""
    path = f"crawl.{name}"
    if name in CRAWL_BOOL_FIELDS:
        return _as_bool(value, path)
    if name in CRAWL_INT_RANGES:
        minimum, maximum = CRAWL_INT_RANGES[name]
        return _as_int(value, path, minimum, maximum)
    if name in CRAWL_ENUM_FIELDS:
        return _as_enum(value, path, CRAWL_ENUM_FIELDS[name])
    if name == "delay_seconds":
        return _as_float(value, path, minimum=2.0, maximum=60.0)
    if name == "timeout_seconds":
        return _as_float(value, path, minimum=0.0, exclusive_minimum=True)
    raise SettingsValidationError(f"未知的采集参数：{name}")  # pragma: no cover - 防御性


# --------------------------------------------------------------------------- #
# Cookie 规范化、脱敏与泄密兜底
# --------------------------------------------------------------------------- #
def _cookie_parts(cookie: str) -> dict[str, str]:
    """把 Cookie 串解析成 ``{name: value}``（忽略空项与无等号项）。"""
    parts: dict[str, str] = {}
    for chunk in str(cookie or "").split(";"):
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            parts[name] = value
    return parts


def normalize_cookie(raw: str) -> str:
    """规范化用户输入的 Cookie：允许只填 SESSDATA 值，也允许完整 Cookie 串。

    Args:
        raw: 用户输入（``SESSDATA=...`` 完整串 / 裸 SESSDATA 值）。

    Returns:
        规范化后的 Cookie 串（去首尾空白；裸值补成 ``SESSDATA=<value>``）。

    Raises:
        SettingsValidationError: 缺少有效 ``SESSDATA``（登录态必需）。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if "=" not in text.split(";")[0]:
        text = f"SESSDATA={text}"
    parts = _cookie_parts(text)
    if not parts.get("SESSDATA"):
        raise SettingsValidationError(
            "Cookie 中缺少有效的 SESSDATA，请从浏览器复制完整 Cookie 或只粘贴 SESSDATA 的值"
        )
    return text


def mask_secret(value: str) -> str:
    """把敏感串脱敏成 ``前5…后4`` 形式（过短时用更保守的遮罩，绝不含完整值）。"""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 2:
        return "…"
    if len(text) <= 9:
        return f"{text[:2]}…{text[-2:]}"
    return f"{text[:5]}…{text[-4:]}"


def masked_cookie(cookie: str) -> str:
    """返回 Cookie 的脱敏展示（形如 ``SESSD…9f2a``：只露名字前缀与值末 4 位）。"""
    parts = _cookie_parts(cookie)
    secret = parts.get("SESSDATA") or next(iter(parts.values()), "")
    if not secret:
        return ""
    if len(secret) <= 4:
        return "SESSD…"
    return f"SESSD…{secret[-4:]}"


def redact_secrets(text: str, cookie: str) -> str:
    """把文本中出现的完整 Cookie / SESSDATA 值替换成脱敏形式（日志与错误兜底）。

    任何可能把异常原文写进任务 ``error`` / HTTP ``detail`` 的地方都应调用它，
    保证「任何日志、错误、索引文件都不得出现完整 Cookie」。
    """
    result = str(text or "")
    if not result or not cookie:
        return result
    secrets = {str(cookie).strip(), *_cookie_parts(cookie).values()}
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        if secret in result:
            result = result.replace(secret, mask_secret(secret))
    return result


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def settings_path() -> Path:
    """返回设置文件路径：``CSA_SETTINGS_PATH`` 优先，否则默认 ``outputs/settings.json``。"""
    override = os.getenv(ENV_SETTINGS_PATH)
    return Path(override) if override and override.strip() else Path(DEFAULT_SETTINGS_PATH)


def default_settings() -> dict[str, Any]:
    """返回一份全新的内部默认设置（含空 Cookie；调用方可随意修改）。"""
    return {
        "account": {"mode": "anonymous", "cookie": "", "uname": "", "mid": 0},
        "crawl": dict(DEFAULT_CRAWL),
        "pipeline": dict(DEFAULT_PIPELINE),
        "updated_at": "",
    }


def _sanitize_account(raw: Any) -> dict[str, Any]:
    account = deepcopy(default_settings()["account"])
    if not isinstance(raw, Mapping):
        return account
    mode = raw.get("mode")
    if isinstance(mode, str) and mode in ACCOUNT_MODES:
        account["mode"] = mode
    cookie = raw.get("cookie")
    if isinstance(cookie, str):
        account["cookie"] = cookie.strip()
    uname = raw.get("uname")
    if isinstance(uname, str):
        account["uname"] = uname
    mid = raw.get("mid")
    if isinstance(mid, (int, float)) and not isinstance(mid, bool) and int(mid) >= 0:
        account["mid"] = int(mid)
    if account["mode"] == "anonymous":
        # 匿名模式下凭据不应继续生效（防御：手工改坏设置文件也不泄密）
        account["cookie"] = ""
    return account


def _sanitize_section(raw: Any, defaults: dict[str, Any], validate: Callable[[str, Any], Any]) -> dict[str, Any]:
    """把落盘的一段设置按字段校验；非法字段回退默认值（不抛异常）。"""
    result = dict(defaults)
    if not isinstance(raw, Mapping):
        return result
    for name in defaults:
        if name not in raw:
            continue
        try:
            result[name] = validate(name, raw[name])
        except SettingsValidationError:
            continue
    return result


def load_settings() -> dict[str, Any]:
    """读取并清洗内部设置（含 Cookie 明文）；文件缺失 / 损坏时返回默认值。"""
    settings = default_settings()
    path = settings_path()
    try:
        if not path.is_file():
            return settings
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("设置文件无法读取，已回退默认值：%s", path)
        return settings
    if not isinstance(payload, Mapping):
        return settings

    settings["account"] = _sanitize_account(payload.get("account"))
    settings["crawl"] = _sanitize_section(
        payload.get("crawl"), DEFAULT_CRAWL, _validate_crawl_value
    )
    settings["pipeline"] = _sanitize_section(
        payload.get("pipeline"),
        DEFAULT_PIPELINE,
        lambda name, value: _as_bool(value, f"pipeline.{name}"),
    )
    updated_at = payload.get("updated_at")
    if isinstance(updated_at, str):
        settings["updated_at"] = updated_at
    return settings


def public_view(settings: Mapping[str, Any]) -> dict[str, Any]:
    """内部设置 → 对外响应（Cookie 只以 ``has_cookie`` / ``masked_cookie`` 出现）。"""
    account = settings.get("account") or {}
    cookie = str(account.get("cookie") or "")
    crawl_raw = settings.get("crawl") or {}
    pipeline_raw = settings.get("pipeline") or {}
    crawl = dict(DEFAULT_CRAWL)
    crawl.update({key: crawl_raw[key] for key in DEFAULT_CRAWL if key in crawl_raw})
    pipeline = dict(DEFAULT_PIPELINE)
    pipeline.update({key: pipeline_raw[key] for key in DEFAULT_PIPELINE if key in pipeline_raw})
    return {
        "account": {
            "mode": account.get("mode") if account.get("mode") in ACCOUNT_MODES else "anonymous",
            "has_cookie": bool(cookie),
            "masked_cookie": masked_cookie(cookie),
            "uname": str(account.get("uname") or ""),
            "mid": int(account.get("mid") or 0),
        },
        "crawl": crawl,
        "pipeline": pipeline,
        "updated_at": str(settings.get("updated_at") or ""),
    }


def load_public() -> dict[str, Any]:
    """读取设置并直接返回可下发结构（``GET /api/settings`` 用）。"""
    return public_view(load_settings())


def _save_settings(settings: Mapping[str, Any]) -> None:
    """原子写盘（权限尽量 0o600）；失败只记日志，绝不抛给 HTTP。"""
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:  # pragma: no cover - Windows 上 chmod 语义有限，失败忽略
            pass
        os.replace(tmp_path, path)
    except OSError as exc:  # pragma: no cover - 磁盘满 / 权限不足等
        logger.warning("设置文件写入失败（本次仅在内存生效）：%s（%s）", path, exc)


def _apply_partial(
    current: dict[str, Any], partial: Any
) -> dict[str, Any]:
    """把部分更新合并进当前设置（浅层嵌套），校验失败抛 400 异常。"""
    if not isinstance(partial, Mapping):
        raise SettingsValidationError("请求体必须是 JSON 对象")
    merged = deepcopy(current)

    account = partial.get("account")
    if account is not None:
        account = _as_mapping(account, "account")
        mode = account.get("mode")
        mode = None if mode is None else _as_enum(mode, "account.mode", ACCOUNT_MODES)
        cookie_given = "cookie" in account
        cookie_value = account.get("cookie")
        if cookie_given and cookie_value is not None and not isinstance(cookie_value, str):
            raise SettingsValidationError("account.cookie 必须是字符串")

        if mode == "anonymous":
            # 规则：显式切匿名 → 清除凭据（即使同时传了 cookie 也不保存）
            merged["account"]["mode"] = "anonymous"
            merged["account"]["cookie"] = ""
        else:
            if cookie_given and cookie_value is not None:
                raw_cookie = cookie_value.strip()
                if raw_cookie:
                    merged["account"]["cookie"] = normalize_cookie(raw_cookie)
                    if mode is None:
                        mode = "account"
                else:
                    # 显式清空 Cookie：若没有同时要求账号模式，则自动回落到匿名
                    merged["account"]["cookie"] = ""
                    if mode is None:
                        merged["account"]["mode"] = "anonymous"
            if mode is not None:
                merged["account"]["mode"] = mode
            if (
                merged["account"]["mode"] == "account"
                and not merged["account"]["cookie"]
            ):
                raise SettingsValidationError(
                    "账号模式需要先填写 B 站 Cookie（请在设置页填写 Cookie 后保存）"
                )

    crawl = partial.get("crawl")
    if crawl is not None:
        crawl = _as_mapping(crawl, "crawl")
        for name in DEFAULT_CRAWL:
            if name in crawl:
                merged["crawl"][name] = _validate_crawl_value(name, crawl[name])

    pipeline_part = partial.get("pipeline")
    if pipeline_part is not None:
        pipeline_part = _as_mapping(pipeline_part, "pipeline")
        for name in DEFAULT_PIPELINE:
            if name in pipeline_part:
                merged["pipeline"][name] = _as_bool(
                    pipeline_part[name], f"pipeline.{name}"
                )

    merged["updated_at"] = now_iso()
    return merged


def update_settings(partial: Any) -> dict[str, Any]:
    """应用一次部分更新并落盘，返回脱敏后的完整设置（``PUT /api/settings``）。

    Raises:
        SettingsValidationError: 字段越界 / 枚举非法 / 账号模式缺凭据（HTTP 400）。
    """
    merged = _apply_partial(load_settings(), partial)
    _save_settings(merged)
    return public_view(merged)


def merge_for_task(partial: Any) -> dict[str, Any]:
    """把任务提交里的 ``settings`` 与已保存设置做部分合并（**不落盘**）。

    任务快照只关心「本次任务怎么跑」，因此这里返回内部结构（含 Cookie），
    由 :func:`snapshot_for_task` 去掉 Cookie 后写进任务记录。
    """
    if partial is None:
        return load_settings()
    return _apply_partial(load_settings(), partial)


def snapshot_for_task(merged: Mapping[str, Any]) -> dict[str, Any]:
    """任务记录里的 ``settings_snapshot``：**不含 Cookie**，也不含脱敏串。

    结构（稳定，供 detail.pipeline 与前端使用）::

        {"account": {"mode": ...}, "crawl": {...11 个参数...},
         "pipeline": {"auto_clean": bool, "auto_analyze": bool}}
    """
    account = merged.get("account") or {}
    crawl_raw = merged.get("crawl") or {}
    pipeline_raw = merged.get("pipeline") or {}
    crawl = dict(DEFAULT_CRAWL)
    crawl.update({key: crawl_raw[key] for key in DEFAULT_CRAWL if key in crawl_raw})
    pipeline = dict(DEFAULT_PIPELINE)
    pipeline.update({key: pipeline_raw[key] for key in DEFAULT_PIPELINE if key in pipeline_raw})
    return {
        "account": {
            "mode": account.get("mode") if account.get("mode") in ACCOUNT_MODES else "anonymous"
        },
        "crawl": crawl,
        "pipeline": pipeline,
    }


def account_cookie_for_mode(mode: str) -> str:
    """按任务快照里的账号模式取服务端已保存 Cookie（匿名模式一律返回空串）。"""
    if mode != "account":
        return ""
    return str(load_settings()["account"].get("cookie") or "")


def cache_login_identity(uname: str, mid: int) -> None:
    """把最近一次检查登录态成功的昵称 / mid 缓存进设置文件（只读字段）。"""
    current = load_settings()
    current["account"]["uname"] = str(uname or "")
    try:
        current["account"]["mid"] = max(int(mid or 0), 0)
    except (TypeError, ValueError):
        current["account"]["mid"] = 0
    current["updated_at"] = now_iso()
    _save_settings(current)


def save_account_cookie(cookie: str, *, uname: str = "", mid: int = 0) -> dict[str, Any]:
    """保存人工登录窗口捕获到的 Cookie（同时切到账号模式并缓存 uname / mid）。

    Cookie 明文**只写**服务端设置文件，不返回、不打日志；写盘失败也不抛给调用方。

    Returns:
        脱敏后的完整设置（与 ``GET /api/settings`` 同构）。
    """
    current = load_settings()
    normalized = normalize_cookie(str(cookie or ""))
    if not normalized:
        raise SettingsValidationError("登录窗口未捕获到有效的 Cookie（缺少 SESSDATA）")
    current["account"]["cookie"] = normalized
    current["account"]["mode"] = "account"
    current["account"]["uname"] = str(uname or "")
    try:
        current["account"]["mid"] = max(int(mid or 0), 0)
    except (TypeError, ValueError):
        current["account"]["mid"] = 0
    current["updated_at"] = now_iso()
    _save_settings(current)
    return public_view(current)


# --------------------------------------------------------------------------- #
# 登录态检查（可注入假客户端，测试全程离线）
# --------------------------------------------------------------------------- #
_session_factory: Callable[..., Any] | None = None
_session_factory_lock = threading.Lock()


def set_session_client_factory(factory: Callable[..., Any] | None) -> None:
    """替换登录态检查用的客户端工厂（测试注入假客户端；``None`` 恢复默认）。

    工厂签名：``factory(*, cookie: str) -> 客户端``，客户端需提供
    ``login_status() -> {is_login, uname, mid, ...}`` 与可选 ``close()``。
    """
    global _session_factory
    with _session_factory_lock:
        _session_factory = factory


def peek_session_client_factory() -> Callable[..., Any] | None:
    """返回当前注入的客户端工厂（测试清理用，不改变状态）。"""
    return _session_factory


def _build_session_client(cookie: str) -> Any:
    """默认客户端工厂：延迟导入爬虫，避免模块导入期拉起 requests / bs4。"""
    from ..crawler.bilibili import BiliClient

    return BiliClient(
        cookie=cookie,
        delay=0.0,
        timeout=SESSION_CHECK_TIMEOUT,
        max_attempts=2,
        respect_robots=False,
        api_robots_policy="ignore",
    )


def check_session(cookie: str | None = None, *, client_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
    """检查 B 站登录态（``BiliClient.login_status``）。

    Args:
        cookie: 可选临时凭据；``None`` / 空串时用已保存凭据。
        client_factory: 可注入的客户端工厂（测试用；默认真实 ``BiliClient``）。

    Returns:
        与 ``POST /api/crawler/session/check`` 响应同构的字典。

    Raises:
        SessionCheckError: 网络 / 接口异常（HTTP 502）；未配置凭据**不报错**，
            直接返回匿名态。
    """
    effective_cookie = str(cookie or "").strip()
    if not effective_cookie:
        effective_cookie = str(load_settings()["account"].get("cookie") or "")
    if not effective_cookie:
        return {
            "mode": "anonymous",
            "has_credentials": False,
            "is_login": False,
            "uname": "",
            "mid": 0,
            "message": "未配置登录凭据，当前为匿名模式",
        }

    factory = client_factory or _session_factory or _build_session_client
    client = None
    try:
        client = factory(cookie=effective_cookie)
        status = dict(client.login_status() or {})
    except Exception as exc:  # noqa: BLE001 - 统一转成 502 + 中文原因
        reason = redact_secrets(
            f"检查登录状态失败：{type(exc).__name__}: {exc}", effective_cookie
        )
        raise SessionCheckError(reason) from None
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响检查结果
                pass

    is_login = bool(status.get("is_login"))
    uname = str(status.get("uname") or "")
    try:
        mid = max(int(status.get("mid") or 0), 0)
    except (TypeError, ValueError):
        mid = 0
    if is_login:
        cache_login_identity(uname, mid)
    return {
        "mode": "account" if is_login else "anonymous",
        "has_credentials": True,
        "is_login": is_login,
        "uname": uname if is_login else "",
        "mid": mid if is_login else 0,
        "message": (
            f"已登录：{uname}" if is_login else "Cookie 无效或已过期，当前未登录"
        ),
    }
