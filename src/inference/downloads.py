"""下载响应的 HTTP 头构造（RFC 5987，中文文件名安全）。

浏览器下载的两个硬要求：

1. ``Content-Disposition: attachment``，文件名由**服务端拼接**（绝不含用户上传的原始
   文件名），非 ASCII 名用 RFC 5987 的 ``filename*=UTF-8''...`` 表达，同时给一个
   ASCII 兜底 ``filename="..."`` 供老浏览器使用；
2. ``Cache-Control: no-store``（导出内容随处理状态变化，不允许浏览器缓存旧结果）。

文件名里的引号 / 分隔符 / 换行统一过滤：``filename`` 兜底名做字符白名单，
``filename*`` 的值做百分号编码，避免头注入。
"""

from __future__ import annotations

from urllib.parse import quote

#: 兜底文件名（过滤后为空时使用）
DEFAULT_FILENAME = "download.csv"

#: ASCII 兜底名里不允许出现的字符（路径分隔符 + 引号 + 头注入相关字符）
_UNSAFE_FILENAME_CHARS = '\\/:*?"<>|\r\n'


def attachment_headers(filename: str) -> dict[str, str]:
    """返回附件下载头：ASCII 兜底名 + UTF-8 ``filename*``，并禁止缓存与嗅探。"""
    fallback = "".join(
        char for char in str(filename) if char.isascii() and char not in _UNSAFE_FILENAME_CHARS
    ).strip() or DEFAULT_FILENAME
    return {
        "Content-Disposition": (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(str(filename))}"
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


__all__ = ["DEFAULT_FILENAME", "attachment_headers"]
