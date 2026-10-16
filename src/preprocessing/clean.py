"""文本清洗规则骨架。

对应需求：去 URL、HTML 标签、特殊符号；去重复样本、空样本。
本模块只做「文本 → 文本」的规则处理，不做分词（分词见 `tokenize.py`）。
"""

from __future__ import annotations

import re
from typing import Iterable

#: 清洗规则占位：实现时按下面顺序应用到 `clean_text`
#: 1) 去 HTML 标签 2) 去 URL 3) 去特殊符号 4) 全角转半角（可选）5) 压缩空白
RULE_ORDER = ("remove_html", "remove_url", "remove_special_chars", "normalize_space")


def remove_url(text: str) -> str:
    """删除文本中的 URL（http/https、www、裸域名、邮件地址）。

    TODO(实现): 建议正则
        ``r"(https?://\\S+|www\\.\\S+|[\\w.+-]+@[\\w-]+\\.[\\w.]+)"`` → 替换为空格。
    """
    raise NotImplementedError("TODO: 尚未实现 URL 去除")


def remove_html(text: str) -> str:
    """删除 HTML 标签与常见实体（``&nbsp;`` / ``&amp;`` 等）。

    TODO(实现): 用 ``re.sub(r"<[^>]+>", " ", text)`` 去标签，
    再 ``html.unescape`` 还原实体，最后把 ``<br>``/``<p>`` 造成的换行压成空格。
    """
    raise NotImplementedError("TODO: 尚未实现 HTML 标签去除")


def remove_special_chars(text: str, keep: str = "") -> str:
    """删除特殊符号，只保留中文、英文、数字与空白。

    Args:
        keep: 额外允许保留的字符集合（例如 ``"，。！？"``，视实验效果而定）。

    TODO(实现): 用 ``re.sub`` 保留 ``[\\u4e00-\\u9fa5A-Za-z0-9\\s] + keep``，
    其余字符替换为空格；连续标点不要连成噪声。
    """
    raise NotImplementedError("TODO: 尚未实现特殊符号去除")


def normalize_space(text: str) -> str:
    """把任意连续空白压缩为单个空格并去除首尾空白。"""
    return re.sub(r"\s+", " ", text).strip()


def clean_text(text: str, *, lower: bool = True, keep: str = "") -> str:
    """按 :data:`RULE_ORDER` 依次清洗单条评论。

    Args:
        text: 原始评论文本。
        lower: 是否把英文字母统一小写（英文分词要求小写化）。
        keep: 传给 :func:`remove_special_chars` 的额外保留字符。

    Returns:
        清洗后的文本（不含分词结果）。

    TODO(实现): 串起 4 条规则；对 ``None`` / 非字符串输入返回空串。
    """
    raise NotImplementedError("TODO: 尚未实现清洗主流程")


def deduplicate(texts: Iterable[str], *, strip: bool = True) -> list[str]:
    """去重并过滤空样本，保持首次出现顺序。

    TODO(实现): 用 ``dict.fromkeys`` 保序去重；``strip`` 后为空串的样本丢弃。
    """
    raise NotImplementedError("TODO: 尚未实现去重")


def build_cleaner(lower: bool = True, keep: str = ""):
    """返回一个 ``text -> text`` 的清洗函数，供 pandas ``Series.map`` 使用。

    TODO(实现): 用 ``functools.partial`` 固定 ``clean_text`` 的参数。
    """
    raise NotImplementedError("TODO: 尚未实现清洗器工厂")
