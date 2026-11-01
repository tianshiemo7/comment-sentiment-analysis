"""Hide synthetic model features from display statistics, keeping training intact."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
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
    """Exclude processing tokens and punctuation, preserving content and negation."""
    return (isinstance(token, str) and bool(token.strip())
            and any(char.isalnum() for char in token)
            and token.strip().lower() not in SPECIAL_TOKENS
            and token.strip() not in processing_markers())


def clean_keyword_groups(payload: object, *, precision: int = 4) -> dict[str, list[dict]]:
    """Filter persisted keyword lists on read without changing historical results.

    Older tasks can contain model markers even after the generation code is fixed.
    Normalize weights against the remaining words, keeping their original counts.
    This operates on copies and never reruns inference or writes to the cache.
    """
    groups = payload if isinstance(payload, Mapping) else {}
    result: dict[str, list[dict]] = {"positive": [], "negative": []}
    for label in result:
        entries = groups.get(label)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            word, count = entry.get("word"), entry.get("count")
            if (not is_display_keyword(word) or isinstance(count, bool)
                    or not isinstance(count, int) or count <= 0):
                continue
            kept.append({"word": word.strip(), "count": count})
        peak = max((entry["count"] for entry in kept), default=0)
        result[label] = [
            {**entry, "weight": round(entry["count"] / peak, precision)}
            for entry in kept
        ]
    return result


@lru_cache(maxsize=1)
def _marker_pattern():
    markers = sorted(processing_markers() | SPECIAL_TOKENS, key=lambda word: (-len(word), word))
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, markers)) + r")(?!\w)", re.IGNORECASE)


def strip_processing_markers(text: str) -> str:
    """Remove whole markers before display tokenization, including its fallback."""
    return _marker_pattern().sub(" ", text)
