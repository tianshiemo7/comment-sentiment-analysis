"""评论采集包；延迟加载框架，不在导入期加载 requests / bs4。"""

__all__ = ['CrawlConfig', 'crawl', 'fetch_html', 'is_allowed_by_robots', 'parse_comments', 'save_raw']


def __getattr__(name):
    if name in __all__:
        from . import spider
        return getattr(spider, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
