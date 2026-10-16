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
   也避免用例之间共享后台线程与状态（真实运行目录仍是 ``outputs/videos/``）。
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

