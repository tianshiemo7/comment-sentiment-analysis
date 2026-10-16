"""「在本机用系统默认程序打开 CSV」的实现。

契约（`docs/API契约.md` 4.5.1 打开 CSV）：

- Windows → :func:`os.startfile`；macOS → ``open``；Linux → ``xdg-open``；

安全边界（关键）：本模块**只接受已经过校验的绝对路径**，
真正的「路径是否合法」判断在 :meth:`src.artifacts.store.ArtifactStore.resolve_open_path`
里完成：扩展名必须是 ``.csv``，``resolve()`` 后父目录必须在
``data/uploads/`` 或 ``data/processed/`` 之内。本模块只负责「怎么弹」。

测试 / 自动化验收：用 :func:`set_opener` 注入假实现（例如只记录路径），
或用环境变量 ``ARTIFACTS_OPENER=noop`` 让服务端不弹窗（避免在 CI / 服务器上
真的拉起 Excel）。两者都不改变接口返回结构。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .errors import OpenUnsupportedError

logger = logging.getLogger("src.artifacts")

#: 环境变量：设为 ``noop`` 时不真正调用系统程序（仅用于自动化验收 / 无桌面环境）
ENV_OPENER = "ARTIFACTS_OPENER"

#: 进程内注入的假 opener（测试用；优先于平台实现与环境变量）
_opener: Callable[[Path], None] | None = None

Opener = Callable[[Path], None]


def set_opener(opener: Opener | None) -> None:
    """注入 / 清除假 opener（传 ``None`` 即恢复真实平台实现）。"""
    global _opener
    _opener = opener


def reset_opener() -> None:
    """清除注入的假 opener。"""
    set_opener(None)


def peek_opener() -> Opener | None:
    """返回当前注入的假 opener（不带副作用，测试断言用）。"""
    return _opener


def _noop(path: Path) -> None:  # pragma: no cover - 仅被环境变量选中
    """空实现：不调用任何系统程序（自动化验收专用）。"""
    logger.info("ARTIFACTS_OPENER=noop：跳过真实打开 %s", path)


def _platform_opener() -> Opener:
    """按当前平台返回真实打开实现；平台不支持时抛 :class:`OpenUnsupportedError`。"""
    if sys.platform.startswith("win"):
        startfile = getattr(os, "startfile", None)
        if startfile is None:  # pragma: no cover - Windows 上必有
            raise OpenUnsupportedError("当前 Windows 环境缺少 os.startfile，无法在本机打开文件")

        def _windows(path: Path) -> None:
            startfile(str(path))  # type: ignore[arg-type]

        return _windows
    if sys.platform == "darwin":

        def _macos(path: Path) -> None:
            subprocess.Popen(["open", str(path)])  # noqa: S603 - 参数为已校验的绝对路径

        return _macos
    if sys.platform.startswith("linux"):

        def _linux(path: Path) -> None:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603 - 参数为已校验的绝对路径

        return _linux
    raise OpenUnsupportedError(f"当前平台不支持在本机打开文件：{sys.platform}")


def open_path(path: str | Path) -> None:
    """用系统默认程序打开 `path`（已在 caller 侧完成路径与扩展名校验）。

    Raises:
        OpenUnsupportedError: 当前平台没有可用实现。
    """
    target = Path(path)
    if _opener is not None:
        _opener(target)
        return
    if (os.getenv(ENV_OPENER) or "").strip().lower() == "noop":
        _noop(target)
        return
    _platform_opener()(target)
