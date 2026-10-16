"""分词、停用词与 id 编码骨架。

对应需求：中文 jieba 分词 / 英文分词 + 小写化；去停用词；`max_len` 统一截断补齐。

约定：
- 特殊符号常量 :data:`PAD_TOKEN` / :data:`UNK_TOKEN` 与 `vocab.py` 保持一致；
- 分词结果以空格连接的字符串形式落盘到 `data/processed/*.csv` 的 `tokens` 列。
"""

from __future__ import annotations

import os
from pathlib import Path

PAD_TOKEN = "<pad>"
UNK_TOKEN = "<unk>"

#: 与 .env.example 保持一致
DEFAULT_MAX_LEN = int(os.getenv("MAX_LEN", "200"))
DEFAULT_STOPWORDS_PATH = Path("data") / "stopwords.txt"

#: 停用词表缺失时的兜底（极简中文停用词，仅保证流程可跑；正式实验请换成完整表）
FALLBACK_STOPWORDS = frozenset(
    "的 了 和 是 就 都 而 及 与 着 或 一个 没有 我们 你们 他们 它 我 你 他 这 那 也 很 还".split()
)


def load_stopwords(path: str | Path = DEFAULT_STOPWORDS_PATH) -> set[str]:
    """加载停用词表，返回词集合。

    Args:
        path: 停用词文件路径，每行一个词，``#`` 开头为注释。

    TODO(实现):
        - 文件存在：逐行读取、去空白、跳过空行与注释行，返回 ``set``；
        - 文件不存在：返回 :data:`FALLBACK_STOPWORDS` 的副本，并打印一次提示。
    """
    raise NotImplementedError("TODO: 尚未实现停用词加载")


def tokenize(text: str, *, lang: str = "zh", stopwords: set[str] | None = None) -> list[str]:
    """把清洗后的文本切分成词/字列表。

    Args:
        text: :func:`src.preprocessing.clean.clean_text` 的输出。
        lang: ``"zh"`` 用 jieba 精确模式；``"en"`` 用空格分词并小写化。
        stopwords: 停用词集合，命中则丢弃该词。

    Returns:
        词列表；空文本返回 ``[]``。

    TODO(实现):
        - ``lang == "zh"``：``import jieba``（函数内导入），``jieba.lcut(text)``；
        - ``lang == "en"``：``text.lower().split()``，可再按标点切分；
        - 过滤 ``len(token) <= 1`` 的纯符号与停用词；
        - 未安装 jieba 时抛出带安装提示的 ``ImportError``。
    """
    raise NotImplementedError("TODO: 尚未实现分词")


def encode_ids(
    tokens: list[str],
    vocab,
    max_len: int = DEFAULT_MAX_LEN,
    *,
    pad_token: str = PAD_TOKEN,
    unk_token: str = UNK_TOKEN,
) -> list[int]:
    """把词序列编码为定长 id 序列（截断 + 补齐）。

    Args:
        tokens: :func:`tokenize` 的输出。
        vocab: :class:`src.preprocessing.vocab.Vocab` 实例（需有 ``__getitem__`` / ``to_id``）。
        max_len: 统一长度（默认来自环境变量 ``MAX_LEN``，即 200）。
        pad_token / unk_token: 特殊符号，用于补齐与 OOV。

    Returns:
        长度为 ``max_len`` 的 int 列表。

    TODO(实现):
        1. 逐词查表，未登录词用 ``unk_token`` 的 id；
        2. 超长取前 ``max_len`` 个，不足用 ``pad_token`` 的 id 右补到 ``max_len``。
    """
    raise NotImplementedError("TODO: 尚未实现 id 编码与截断补齐")


def tokens_to_text(tokens: list[str]) -> str:
    """把词列表还原为空格连接的字符串，用于落盘到 `tokens` 列。"""
    return " ".join(tokens)


def text_to_tokens(tokens_text: str) -> list[str]:
    """解析落盘的 `tokens` 列。"""
    return tokens_text.split()
