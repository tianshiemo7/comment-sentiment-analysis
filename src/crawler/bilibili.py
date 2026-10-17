
import hashlib
import json
import logging
import os
import re
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import requests


# =========================
# 基础配置
# =========================

LOG = logging.getLogger("bili_crawler")

API = "https://api.bilibili.com"

ROOT = Path(__file__).resolve().parents[2]

SAVE_DIR = ROOT / "data" / "raw"

# WBI 签名混淆表
MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52
)

COLUMNS = [
    "bvid",
    "aid",
    "video_title",
    "rpid",
    "level",
    "root_rpid",
    "parent_rpid",
    "text",
    "likes",
    "reply_count",
    "ctime_utc",
    "collected_at_utc"
]

BVID_RE = re.compile(
    r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])"
)


class CrawlStopped(Exception):
    pass


# =========================
# 工具函数
# =========================

def utc_iso(ts=None):
    """时间戳转 UTC 时间字符串"""

    if ts is None:
        return datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")

    try:
        return datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        ).isoformat(timespec="seconds")

    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def parse_bvid(value):
    """解析 BV 号，支持直接输入视频 URL"""

    value = value.strip()

    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)

        if parsed.hostname not in {
            "www.bilibili.com",
            "bilibili.com",
            "m.bilibili.com"
        }:
            raise ValueError("不是有效的 B站视频网址")

        value = parsed.path

    match = BVID_RE.search(value)

    if not match:
        raise ValueError(f"无法识别 BV 号：{value}")

    return match.group()


# =========================
# WBI 签名
# =========================

def get_mixin_key(img_key, sub_key):
    """生成 WBI 混合密钥"""

    raw = img_key + sub_key

    if len(raw) != 64:
        raise CrawlStopped("WBI 密钥格式异常")

    key = "".join(
        raw[i] for i in MIXIN_KEY_ENC_TAB
    )

    return key[:32]


def sign_wbi(params, mixin_key):
    """对请求参数进行 WBI 签名"""

    params = {
        str(k): "".join(
            c for c in str(v)
            if c not in "!'()*"
        )
        for k, v in params.items()
    }

    params["wts"] = str(int(time.time()))

    params = dict(sorted(params.items()))

    query = urlencode(
        params,
        quote_via=quote,
        safe=""
    )

    w_rid = hashlib.md5(
        (query + mixin_key).encode("utf-8")
    ).hexdigest()

    params["w_rid"] = w_rid

    return params


# =========================
# B站 API 客户端
# =========================

