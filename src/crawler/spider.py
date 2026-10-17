"""统一采集框架：配置、robots、站点适配、增量 CSV 和来源登记。"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.crawler.sources import SourceRecord, write_source_note
else:
    from .sources import SourceRecord, write_source_note

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DELAY_SECONDS = float(os.getenv('CRAWL_DELAY_SECONDS', '1.5'))
DEFAULT_USER_AGENT = os.getenv('CRAWL_USER_AGENT', 'comment-sentiment-analysis-course/0.1 (+https://github.com/tianshiemo7/comment-sentiment-analysis)')
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_ITEMS = 2000
RAW_FIELDS = ('text', 'label', 'source', 'url', 'crawled_at')
LOG = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    """max_items 为本次新增条数上限，B站参数放在 extra。"""
    start_urls: list[str]
    source: str
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = DEFAULT_TIMEOUT
    max_items: int = DEFAULT_MAX_ITEMS
    out_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    respect_robots: bool = True

    def __post_init__(self):
        if not self.start_urls:
            raise ValueError('start_urls 不能为空')
        if not re.fullmatch(r'[\w-]+', self.source):
            raise ValueError('source 只能包含文字、数字、下划线或连字符')
        for name in ('delay_seconds', 'timeout'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} 必须是大于零的有限数值')
        if not isinstance(self.max_items, int) or self.max_items < 1:
            raise ValueError('max_items 必须为正整数')
        if not self.user_agent.strip():
            raise ValueError('user_agent 不能为空')
        self.out_path = Path(self.out_path) if self.out_path else ROOT / 'data' / 'raw' / f'{self.source}.csv'
        if self.source == 'bilibili':
            self.delay_seconds = max(2.0, self.delay_seconds)
            for name, default, maximum in (('max_pages', 5, 50), ('max_sub_pages', 3, 20)):
                value = self.extra.get(name, default)
                if not isinstance(value, int) or not 1 <= value <= maximum:
                    raise ValueError(f'{name} 必须为 1~{maximum}')
            if self.extra.get('mode', 'time') not in ('time', 'hot', 2, 3):
                raise ValueError('mode 必须为 time/hot 或 2/3')


def is_allowed_by_robots(url: str, user_agent: str = DEFAULT_USER_AGENT, *,
                         timeout: float = DEFAULT_TIMEOUT, session=None, cache=None,
                         raise_on_denied: bool = False) -> bool:
    """默认返回 bool；采集入口可要求抛出明确的禁止 / 检查失败原因。"""
    import requests
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        if raise_on_denied:
            raise PermissionError(f'robots.txt 检查失败：不是有效的 HTTP(S) 地址：{url}')
        return False
    robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
    cache = {} if cache is None else cache
    key = (robots_url, user_agent)
    if key not in cache:
        own_session = session is None
        session = session or requests.Session()
        try:
            LOG.info('检查 robots.txt：%s', robots_url)
            response = session.get(robots_url, headers={'User-Agent': user_agent},
                                   timeout=timeout, allow_redirects=False)
            if response.status_code == 404:
                cache[key] = (True, '')
            elif response.status_code == 200:
                parser = RobotFileParser(robots_url)
                parser.parse(response.text.splitlines())
                cache[key] = (parser, '')
            else:
                reason = (f'robots.txt 检查失败：HTTP {response.status_code}，'
                          f'无法确认抓取规则；地址：{robots_url}')
                LOG.warning('%s', reason)
                cache[key] = (False, reason)
        except requests.RequestException as exc:
            reason = (f'robots.txt 检查失败：{type(exc).__name__}，'
                      f'请检查网络、代理或超时设置；地址：{robots_url}')
            LOG.warning('%s', reason)
            cache[key] = (False, reason)
        finally:
            if own_session:
                session.close()
    policy, reason = cache[key]
    allowed = policy if isinstance(policy, bool) else policy.can_fetch(user_agent, url)
    if not allowed:
        reason = reason or (f'robots.txt 明确禁止当前爬虫访问：{url}；'
                            f'规则文件：{robots_url}。未发送目标接口请求。')
        if raise_on_denied:
            raise PermissionError(reason)
        LOG.warning('%s', reason)
    return allowed


def fetch_html(url: str, config: CrawlConfig) -> str:
    """按配置检查 robots 并限速抓取；仅重试超时、连接异常和 5xx。"""
    import requests
    with requests.Session() as session:
        if config.respect_robots:
            # 上一个页面结束后也要限速，robots.txt 同样是网络请求。
            time.sleep(config.delay_seconds)
            is_allowed_by_robots(url, config.user_agent, timeout=config.timeout,
                                 session=session, raise_on_denied=True)
        for attempt in range(3):
            time.sleep(config.delay_seconds)
            try:
                response = session.get(url, headers={'User-Agent': config.user_agent},
                                       timeout=config.timeout, allow_redirects=False)
                if 300 <= response.status_code < 400:
                    raise ValueError('请提供最终 URL；重定向目标需要单独检查 robots.txt')
                response.raise_for_status()
                response.encoding = response.apparent_encoding or 'utf-8'
                return response.text
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                status = exc.response.status_code if exc.response is not None else None
                if attempt == 2 or (status is not None and status < 500):
                    raise
                LOG.warning('页面请求失败，第 %d 次重试：%s', attempt + 1, exc)
                time.sleep(2 ** attempt)
    raise RuntimeError('页面请求失败')


def parse_comments(html: str, selector: str | None = None) -> list[str]:
    """HTML 站点必须指定评论正文的 CSS selector，避免误采整页导航。"""
    if not selector:
        raise ValueError('HTML 采集必须指定评论正文 selector')
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script, style, button, noscript'):
        node.decompose()
    texts = [node.get_text(' ', strip=True) for node in soup.select(selector)]
    return [text for text in texts if any(char.isalnum() for char in text)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def save_raw(items: Iterable[dict[str, Any]], out_path: str | Path) -> Path:
    """显式覆盖导出标准五列；采集主流程使用增量 RawStore。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(items)
    temporary.replace(path)
    return path


