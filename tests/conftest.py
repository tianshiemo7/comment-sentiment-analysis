"""pytest 共享配置。

作用：

1. 把仓库根目录加入 ``sys.path``，这样无论用
   ``python -m pytest tests/`` 还是 ``pytest tests/`` 运行，
   ``import src.inference.app`` 都能解析到仓库内的 `src` 包；
2. 每条用例自动把批量导出的落盘目录重定向到临时目录，避免单测在
   ``outputs/predictions/`` 里堆积 CSV（真实运行时的落盘位置由
   ``batch.PREDICTIONS_DIR`` 决定，仍然是 ``outputs/predictions/``）；
3. 每条用例自动把 ``/api/videos`` 系列的落盘目录重定向到临时目录，并清掉
   `src.videos.service` 的进程内单例，避免单测在 ``outputs/videos/`` 里留下产物、
   也避免用例之间共享后台线程与状态（真实运行目录仍是 ``outputs/videos/``）；
4. 每条用例自动把 ``/api/artifacts`` 与 ``/api/solo-comments`` 的落盘目录
   （``data/uploads/``）重定向到临时目录并清掉 ``src.artifacts.service`` 单例；
5. 每条用例自动把「在本机打开 CSV」的实现换成记录器，**测试绝不真的拉起 Excel**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def isolated_predictions_dir(tmp_path, monkeypatch):
    """把 ``src.inference.batch.PREDICTIONS_DIR`` 指向本次用例的临时目录。"""
    from src.inference import batch as batch_io

    target = tmp_path / "predictions"
    monkeypatch.setattr(batch_io, "PREDICTIONS_DIR", target)
    return target


def _dispose_video_service(video_service) -> None:
    """关停并清除 `src.videos.service` 的进程内单例（幂等）。"""
    service = video_service.peek_service()
    if service is not None:
        service.shutdown()
    video_service.set_service(None)


@pytest.fixture(autouse=True)
def isolated_videos_dir(tmp_path, monkeypatch):
    """把 ``src.videos.store.DEFAULT_VIDEOS_DIR`` 指向临时目录，并重置单例。"""
    from src.videos import service as video_service
    from src.videos import store as video_store

    monkeypatch.delenv("VIDEOS_DIR", raising=False)
    target = tmp_path / "videos"
    monkeypatch.setattr(video_store, "DEFAULT_VIDEOS_DIR", target)
    _dispose_video_service(video_service)
    yield target
    _dispose_video_service(video_service)


def _dispose_artifacts_service(artifacts_service) -> None:
    """关停并清除 `src.artifacts.service` 的进程内单例（幂等）。"""
    service = artifacts_service.peek_service()
    if service is not None:
        service.shutdown()
    artifacts_service.set_service(None)


@pytest.fixture(autouse=True)
def isolated_artifacts_dir(tmp_path, monkeypatch):
    """把 ``src.artifacts.store.DEFAULT_PROJECTS_ROOT`` 指向临时目录，并重置单例。"""
    from src.artifacts import service as artifacts_service
    from src.artifacts import store as artifacts_store

    monkeypatch.delenv("ARTIFACTS_DIR", raising=False)
    monkeypatch.delenv("ARTIFACTS_OPENER", raising=False)
    target = tmp_path / "artifacts"
    monkeypatch.setattr(artifacts_store, "DEFAULT_PROJECTS_ROOT", target)
    _dispose_artifacts_service(artifacts_service)
    yield target
    _dispose_artifacts_service(artifacts_service)


@pytest.fixture(autouse=True)
def artifact_opener_recorder():
    """把「在本机打开 CSV」换成记录器，返回被打开的路径列表（测试不弹 Excel）。

    需要断言打开行为的用例直接把这个 fixture 作为参数请求即可；
    它同时是 autouse 的，因此任何用例都不会真的调用 ``os.startfile``。
    """
    from src.artifacts import opener

    calls: list[str] = []
    previous = opener.peek_opener()
    opener.set_opener(lambda path: calls.append(str(path)))
    yield calls
    opener.set_opener(previous)

