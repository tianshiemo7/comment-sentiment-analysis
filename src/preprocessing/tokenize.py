"""Shared tokenization, protected stopwords, and fixed-length ID encoding."""
from __future__ import annotations

import io
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from .clean import RESOURCES_DIR

PAD_TOKEN = "<pad>"
UNK_TOKEN = "<unk>"
DEFAULT_MAX_LEN = int(os.getenv("MAX_LEN", "200"))
DEFAULT_STOPWORDS_PATH = RESOURCES_DIR / "stopwords.txt"
DEFAULT_USERDICT_PATH = RESOURCES_DIR / "userdict.txt"
PROTECTED_WORDS = frozenset((
    "不 没 没有 无 未 别 并非 不是 不太 不能 不会 不好 但是 但 却 不过 然而 很 非常 太 更 最 "
    "no not never neither nor hardly without but however very"
).split())
FALLBACK_STOPWORDS = frozenset("的 了 和 是 而 及 与 着 或 一个 我们 你们 他们 它 我 你 他 这 那 也".split())
logger = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def _warn_missing_stopwords(path: str) -> None:
    logger.warning("停用词文件不存在：%s，使用内置最小表", path)


def load_stopwords(path: str | Path = DEFAULT_STOPWORDS_PATH) -> set[str]:
    path = Path(path)
    if not path.is_file():
        _warn_missing_stopwords(str(path))
        return set(FALLBACK_STOPWORDS) - PROTECTED_WORDS
    words = {
        line.strip().lower() for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return words - PROTECTED_WORDS


@lru_cache(maxsize=8)
def get_tokenizer(userdict_text: str):
    """Resource snapshots do not mutate jieba's global dictionary."""
    try:
        import jieba
    except ImportError as exc:
        raise ImportError("中文分词需要 jieba：python -m pip install jieba") from exc
    instance = jieba.Tokenizer()
    instance.load_userdict(io.StringIO(userdict_text))
    for word in PROTECTED_WORDS:
        instance.add_word(word, freq=10000)
    return instance


def tokenize(
    text: str, *, lang: str = "zh", stopwords: set[str] | None = None,
    tokenizer=None,
) -> list[str]:
    if lang not in {"zh", "en"}:
        raise ValueError("lang 必须为 zh 或 en")
    if not isinstance(text, str) or not text.strip():
        return []
    if lang == "zh":
        if tokenizer is None:
            tokenizer = get_tokenizer(DEFAULT_USERDICT_PATH.read_text(encoding="utf-8-sig"))
        pieces = tokenizer.lcut(text)
    else:
        pieces = re.findall(r"[^\W_]+(?:'[^\W_]+)?", text.lower(), flags=re.UNICODE)
    stops = {word.lower() for word in stopwords or set()} - PROTECTED_WORDS
    return [word for piece in pieces if (word := piece.strip().lower())
            and any(char.isalnum() for char in word) and word not in stops]


def encode_ids(
    tokens: list[str], vocab, max_len: int = DEFAULT_MAX_LEN, *,
    pad_token: str = PAD_TOKEN, unk_token: str = UNK_TOKEN,
) -> list[int]:
    if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len < 1:
        raise ValueError("max_len 必须为正整数")
    mapping = vocab.word2id if hasattr(vocab, "word2id") else vocab
    if pad_token not in mapping or unk_token not in mapping:
        raise ValueError("词表必须包含 PAD 和 UNK 标记")
    ids = [mapping.get(word, mapping[unk_token]) for word in tokens[:max_len]]
    return ids + [mapping[pad_token]] * (max_len - len(ids))


def tokens_to_text(tokens: list[str]) -> str:
    return " ".join(tokens)


def text_to_tokens(tokens_text: str) -> list[str]:
    return tokens_text.split()
