"""Original comment text for presentation, separate from normalized model input."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from ..visualization.keywords import strip_processing_markers


def original_comment_text(record: Mapping) -> str:
    """Clean-stage records retain raw_text; crawler records already have raw text."""
    for key in ("raw_text", "text_raw", "text"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def restore_top_comments(payload: object, source_records: Sequence = ()) -> dict[str, list[dict]]:
    """Resolve old cached normalized text to its unambiguous original, read-only.

    Source records come from clean.json (with raw_text) or crawl.json (original
    text). If source data is missing, remove complete synthetic markers rather
    than pretending to recover the original punctuation. Metadata and order stay
    unchanged, and a literal marker mentioned in an original comment is preserved.
    """
    from ..preprocessing.clean import clean_text

    originals: dict[str, set[str]] = defaultdict(set)
    for record in source_records:
        if not isinstance(record, Mapping):
            continue
        raw = original_comment_text(record)
        if not raw:
            continue
        originals[raw].add(raw)
        originals[clean_text(raw)].add(raw)
        text = record.get("text")
        if isinstance(text, str):
            originals[text].add(raw)

    groups = payload if isinstance(payload, Mapping) else {}
    result: dict[str, list[dict]] = {"positive": [], "negative": []}
    for label in result:
        entries = groups.get(label)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            text = str(entry.get("text") or "")
            matches = originals.get(text, set())
            if len(matches) == 1:
                text = next(iter(matches))
            else:
                filtered = strip_processing_markers(text)
                if filtered != text:
                    text = " ".join(filtered.split())
            result[label].append({**entry, "text": text})
    return result