class CrawlLimitReached(Exception):
    """本次新增条数达到上限，当前页保留原游标。"""


class RawStore:
    """逐批落盘；已有文件仅追加，按 B站评论 ID 或 HTML 来源去重。"""
    def __init__(self, path, fields, max_items):
        self.path = Path(path)
        self.fields = tuple(fields)
        self.max_items = max_items
        self.items = []
        self.keys = set()
        self.visited_urls = set()
        self.existed = self.path.exists() and self.path.stat().st_size > 0
        if self.existed:
            with self.path.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != self.fields:
                    raise ValueError(f'输出文件字段与框架不一致，请使用新的 --out 文件：{self.path}')
                self.keys = {self.key(row) for row in reader}
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('w', encoding='utf-8-sig', newline='') as handle:
                csv.DictWriter(handle, fieldnames=self.fields).writeheader()

    @staticmethod
    def key(row):
        if row.get('rpid'):
            return row['source'], row['bvid'], str(row['rpid'])
        return row['source'], row['url'], row['text']

    @property
    def full(self):
        return len(self.items) >= self.max_items

    def check_limit(self):
        if self.full:
            raise CrawlLimitReached()

    def add(self, records):
        added = 0
        with self.path.open('a', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fields)
            for row in records:
                if row is None or not str(row.get('text', '')).strip():
                    continue
                key = self.key(row)
                if key in self.keys:
                    continue
                self.check_limit()
                writer.writerow(row)
                handle.flush()
                self.keys.add(key)
                self.items.append(row)
                added += 1
        return added


