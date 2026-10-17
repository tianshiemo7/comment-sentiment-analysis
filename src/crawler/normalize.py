"""评论文本与记录字段的标准化（正规化）。

目标：把不同站点、不同接口版本返回的评论整理成**同一种干净、可复现、可比较**的
形式，再交给下游 ``src/preprocessing``。所有函数都是纯函数，便于单元测试与在
离线基准中单独压测。

文本标准化做哪些事（顺序固定）：

1. ``html.unescape``：把 ``&amp;`` / ``&#39;`` / ``&nbsp;`` 等实体还原成字符；
2. Unicode **NFKC**：全角字母数字、兼容汉字等统一为常规形式（``Ａ`` → ``A``）；
3. 统一换行（``\\r\\n`` / ``\\r`` → ``\\n``）；
4. 删除零宽字符与双向控制字符（``U+200B``、``U+202E``、``U+FEFF`` 等），
   这些字符肉眼不可见，却会让去重、分词和模型统计出现「看起来一样的两条」；
5. 删除除换行/制表符以外的控制字符；
6. 合并连续空白、去掉行尾空格、最多保留一个空行，最后 ``strip``。

**刻意不做**的事：

- 不删除 emoji / B 站表情（``[笑哭]``、``[doge]``）：它们是情感信号，
  交给下游 ``resources/emoji_map.json`` 映射；
- 默认不删除 URL（``strip_urls=False``）：保留原文可追溯；需要时显式开启；
- 不做繁简转换、不做停用词删除：这些属于预处理阶段，爬虫只负责「同一格式」。
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping

#: 需要删除的零宽 / 双向控制 / BOM 字符
ZERO_WIDTH_CHARS = frozenset(
    "\u200b\u200c\u200d\u200e\u200f"  # ZWSP / ZWNJ / ZWJ / LRM / RLM
    "\u202a\u202b\u202c\u202d\u202e"  # 双向控制
    "\u2060\u2061\u2062\u2063\u2064"  # word joiner / invisible operators
    "\ufeff"                          # BOM / zero width no-break space
)

#: 中文标点保留集：NFKC 会把全角 ，！？ 等压成 ASCII，削弱原文语气与可读性，
#: 因此只对字母/数字/兼容字符做 NFKC，中文标点原样保留。
_CJK_PUNCTUATION = frozenset(
    "，。！？：；、（）【】《》〈〉「」『』〔〕“”‘’…—～·￥＃＠＆＊％‰"
)

#: URL 识别（仅在 ``strip_urls=True`` 时使用）
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'）】]+", re.IGNORECASE)


def _nfkc(text: str) -> str:
    """逐字符 NFKC：全角字母数字变半角，但保留中文标点。"""

    return "".join(
        char if char in _CJK_PUNCTUATION else unicodedata.normalize("NFKC", char)
        for char in text
    )


#: 常见 emoji / 符号区段（用于「纯 emoji 评论也算有意义文本」的判断）
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # 大多数 emoji
    "\U00002600-\U000027BF"  # 杂项符号 / 装饰符号
    "\U0001F000-\U0001F2FF"  # 麻将、扑克、表情
    "\U00002190-\U000021FF"  # 箭头
    "\U0000FE0F"             # variation selector-16
    "]"
)


def normalize_text(text: Any, *, strip_urls: bool = False) -> str:
    """把任意来源的评论文本标准化成单一只含可见字符的字符串。"""

    if text is None:
        return ""
    value = str(text)
    value = html.unescape(value)
    value = _nfkc(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if strip_urls:
        value = URL_RE.sub(" ", value)
    # NFKC 之后仍可能有控制字符；保留 \n 与 \t，其余 Cc/Cf 一律删除。
    value = "".join(
        char
        for char in value
        if char in ("\n", "\t")
        or unicodedata.category(char) not in ("Cc", "Cf")
    )
    value = "".join(char for char in value if char not in ZERO_WIDTH_CHARS)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def normalize_oneline(text: Any) -> str:
    """把标题 / UP 主名等「应为一行」的字段压成单行。"""

    return re.sub(r"\s+", " ", normalize_text(text)).strip()


def is_meaningful_text(text: str) -> bool:
    """判断标准化后的文本是否值得保留。

    纯标点 / 纯空白 / 纯零宽字符会被过滤；纯 emoji 保留（有情感信息）。
    """

    if not text:
        return False
    if any(char.isalnum() for char in text):
        return True
    return bool(_EMOJI_RE.search(text))


def to_int(value: Any, default: int = 0) -> int:
    """宽松地把接口返回值转成 int（``None`` / 空串 / 非法值回落到默认值）。"""

    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def utc_iso(timestamp: Any = None) -> str:
    """秒级 ISO8601 UTC 时间字符串；非法时间戳返回空串。"""

    if timestamp is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def normalize_rpid(value: Any) -> str:
    """评论 ID 统一为字符串（B 站 rpid 超过 2^53，不能走浮点）。"""

    if value is None:
        return ""
    value = str(value).strip()
    return "" if value in ("", "0", "None") else value


def normalize_comment(
    item: Mapping[str, Any],
    bvid: str,
    aid: int | str,
    title: str,
    level: int = 1,
    forced_root: str = "",
    *,
    strip_urls: bool = False,
    collected_at: str | None = None,
) -> dict[str, Any] | None:
    """把一个 B 站评论接口对象整理成统一下游字段。

    Args:
        item: ``/x/v2/reply/wbi/main`` 或 ``/x/v2/reply/reply`` 返回的单条评论。
        bvid: 视频 BV 号。
        aid: 视频 aid。
        title: 视频标题（会压成单行）。
        level: 1 = 一级评论，2 = 楼中楼。
        forced_root: 拉取楼中楼时已知的根评论 rpid（接口偶发不返回时会用它补全）。
        strip_urls: 是否删除评论里的 URL（默认保留）。
        collected_at: 采集时间；默认当前 UTC 时间，批量写入时传入同一时间便于审计。

    Returns:
        标准化后的记录；评论 ID 缺失或正文没有意义时返回 ``None``。
    """

    rpid = normalize_rpid(item.get("rpid_str") or item.get("rpid"))
    if not rpid:
        return None

    content = item.get("content")
    message = content.get("message") if isinstance(content, Mapping) else ""
    text = normalize_text(message, strip_urls=strip_urls)
    if not is_meaningful_text(text):
        return None

    if level == 1:
        root = ""
        parent = ""
    else:
        root = normalize_rpid(item.get("root_str") or item.get("root")) or forced_root
        if not root:
            return None
        parent = normalize_rpid(item.get("parent_str") or item.get("parent")) or root

    return {
        "bvid": bvid,
        "aid": str(aid),
        "video_title": normalize_oneline(title),
        "rpid": rpid,
        "level": int(level),
        "root_rpid": root,
        "parent_rpid": parent,
        "text": text,
        "likes": to_int(item.get("like")),
        "reply_count": to_int(item.get("rcount")),
        "ctime_utc": utc_iso(item.get("ctime")),
        "collected_at_utc": collected_at or utc_iso(),
    }


def normalize_record(record: Mapping[str, Any], *, strip_urls: bool = False) -> dict[str, Any] | None:
    """通用 HTML 爬虫记录标准化：只保证 ``text`` 有意义，其余字段原样保留。"""

    text = normalize_text(record.get("text"), strip_urls=strip_urls)
    if not is_meaningful_text(text):
        return None
    normalized = dict(record)
    normalized["text"] = text
    return normalized


def text_signature(text: str) -> str:
    """文本级去重签名（NFKC + 去空白 + casefold），用于统计重复率。"""

    return re.sub(r"\s+", "", normalize_text(text)).casefold()