class BiliClient:

    def __init__(self, delay=2.5, timeout=15, user_agent=None, respect_robots=True):

        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                user_agent or os.environ.get("CRAWL_USER_AGENT", "CommentSentimentCourseProject/1.0")
            ),
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json"
        })

        # 仅在有合法访问权限时使用凭据
        # 不要把 Cookie 直接写在源码里
        sessdata = os.environ.get("BILI_SESSDATA")

        if sessdata:
            self.session.cookies.set(
                "SESSDATA",
                sessdata,
                domain=".bilibili.com"
            )

        self.last_request = 0.0
        self.mixin_key = None
        self.robots_cache = {}

    def request(self, path, params=None, allow_nav=False):
        """统一请求方法"""

        from src.crawler.spider import is_allowed_by_robots

        url = API + path
        if self.respect_robots:
            cache_size = len(self.robots_cache)
            try:
                is_allowed_by_robots(
                    url, self.session.headers["User-Agent"], timeout=self.timeout,
                    session=self.session, cache=self.robots_cache, raise_on_denied=True
                )
            except PermissionError as exc:
                raise CrawlStopped(str(exc)) from exc
            if len(self.robots_cache) != cache_size:
                self.last_request = time.monotonic()

        for attempt in range(3):

            elapsed = (
                time.monotonic() - self.last_request
            )

            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)

            try:
                self.last_request = time.monotonic()

                LOG.info(
                    "正在请求 %s（第 %d/3 次尝试，超时设置 %s 秒）",
                    path, attempt + 1, self.timeout
                )
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    allow_redirects=False
                )

                if 300 <= response.status_code < 400:
                    raise CrawlStopped("接口发生重定向，请检查接口地址")

                # 遇到权限或限流问题立即停止
                if response.status_code in (
                    401, 403, 412, 429
                ):
                    raise CrawlStopped(
                        f"HTTP {response.status_code}："
                        "权限不足或访问受限"
                    )

                if response.status_code >= 500:
                    raise requests.HTTPError(
                        f"服务器错误：{response.status_code}"
                    )

                response.raise_for_status()

                result = response.json()

                if not isinstance(result, dict):
                    raise CrawlStopped("返回数据格式异常")

                code = result.get("code")

                if code == -352:
                    raise CrawlStopped(
                        f"{path} 返回 -352：B站风控校验失败。"
                        "请暂停采集并在浏览器确认登录和访问权限；本次不自动重试。"
                    )

                if code != 0:

                    # 导航接口在匿名状态下可能返回 -101
                    if not (allow_nav and code == -101):
                        raise CrawlStopped(
                            f"接口错误：{code} "
                            f"{result.get('message')}"
                        )

                return result

            except CrawlStopped:
                raise

            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.HTTPError
            ) as e:

                if isinstance(e, requests.HTTPError):
                    status = getattr(
                        getattr(e, "response", None),
                        "status_code",
                        500
                    )
                    if status < 500:
                        raise CrawlStopped(
                            f"请求失败：{e}"
                        ) from e

                if attempt == 2:
                    raise CrawlStopped(
                        f"网络请求失败：{e}"
                    ) from e

                LOG.warning(
                    "网络异常，进行第 %d 次重试",
                    attempt + 1
                )

                time.sleep(2 * (attempt + 1))

            except (
                ValueError,
                requests.RequestException
            ) as e:
                raise CrawlStopped(
                    f"响应解析或请求失败：{e}"
                ) from e

        raise CrawlStopped("请求失败")

    def get_video_info(self, bvid):
        """BV 号获取视频信息"""

        result = self.request(
            "/x/web-interface/view",
            {"bvid": bvid}
        )

        data = result.get("data") or {}

        aid = data.get("aid")

        if not aid:
            raise CrawlStopped("无法获取视频 aid")

        title = data.get("title", "")

        return int(aid), title

    def get_wbi_key(self):
        """获取 WBI 密钥"""

        if self.mixin_key:
            return self.mixin_key

        result = self.request(
            "/x/web-interface/nav",
            allow_nav=True
        )

        data = result.get("data") or {}
        images = data.get("wbi_img") or {}

        img_url = images.get("img_url", "")
        sub_url = images.get("sub_url", "")

        if not img_url or not sub_url:
            raise CrawlStopped(
                "无法获取 WBI 密钥，请检查访问权限"
            )

        img_key = Path(
            urlparse(img_url).path
        ).stem

        sub_key = Path(
            urlparse(sub_url).path
        ).stem

        self.mixin_key = get_mixin_key(
            img_key,
            sub_key
        )

        return self.mixin_key

    def get_main_comments(self, aid, offset="", mode=2):
        """获取一级评论"""

        params = {
            "type": 1,
            "oid": aid,
            "mode": mode
        }

        if offset:
            params["pagination_str"] = json.dumps(
                {"offset": offset},
                separators=(",", ":")
            )

        params = sign_wbi(
            params,
            self.get_wbi_key()
        )

        result = self.request(
            "/x/v2/reply/wbi/main",
            params
        )

        return result.get("data") or {}

    def get_sub_comments(self, aid, root, page=1):
        """获取楼中楼评论"""

        params = {
            "type": 1,
            "oid": aid,
            "root": root,
            "pn": page,
            "ps": 20
        }

        result = self.request(
            "/x/v2/reply/reply",
            params
        )

        return result.get("data") or {}


# =========================
# 评论数据整理
# =========================

def normalize_comment(
    item,
    bvid,
    aid,
    title,
    level=1,
    forced_root=""
):
    """统一一级评论和二级评论格式"""

    rpid = str(
        item.get("rpid_str")
        or item.get("rpid")
        or ""
    )

    content = item.get("content") or {}

    message = str(
        content.get("message") or ""
    )

    if not rpid or not message.strip():
        return None

    root = str(
        item.get("root_str")
        or item.get("root")
        or "0"
    )

    parent = str(
        item.get("parent_str")
        or item.get("parent")
        or "0"
    )

    if level == 1:
        root = ""
        parent = ""

    else:
        root = forced_root or root

        if parent == "0":
            parent = root

    return {
        "bvid": bvid,
        "aid": aid,
        "video_title": title,
        "rpid": rpid,
        "level": level,
        "root_rpid": root,
        "parent_rpid": parent,
        "text": message,
        "likes": item.get("like", 0),
        "reply_count": item.get("rcount", 0),
        "ctime_utc": utc_iso(item.get("ctime")),
        "collected_at_utc": utc_iso()
    }


# =========================
# 断点续爬
# =========================

def load_state(
    path,
    mode,
    include_replies,
    max_sub_pages,
    reset=False
):

    if path.exists() and not reset:

        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)

        if (
            state.get("mode") != mode
            or state.get("include_replies") != include_replies
            or state.get("max_sub_pages") != max_sub_pages
        ):
            raise ValueError(
                "本次配置与断点配置不同，"
                "如需重新扫描请使用 --reset-state"
            )

        return state

    return {
        "mode": mode,
        "include_replies": include_replies,
        "max_sub_pages": max_sub_pages,
        "offset": "",
        "pages_done": 0,
        "finished": False
    }


def save_state(path, state):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_path = path.with_suffix(".tmp")

    with temp_path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    temp_path.replace(path)


