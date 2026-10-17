"""`/api/videos` 系列（B 站视频评论分析任务）的 Python 实现。

模块划分：

| 文件 | 职责 |
| --- | --- |
| `schemas.py` | 契约的代码化：Pydantic 请求 / 响应模型、状态与 phases 枚举、BV 号正则 |
| `config.py` | 流水线阶段时长等可配置参数（环境变量，默认合计 30 秒） |
| `errors.py` | 400 / 409 / 404 业务异常与契约锁定的错误文案 |
| `messages.py` | 各状态的 `message` 文案（与前端显示对齐） |
| `store.py` | 落盘存储：`outputs/videos/index.json` + `<bvid>.json` + 阶段产物 `<bvid>/`（线程安全 + 原子写） |
| `pipeline.py` | 后台流水线：旧路径一次跑完；带 settings 时在 `crawled` / `cleaned` 暂停（可继续/可失败） |
| `settings.py` | 系统设置：账号 / 采集参数 / 自动流程（Cookie 脱敏、原子写、登录态检查） |
| `browser_login.py` | 人工登录窗口（Playwright，人在本机完成扫码/登录，后端捕获 Cookie） |
| `placeholder.py` | ⚠️ 占位 detail 生成器（真实爬虫 / 模型未接入，`placeholder: true` 如实标注） |
| `service.py` | 业务逻辑 + 单工作线程调度 + 提交/重试/继续 + 启动恢复 + 优雅退出 |

HTTP 层仍留在 `src/inference/app.py`（只新增 `/api/videos*` 路由，原 5 个接口不动）。

用法::

    from src.videos.service import VideoService
    from src.videos.config import PipelineConfig

    service = VideoService(root="outputs/videos", config=PipelineConfig(crawl_seconds=0.2))
    service.start()
    service.submit("BV1xx411c7mD")
    ...
    service.shutdown()
"""

from .config import PipelineConfig
from .errors import (
    InvalidBvidError,
    PipelineAborted,
    PipelineFailure,
    VideoBusyError,
    VideoError,
    VideoNotFoundError,
)
from .service import VideoService, get_service, peek_service, set_service
from .store import VideoStore

__all__ = [
    "PipelineConfig",
    "VideoService",
    "VideoStore",
    "get_service",
    "set_service",
    "peek_service",
    "VideoError",
    "InvalidBvidError",
    "VideoBusyError",
    "VideoNotFoundError",
    "PipelineAborted",
    "PipelineFailure",
]
