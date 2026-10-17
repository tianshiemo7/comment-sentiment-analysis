"""① 爬虫模块：公开数据集下载与站点评论采集。

对外入口：

- 通用 HTML 采集：``spider.CrawlConfig`` / ``crawl`` / ``fetch_html`` / ``parse_comments``
  （``python -m src.crawler html --url ... --source ...``）；
- B 站接口采集：``bilibili.BiliClient`` / ``crawl_video`` / ``crawl_bilibili`` /
  ``fetch_video_comments``（``python -m src.crawler bilibili --bvid ...``）；
- 来源登记：``sources.register_source`` → ``data/raw/来源说明.md``；
- 命令行总入口：``python -m src.crawler``。

本包在 **导入期不引入 requests / bs4**：只有真正调用采集函数时才延迟导入，
因此 CI 在「最小依赖集」下也能 ``import src.crawler``。具体实现见各子模块。
"""

from __future__ import annotations

from importlib import import_module

from .sources import (
    SOURCE_NOTE_PATH,
    SOURCES_JSON_PATH,
    SourceRecord,
    register_source,
    render_source_table,
    write_source_note,
)

#: 延迟导入的公共符号 → 所属子模块
_LAZY_EXPORTS = {
    # 通用 HTML 爬虫
    "CrawlConfig": "spider",
    "CrawlReport": "spider",
    "HtmlCrawler": "spider",
    "crawl": "spider",
    "crawl_with_report": "spider",
    "fetch_html": "spider",
    "is_allowed_by_robots": "spider",
    "parse_comments": "spider",
    "save_raw": "spider",
    # B 站接口爬虫
    "BiliClient": "bilibili",
    "VideoCrawlResult": "bilibili",
    "crawl_video": "bilibili",
    "crawl_bilibili": "bilibili",
    "fetch_video_comments": "bilibili",
    "parse_bvid": "bilibili",
    # 公共组件
    "CrawlerError": "errors",
    "CrawlStopped": "errors",
    "RiskControlError": "errors",
    "RobotsDenied": "errors",
    "CommentClosed": "errors",
    "RateLimiter": "rate_limit",
    "backoff_delay": "rate_limit",
    "RobotsChecker": "robots",
    "normalize_text": "normalize",
    "normalize_comment": "normalize",
    # 公开数据集 / 标注工作流
    "DatasetError": "datasets",
    "DatasetSpec": "datasets",
    "list_datasets": "datasets",
    "download_dataset": "datasets",
    "import_labeled_csv": "datasets",
    "export_labeling_sample": "labeling",
    "merge_labels": "labeling",
}

__all__ = [
    "SOURCE_NOTE_PATH",
    "SOURCES_JSON_PATH",
    "SourceRecord",
    "register_source",
    "render_source_table",
    "write_source_note",
    *sorted(_LAZY_EXPORTS),
]


def __getattr__(name: str):
    """PEP 562 延迟导入：保持包导入零网络依赖，同时保留扁平导入体验。"""

    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
