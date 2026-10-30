"""Deterministic cleaning retaining negation, emoji names and sentiment cues."""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache, partial
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping

RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources"
RULE_ORDER = ("remove_html", "remove_url", "normalize_english_negation", "normalize_emoji", "remove_special_chars", "normalize_space")
# Stop before Chinese punctuation/text rather than eating the following opinion.
_URL = re.compile(
    r"https?://[^\s<>\u3400-\u9fff，。！？；、\"']+|"
    r"www\.[^\s<>\u3400-\u9fff，。！？；、\"']+|"
    r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+|"
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|io|tv|edu|gov)(?:/[^\s\u3400-\u9fff，。！？；、]*)?\b",
    re.IGNORECASE,
)
_UNKNOWN_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\u00A9\u00AE]")
_SCRIPT_STYLE_BLOCK = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_TAG = re.compile(r"</?(?:script|style)\b[^>]*>", re.IGNORECASE)
_NEGATIVE_CONTRACTION = re.compile(r"\b(?:can't|won't|shan't|ain't|cannot)\b|\b([a-z]+)n't\b", re.IGNORECASE)
_NEGATIVE_BASES = {"can't": "can", "won't": "will", "shan't": "shall", "ain't": "is", "cannot": "can"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.parts.append(" ")

    def handle_endtag(self, tag):
        self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def remove_url(text: str) -> str:
    return _URL.sub(" ", text)


def remove_html(text: str) -> str:
    """Strip markup, decode entities once, and retain text after unclosed tags.

    Complete script/style blocks are removed first. Unmatched script/style tags
    are stripped without putting HTMLParser into an unterminated CDATA mode.
    Escaped code such as &lt;script&gt; remains comment text, never reparsed.
    """
    text = _SCRIPT_STYLE_BLOCK.sub(" ", text)
    text = _SCRIPT_STYLE_TAG.sub(" ", text)
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def normalize_english_negation(text: str) -> str:
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "ʼ": "'"}))
    def expand(match):
        original = match.group()
        base = _NEGATIVE_BASES.get(original.lower(), match.group(1))
        replacement = f"{base} not"
        if original.isupper():
            return replacement.upper()
        return replacement.capitalize() if original[0].isupper() else replacement
    return _NEGATIVE_CONTRACTION.sub(expand, text)


def remove_special_chars(text: str, keep: str = "") -> str:
    return "".join(char if char.isalnum() or char.isspace() or char in keep else " " for char in text)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _default_emoji_map() -> dict[str, str]:
    return json.loads((RESOURCES_DIR / "emoji_map.json").read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=16)
def _emoji_pattern(keys: tuple[str, ...]):
    return re.compile("|".join(re.escape(key) for key in keys))


def normalize_emoji(text: str, emoji_map: Mapping[str, str] | None = None) -> str:
    mapping = _default_emoji_map() if emoji_map is None else emoji_map
    if mapping:
        pattern = _emoji_pattern(tuple(sorted(mapping, key=lambda key: (-len(key), key))))
        text = pattern.sub(lambda match: f" {mapping[match.group()]} ", text)
    return _UNKNOWN_EMOJI.sub(" 表情未知 ", text).replace("\ufe0f", "").replace("\u200d", "")


def clean_text(
    text: str, *, lower: bool = True, keep: str = "",
    emoji_map: Mapping[str, str] | None = None,
) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = remove_url(remove_html(text))
    text = normalize_english_negation(text)
    text = normalize_emoji(text, emoji_map)
    text = re.sub(r"!+", " 标点感叹 ", text)
    text = re.sub(r"\?+", " 标点疑问 ", text)
    text = re.sub(r"[,，、;；]+", " 标点分隔 ", text)
    text = "".join(" " if unicodedata.category(char) in {"Cf", "Cc"} else char for char in text)
    text = remove_special_chars(text, keep)
    return normalize_space(text.lower() if lower else text)


def deduplicate(texts: Iterable[str], *, strip: bool = True) -> list[str]:
    result, seen = [], set()
    for text in texts:
        if not isinstance(text, str):
            continue
        text = text.strip() if strip else text
        if text.strip() and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def build_cleaner(lower: bool = True, keep: str = ""):
    return partial(clean_text, lower=lower, keep=keep)