def crawl(config: CrawlConfig) -> list[dict[str, Any]]:
    """站点适配 -> 增量 CSV -> 来源登记；返回本次新保存的记录。"""
    if config.source == 'bilibili':
        from src.crawler import bilibili
        fields = RAW_FIELDS + tuple(name for name in bilibili.COLUMNS if name not in RAW_FIELDS)
        # 在创建输出前验证全部 BV，避免输入错误产生无用文件。
        bvids = list(dict.fromkeys(bilibili.parse_bvid(value) for value in config.start_urls))
        urls = [f'https://www.bilibili.com/video/{bvid}/' for bvid in bvids]
    else:
        fields = RAW_FIELDS
        urls = list(dict.fromkeys(config.start_urls))
        if not config.extra.get('selector'):
            raise ValueError('HTML 采集必须指定 selector')
    store = RawStore(config.out_path, fields, config.max_items)
    robots_status = '已启用' if config.respect_robots else '已跳过'
    LOG.info('robots.txt 检查：%s', robots_status)
    status = '已完成本次配置范围（不代表已抓取全部评论）'
    error = None
    try:
        if config.source == 'bilibili':
            bilibili.crawl_config(config, store, bvids)
        else:
            for url in urls:
                store.visited_urls.add(url)
                html = fetch_html(url, config)
                rows = ({'text': text, 'label': None, 'source': config.source,
                         'url': url, 'crawled_at': now_iso()}
                        for text in parse_comments(html, config.extra['selector']))
                store.add(rows)
                store.check_limit()
    except CrawlLimitReached:
        status = '达到本次新增条数上限；未完成页面下次重扫并去重'
    except BaseException as exc:
        error = exc
        status = f'未完成：{exc}'
        raise
    finally:
        # 失败时也登记已落盘的数据，但不让来源登记异常掩盖原始错误。
        records = []
        for url in urls:
            count = sum(row['url'] == url for row in store.items)
            url_status = status if url in store.visited_urls else '未访问（本次任务未处理到此页面）'
            records.append(SourceRecord(config.source, url, now_iso(), count, labeled=False,
                                        note=f'{url_status}；文件：{config.out_path.name}；'
                                             f'条数为本次新增；robots.txt 检查：{robots_status}'))
        try:
            write_source_note(records, config.out_path.parent / '来源说明.md')
        except OSError:
            if error is None:
                raise
            LOG.exception('来源登记失败；CSV 已保存')
        LOG.info('本次新增 %d 条，CSV：%s', len(store.items), config.out_path)
    return store.items


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description='评论采集框架（B站 / 静态 HTML）')
    parser.add_argument('--site', choices=['bilibili', 'html'], default='bilibili')
    parser.add_argument('--bvid', nargs='+', help='BV 号或视频链接，省略时交互输入')
    parser.add_argument('--urls', nargs='+', help='HTML 页面 URL 或 B站视频链接')
    parser.add_argument('--selector', help='HTML 评论正文 CSS 选择器')
    parser.add_argument('--max-pages', type=int, default=5)
    parser.add_argument('--max-sub-pages', type=int, default=3)
    parser.add_argument('--include-replies', action='store_true')
    parser.add_argument('--mode', choices=['time', 'hot'], default='time')
    parser.add_argument('--delay', type=float, default=max(2.5, DEFAULT_DELAY_SECONDS))
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument('--max-items', '--limit', type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument('--out', type=Path, help='统一 CSV 输出路径')
    parser.add_argument('--reset-state', action='store_true', help='重新扫描，保留 CSV 去重')
    parser.add_argument('--skip-robots', action='store_true', help='跳过本地 robots.txt 检查')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    print('评论采集工具已启动', flush=True)
    values = args.bvid or args.urls
    if args.site == 'html' and (not args.urls or not args.selector or args.bvid):
        parser.error('HTML 采集需要 --urls 和 --selector，不能使用 --bvid')
    if args.bvid and args.urls:
        parser.error('--bvid 与 --urls 请选择一个')
    if not values:
        try:
            values = input('请输入 BV 号或视频链接（多个用空格分隔）：').split()
        except (EOFError, OSError):
            parser.error('当前窗口不能输入，请在 VS Code 终端运行或使用 --bvid BV号')
        except KeyboardInterrupt:
            print('\n已取消采集')
            return 130
        if not values:
            parser.error('未输入 BV 号或视频链接')
    try:
        cfg = CrawlConfig(values, args.site, args.delay, timeout=args.timeout,
                          max_items=args.max_items, out_path=args.out,
                          respect_robots=not args.skip_robots,
                          extra={'max_pages': args.max_pages, 'max_sub_pages': args.max_sub_pages,
                                 'include_replies': args.include_replies, 'mode': args.mode,
                                 'reset_state': args.reset_state, 'selector': args.selector})
        crawl(cfg)
    except KeyboardInterrupt:
        print('\n已取消；已写入 CSV 的数据保留，未完成页面下次重扫并去重')
        return 130
    except Exception as exc:
        LOG.error('采集停止：%s', exc)
        print('已写入的 CSV 数据会保留；仅完整处理的页面推进断点。')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
