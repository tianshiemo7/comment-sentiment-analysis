"""Hide synthetic model features from display statistics, keeping training intact."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SPECIAL_TOKENS = frozenset({"<pad>", "<unk>"})


@lru_cache(maxsize=1)
def processing_markers() -> frozenset[str]:
    markers = {"标点分隔", "标点感叹", "标点疑问", "表情未知"}
    path = Path(__file__).resolve().parents[2] / "resources/emoji_map.json"
    try:
        mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        mapping = {}
    if isinstance(mapping, dict):
        markers.update(value for value in mapping.values() if isinstance(value, str) and value)
    return frozenset(markers)


def is_display_keyword(token: str) -> bool:
    """Exclude only known processing tokens, preserving content and negation."""
    return (isinstance(token, str) and bool(token.strip())
            and token.strip().lower() not in SPECIAL_TOKENS
            and token.strip() not in processing_markers())


@lru_cache(maxsize=1)
def _marker_pattern():
    markers = sorted(processing_markers() | SPECIAL_TOKENS, key=lambda word: (-len(word), word))
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, markers)) + r")(?!\w)", re.IGNORECASE)


def strip_processing_markers(text: str) -> str:
    """Remove whole markers before display tokenization, including its fallback."""
    return _marker_pattern().sub(" ", text)
