
import argparse
import csv
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

    def __init__(self, delay=2.5, timeout=15):

        self.delay = delay
        self.timeout = timeout

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                "CommentSentimentCourseProject/1.0"
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

    def request(self, path, params=None, allow_nav=False):
        """统一请求方法"""

        url = API + path

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
                    timeout=self.timeout
                )

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
# CSV 保存与去重
# =========================

class CsvStore:

    def __init__(self, path):

        self.path = Path(path)
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.ids = set()

        if self.path.exists():

            with self.path.open(
                "r",
                encoding="utf-8-sig",
                newline=""
            ) as f:

                reader = csv.DictReader(f)

                self.ids = {
                    row["rpid"]
                    for row in reader
                    if row.get("rpid")
                }

        else:

            with self.path.open(
                "w",
                encoding="utf-8-sig",
                newline=""
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=COLUMNS
                )

                writer.writeheader()

    def add(self, records):
        """追加保存，自动根据评论 ID 去重"""

        new_rows = []

        for row in records:

            if row is None:
                continue

            rpid = row["rpid"]

            if rpid in self.ids:
                continue

            self.ids.add(rpid)
            new_rows.append(row)

        if new_rows:

            with self.path.open(
                "a",
                encoding="utf-8",
                newline=""
            ) as f:

                writer = csv.DictWriter(
                    f,
                    fieldnames=COLUMNS
                )

                writer.writerows(new_rows)

        return len(new_rows)


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
# 核心爬虫
# =========================

def crawl_video(
    client,
    bvid,
    max_pages=5,
    include_replies=True,
    max_sub_pages=3,
    mode=2,
    reset_state=False
):

    print("\n" + "=" * 55)
    print(f"开始采集视频：{bvid}")

    aid, title = client.get_video_info(bvid)

    print(f"视频标题：{title}")
    print(f"视频 aid：{aid}")

    csv_path = SAVE_DIR / f"{bvid}_comments.csv"

    state_path = (
        SAVE_DIR / ".checkpoints" / f"{bvid}.json"
    )

    store = CsvStore(csv_path)

    state = load_state(
        state_path,
        mode,
        include_replies,
        max_sub_pages,
        reset=reset_state
    )

    if state["finished"]:
        print("该视频上次已采集到分页末尾")
        print("重新扫描请添加 --reset-state")
        return

    offset = state["offset"]

    total_new = 0

    for page_index in range(max_pages):

        print(f"\n正在采集第 {page_index + 1} 页一级评论")

        data = client.get_main_comments(
            aid,
            offset=offset,
            mode=mode
        )

        replies = data.get("replies") or []
        cursor = data.get("cursor") or {}

        if not isinstance(replies, list):
            raise CrawlStopped("一级评论格式异常")

        if not replies and not cursor.get("is_end"):
            raise CrawlStopped(
                "当前页没有评论，但未收到结束标记"
            )

        # 保存一级评论
        main_rows = []

        for item in replies:

            row = normalize_comment(
                item,
                bvid,
                aid,
                title,
                level=1
            )

            main_rows.append(row)

        added = store.add(main_rows)

        total_new += added

        print(f"一级评论新增：{added} 条")

        # =========================
        # 获取二级评论
        # =========================

        if include_replies:

            sub_added = 0

            for item in replies:

                root_id = str(
                    item.get("rpid_str")
                    or item.get("rpid")
                    or ""
                )

                if not root_id:
                    continue

                reply_count = int(
                    item.get("rcount") or 0
                )

                if reply_count <= 0 and not item.get("replies"):
                    continue

                for sub_page in range(
                    1,
                    max_sub_pages + 1
                ):

                    sub_data = client.get_sub_comments(
                        aid,
                        root_id,
                        page=sub_page
                    )

                    sub_replies = (
                        sub_data.get("replies") or []
                    )

                    if not isinstance(sub_replies, list):
                        raise CrawlStopped(
                            "二级评论格式异常"
                        )

                    rows = []

                    for sub_item in sub_replies:

                        row = normalize_comment(
                            sub_item,
                            bvid,
                            aid,
                            title,
                            level=2,
                            forced_root=root_id
                        )

                        rows.append(row)

                    count = store.add(rows)

                    sub_added += count

                    page_info = (
                        sub_data.get("page") or {}
                    )

                    sub_total = int(
                        page_info.get("count") or 0
                    )

                    if not sub_replies:
                        break

                    if len(sub_replies) < 20:
                        break

                    if (
                        sub_total > 0
                        and sub_page * 20 >= sub_total
                    ):
                        break

            total_new += sub_added

            print(f"二级评论新增：{sub_added} 条")

        # =========================
        # 获取下一页游标
        # =========================

        pagination = (
            cursor.get("pagination_reply") or {}
        )

        next_offset = pagination.get(
            "next_offset"
        )

        is_end = bool(
            cursor.get("is_end")
        )

        if (
            not is_end
            and (
                not next_offset
                or next_offset == offset
            )
        ):
            raise CrawlStopped(
                "无法获取下一页游标，"
                "已保存的数据不会丢失"
            )

        # 保存当前进度
        state = {
            "mode": mode,
            "include_replies": include_replies,
            "max_sub_pages": max_sub_pages,
            "offset": "" if is_end else next_offset,
            "pages_done": state["pages_done"] + 1,
            "finished": is_end
        }

        save_state(state_path, state)

        offset = state["offset"]

        print(
            f"当前 CSV 已保存：{len(store.ids)} 条评论"
        )

        if is_end:
            print("当前可访问评论已到末尾")
            break

    print("\n" + "-" * 55)
    print(f"本次新增：{total_new} 条")
    print(f"累计保存：{len(store.ids)} 条")
    print(f"CSV 文件：{csv_path}")
    print("-" * 55)