# =========================
# B站适配器：复用统一配置、CSV 存储和来源登记
# =========================


def framework_record(item, bvid, aid, title, level=1, root=''):
    row = normalize_comment(item, bvid, aid, title, level, root)
    if row is None:
        return None
    row.update(label=None, source='bilibili',
               url=f'https://www.bilibili.com/video/{bvid}/',
               crawled_at=row['collected_at_utc'])
    return row


def crawl_config(config, store, bvids):
    """框架适配入口；同一任务共享 HTTP 会话、限速与 CSV 去重集合。"""
    client = BiliClient(delay=config.delay_seconds, timeout=config.timeout,
                        user_agent=config.user_agent, respect_robots=config.respect_robots)
    mode = config.extra.get('mode', 'time')
    mode = 2 if mode == 'time' else 3 if mode == 'hot' else mode
    try:
        for bvid in bvids:
            _crawl_video(client, bvid, config, store, mode)
            store.check_limit()
    finally:
        client.session.close()


def _crawl_video(client, bvid, config, store, mode):
    store.visited_urls.add(f'https://www.bilibili.com/video/{bvid}/')
    include_replies = config.extra.get('include_replies', False)
    max_sub_pages = config.extra.get('max_sub_pages', 3)
    # 绑定输出文件，避免不同 CSV 共用游标而遗漏数据；不覆盖旧独立脚本的断点。
    output_id = hashlib.sha256(str(config.out_path.resolve()).encode('utf-8')).hexdigest()[:16]
    state_path = config.out_path.parent / '.checkpoints' / output_id / f'{bvid}.json'
    state = load_state(state_path, mode, include_replies, max_sub_pages,
                       reset=config.extra.get('reset_state', False) or not store.existed)
    if state['finished']:
        print(f'{bvid} 已到分页末尾；重新扫描可加 --reset-state', flush=True)
        return
    # 开始前保存当前安全游标，包含选项，失败后也能校验断点配置。
    save_state(state_path, state)
    print(f'\n开始采集视频：{bvid}', flush=True)
    aid, title = client.get_video_info(bvid)
    print(f'视频标题：{title}\n视频 aid：{aid}', flush=True)
    offset = state['offset']
    seen_offsets = {offset}
    for _ in range(config.extra.get('max_pages', 5)):
        print(f"正在采集第 {state['pages_done'] + 1} 页一级评论", flush=True)
        data = client.get_main_comments(aid, offset=offset, mode=mode)
        replies = data.get('replies') or []
        cursor = data.get('cursor') or {}
        if not isinstance(replies, list):
            raise CrawlStopped('一级评论格式异常')
        if not replies and not cursor.get('is_end'):
            raise CrawlStopped('当前页没有评论，但未收到结束标记')
        added = store.add(framework_record(item, bvid, aid, title) for item in replies)
        print(f'一级评论新增：{added} 条', flush=True)
        if include_replies:
            for item in replies:
                root = str(item.get('rpid_str') or item.get('rpid') or '')
                if not root or (int(item.get('rcount') or 0) <= 0 and not item.get('replies')):
                    continue
                for page in range(1, max_sub_pages + 1):
                    store.check_limit()
                    print(f'正在采集评论 {root} 的第 {page} 页楼中楼', flush=True)
                    sub_data = client.get_sub_comments(aid, root, page=page)
                    sub_replies = sub_data.get('replies') or []
                    if not isinstance(sub_replies, list):
                        raise CrawlStopped('二级评论格式异常')
                    count = store.add(framework_record(item, bvid, aid, title, 2, root)
                                      for item in sub_replies)
                    print(f'楼中楼新增：{count} 条', flush=True)
                    sub_total = int((sub_data.get('page') or {}).get('count') or 0)
                    if not sub_replies or len(sub_replies) < 20 or (sub_total and page * 20 >= sub_total):
                        break
        pagination = cursor.get('pagination_reply') or {}
        next_offset = pagination.get('next_offset')
        is_end = bool(cursor.get('is_end'))
        if not is_end and (not next_offset or next_offset in seen_offsets):
            raise CrawlStopped('下一页游标缺失或重复；数据已保存，当前页下次重扫')
        state = dict(mode=mode, include_replies=include_replies, max_sub_pages=max_sub_pages,
                     offset='' if is_end else next_offset,
                     pages_done=state['pages_done'] + 1, finished=is_end)
        save_state(state_path, state)
        offset = state['offset']
        seen_offsets.add(offset)
        print(f'本次累计新增：{len(store.items)} 条', flush=True)
        if is_end:
            print('当前可访问评论已到末尾', flush=True)
            break
        store.check_limit()


def main(argv=None):
    """兼容 python src/crawler/bilibili.py，统一交给框架入口。"""
    if __package__ in (None, ''):
        sys.path.insert(0, str(ROOT))
    from src.crawler.spider import main as framework_main
    return framework_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
