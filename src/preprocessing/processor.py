"""Serializable training/inference preprocessing and model-ready NumPy arrays."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .clean import RESOURCES_DIR, clean_text
from .tokenize import (
    DEFAULT_MAX_LEN, DEFAULT_STOPWORDS_PATH, DEFAULT_USERDICT_PATH, PROTECTED_WORDS,
    encode_ids, get_tokenizer, load_stopwords, tokenize,
)

PIPELINE_VERSION = "1.0.0"
LABEL_MAP = {"negative": 0, "positive": 1}


@dataclass
class TextPreprocessor:
    """Snapshot resources once; reload the same JSON for inference on new comments."""
    lang: str
    max_len: int
    stopwords: set[str]
    userdict_text: str
    emoji_map: dict[str, str]

    def __post_init__(self):
        if self.lang not in {"zh", "en"}:
            raise ValueError("lang 必须为 zh 或 en")
        if isinstance(self.max_len, bool) or not isinstance(self.max_len, int) or self.max_len < 1:
            raise ValueError("max_len 必须为正整数")
        if not isinstance(self.emoji_map, dict) or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in self.emoji_map.items()
        ):
            raise ValueError("表情映射必须包含非空字符串键和值")
        self.stopwords = set(self.stopwords) - PROTECTED_WORDS

    @classmethod
    def from_resources(
        cls, *, lang: str = "zh", max_len: int = DEFAULT_MAX_LEN,
        stopwords_path: str | Path = DEFAULT_STOPWORDS_PATH,
        userdict_path: str | Path = DEFAULT_USERDICT_PATH,
        emoji_map_path: str | Path = RESOURCES_DIR / "emoji_map.json",
    ):
        return cls(lang, max_len, load_stopwords(stopwords_path),
                   Path(userdict_path).read_text(encoding="utf-8-sig"),
                   json.loads(Path(emoji_map_path).read_text(encoding="utf-8-sig")))

    def transform(self, text: str) -> tuple[str, list[str]]:
        cleaned = clean_text(text, emoji_map=self.emoji_map)
        tokenizer = get_tokenizer(self.userdict_text) if self.lang == "zh" else None
        return cleaned, tokenize(cleaned, lang=self.lang, stopwords=self.stopwords, tokenizer=tokenizer)

    def encode(self, text: str, vocab) -> dict:
        cleaned, tokens = self.transform(text)
        if not tokens:
            raise ValueError("清洗和分词后无有效内容")
        return self.encode_tokens(tokens, vocab) | {"text": cleaned, "tokens": tokens}

    def encode_tokens(self, tokens: list[str], vocab) -> dict:
        if not tokens:
            raise ValueError("不能把空词序列编码为有效模型输入")
        length = min(len(tokens), self.max_len)
        return {"input_ids": encode_ids(tokens, vocab, self.max_len),
                "attention_mask": [1] * length + [0] * (self.max_len - length),
                "length": length, "source_length": len(tokens), "truncated": len(tokens) > self.max_len}

    def to_dict(self) -> dict:
        return {"pipeline_version": PIPELINE_VERSION, "lang": self.lang, "max_len": self.max_len,
                "stopwords": sorted(self.stopwords), "protected_words": sorted(PROTECTED_WORDS),
                "userdict_text": self.userdict_text, "emoji_map": self.emoji_map}

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("pipeline_version") != PIPELINE_VERSION or payload.get("protected_words") != sorted(PROTECTED_WORDS):
            raise ValueError("预处理版本不兼容，请使用与训练相同的代码和资源")
        return cls(payload["lang"], payload["max_len"], set(payload["stopwords"]),
                   payload["userdict_text"], payload["emoji_map"])


def save_encoded_split(records, processor: TextPreprocessor, vocab, path: str | Path) -> Path:
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("生成训练数组需要 numpy：python -m pip install numpy") from exc
    samples = [processor.encode_tokens(record.tokens.split(), vocab) for record in records]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        input_ids=np.array([sample["input_ids"] for sample in samples], dtype=np.int64).reshape(-1, processor.max_len),
        attention_mask=np.array([sample["attention_mask"] for sample in samples], dtype=np.int64).reshape(-1, processor.max_len),
        lengths=np.array([sample["length"] for sample in samples], dtype=np.int64),
        source_lengths=np.array([sample["source_length"] for sample in samples], dtype=np.int64),
        labels=np.array([LABEL_MAP[record.label] for record in records], dtype=np.int64),
        comment_ids=np.array([str(record.meta["comment_id"]) for record in records], dtype=np.str_),
    )
    return path


def load_encoded_split(path: str | Path) -> dict:
    """Read without pickle; arrays can be passed directly to torch.from_numpy."""
    import numpy as np
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key] for key in archive.files}


def load_preprocessing_bundle(directory: str | Path):
    """Verify and load inference resources from a prepared data/processed directory.

    The directory can be copied alongside a future model checkpoint. No training
    data, fitting, global dictionaries, or mutable local resource files are used.
    """
    from .vocab import Vocab

    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name in ("vocab.json", "preprocessor.json", "label_map.json"):
        expected = manifest["files"].get(f"data/processed/{name}")
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if not expected or expected != actual:
            raise ValueError(f"预处理资源校验失败：{name}")
    labels = json.loads((directory / "label_map.json").read_text(encoding="utf-8"))
    if labels != LABEL_MAP:
        raise ValueError("标签映射与当前情感二分类约定不一致")
    return TextPreprocessor.load(directory / "preprocessor.json"), Vocab.load(directory / "vocab.json"), labels