# =========================
# 命令行入口
# =========================

def main():

    # VS Code 输出面板或重定向运行时也及时显示进度。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="B站视频评论采集工具"
    )

    parser.add_argument(
        "--bvid",
        nargs="+",
        help="一个或多个视频 BV 号或链接；省略时提示输入"
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="每次最多采集多少页一级评论"
    )

    parser.add_argument(
        "--max-sub-pages",
        type=int,
        default=3,
        help="每条根评论最多采集多少页二级评论"
    )

    parser.add_argument(
        "--include-replies",
        action="store_true",
        help="是否采集楼中楼"
    )

    parser.add_argument(
        "--mode",
        choices=["time", "hot"],
        default="time"
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=2.5
    )

    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="从第一页重新扫描，保留 CSV 去重"
    )

    args = parser.parse_args()

    if not 1 <= args.max_pages <= 50:
        parser.error("--max-pages 必须为 1~50")

    if not 1 <= args.max_sub_pages <= 20:
        parser.error("--max-sub-pages 必须为 1~20")

    if args.delay < 2:
        parser.error("--delay 不能小于 2 秒")

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s"
    )

    print("B站评论采集工具已启动", flush=True)

    if not args.bvid:
        try:
            value = input("请输入 BV 号或视频链接（多个用空格分隔）：").strip()
        except (EOFError, OSError):
            parser.error(
                "当前运行窗口无法接收输入。请在 VS Code 终端运行，"
                "或通过 --bvid BV号 指定视频。"
            )
        except KeyboardInterrupt:
            print("\n已取消采集", flush=True)
            return

        if not value:
            parser.error("未输入 BV 号或视频链接")

        args.bvid = value.split()

    client = BiliClient(delay=args.delay)

    mode = 2 if args.mode == "time" else 3

    bvids = []

    for value in args.bvid:

        try:
            bvid = parse_bvid(value)

        except ValueError as e:
            print("BV 号错误：", e)
            return

        if bvid not in bvids:
            bvids.append(bvid)

    for bvid in bvids:

        try:
            crawl_video(
                client=client,
                bvid=bvid,
                max_pages=args.max_pages,
                include_replies=args.include_replies,
                max_sub_pages=args.max_sub_pages,
                mode=mode,
                reset_state=args.reset_state
            )

        except (
            CrawlStopped,
            ValueError,
            OSError
        ) as e:

            print(f"\n采集停止：{e}")
            print("已采集的数据和断点会保留")
            break


if __name__ == "__main__":
    main()
