"""Deterministic cleaning retaining negation, emoji names and sentiment cues."""
from __future__ import annotations

import html
import json
import re
import unicodedata
from functools import lru_cache, partial
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping

RESOURCES_DIR = Path(__file__).resolve().parents[2] / "resources"
RULE_ORDER = ("remove_html", "remove_url", "normalize_emoji", "remove_special_chars", "normalize_space")
# Stop before Chinese punctuation/text rather than eating the following opinion.
_URL = re.compile(
    r"https?://[^\s<>\u3400-\u9fff，。！？；、\"']+|"
    r"www\.[^\s<>\u3400-\u9fff，。！？；、\"']+|"
    r"[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+|"
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org|io|tv|edu|gov)(?:/[^\s\u3400-\u9fff，。！？；、]*)?\b",
    re.IGNORECASE,
)
_UNKNOWN_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def remove_url(text: str) -> str:
    return _URL.sub(" ", text)


def remove_html(text: str) -> str:
    parser = _HTMLText()
    parser.feed(html.unescape(text))
    parser.close()
    return "".join(parser.parts)


def remove_special_chars(text: str, keep: str = "") -> str:
    return "".join(char if char.isalnum() or char.isspace() or char in keep else " " for char in text)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _default_emoji_map() -> dict[str, str]:
    return json.loads((RESOURCES_DIR / "emoji_map.json").read_text(encoding="utf-8-sig"))


def normalize_emoji(text: str, emoji_map: Mapping[str, str] | None = None) -> str:
    mapping = _default_emoji_map() if emoji_map is None else emoji_map
    if mapping:
        pattern = re.compile("|".join(re.escape(key) for key in sorted(mapping, key=lambda key: (-len(key), key))))
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
    text = normalize_emoji(text, emoji_map)
    text = re.sub(r"!+", " 标点感叹 ", text)
    text = re.sub(r"\?+", " 标点疑问 ", text)
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
