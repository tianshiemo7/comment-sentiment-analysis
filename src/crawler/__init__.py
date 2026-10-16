"""① 爬虫模块：公开数据集下载与站点评论采集。

对外入口见 `spider.py`（采集流程）与 `sources.py`（来源登记）。
本包不在导入期引入 `requests` / `bs4`，缺失依赖时只有真正调用函数才会报错。
"""

from .spider import CrawlConfig, crawl, fetch_html, is_allowed_by_robots, parse_comments

__all__ = [
    "CrawlConfig",
    "crawl",
    "fetch_html",
    "is_allowed_by_robots",
    "parse_comments",
]
