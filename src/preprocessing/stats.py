"""Label, length, truncation and OOV statistics without fitting validation data."""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

STATS_PATH = Path("outputs") / "stats" / "dataset_stats.json"


def label_distribution(labels: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(label for label in labels if label is not None).items()))


def length_stats(tokenized_texts: Sequence[Sequence[str]]) -> dict[str, float]:
    lengths = sorted(len(sentence) for sentence in tokenized_texts)
    if not lengths:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p90": 0.0, "max": 0}
    position = (len(lengths) - 1) * .9
    lower, upper = math.floor(position), math.ceil(position)
    p90 = lengths[lower] + (lengths[upper] - lengths[lower]) * (position - lower)
    return {"count": len(lengths), "mean": statistics.mean(lengths),
            "median": statistics.median(lengths), "p90": p90, "max": lengths[-1]}


def dataset_stats(
    tokenized_texts: Sequence[Sequence[str]], labels: Sequence[str] | None = None,
    vocab=None, *, max_len: int | None = None,
    out_path: str | Path | None = STATS_PATH,
) -> dict:
    if labels is not None and len(labels) != len(tokenized_texts):
        raise ValueError("标签数量必须与文本数量一致")
    if max_len is not None and (isinstance(max_len, bool) or not isinstance(max_len, int) or max_len < 1):
        raise ValueError("max_len 必须为正整数")
    total = len(tokenized_texts)
    token_count = sum(map(len, tokenized_texts))
    oov_count = sum(word not in vocab for sentence in tokenized_texts for word in sentence) if vocab is not None else None
    payload = {
        "total": total, "vocab_size": len(vocab) if vocab is not None else None,
        "min_freq": getattr(vocab, "min_freq", None), "max_len": max_len,
        "length": length_stats(tokenized_texts),
        "labels": label_distribution(labels) if labels is not None else {},
        "truncated_ratio": sum(len(sentence) > max_len for sentence in tokenized_texts) / total
        if total and max_len is not None else 0.0,
        "token_count": token_count, "oov_count": oov_count,
        "oov_ratio": (oov_count / token_count if token_count else 0.0) if oov_count is not None else None,
    }
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_stats(path: str | Path = STATS_PATH) -> dict:
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
