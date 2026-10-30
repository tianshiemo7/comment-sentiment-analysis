"""仅从训练语料构建可复现词表。

对应需求：词表 ``min_freq ≥ 3``，并统计词表规模（见 `stats.py`）。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .tokenize import PAD_TOKEN, UNK_TOKEN

DEFAULT_MIN_FREQ = int(os.getenv("MIN_FREQ", "3"))
DEFAULT_VOCAB_PATH = Path("data") / "processed" / "vocab.json"


@dataclass
class Vocab:
    """词表：``word -> id`` 双向映射。

    id 约定（必须保持稳定，模型与推理服务共用）::

        0 -> <pad>     # 补齐
        1 -> <unk>     # 未登录词（OOV）
        2..N -> 按词频降序的普通词

    Attributes:
        word2id: 词到 id 的映射。
        id2word: id 到词的映射（与 ``word2id`` 互逆）。
        min_freq: 建表时的最小词频。
        freqs: 原始词频统计（可选保留，便于分析）。
    """

    word2id: dict[str, int] = field(default_factory=dict)
    id2word: dict[int, str] = field(default_factory=dict)
    min_freq: int = DEFAULT_MIN_FREQ
    freqs: Counter = field(default_factory=Counter)

    def __len__(self) -> int:
        return len(self.word2id)

    def __contains__(self, word: str) -> bool:
        return word in self.word2id

    def __getitem__(self, word: str) -> int:
        """查词 id，未登录词返回 ``<unk>`` 的 id。"""
        return self.to_id(word)

    @property
    def pad_id(self) -> int:
        return self.word2id[PAD_TOKEN]

    @property
    def unk_id(self) -> int:
        return self.word2id[UNK_TOKEN]

    def to_id(self, word: str) -> int:
        """返回词 id；OOV 一律映射到 ``<unk>``。"""
        return self.word2id.get(word, self.word2id.get(UNK_TOKEN, 1))

    def to_word(self, idx: int) -> str:
        """返回 id 对应的词；越界返回 ``<unk>``。"""
        return self.id2word.get(idx, UNK_TOKEN)

    def to_dict(self) -> dict:
        """序列化为可 JSON 落盘的字典。"""
        return {
            "min_freq": self.min_freq,
            "word2id": self.word2id,
            "freqs": dict(self.freqs),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Vocab":
        """从 :meth:`to_dict` 的结果还原词表。"""
        word2id = {str(k): int(v) for k, v in payload.get("word2id", {}).items()}
        if (word2id.get(PAD_TOKEN) != 0 or word2id.get(UNK_TOKEN) != 1
                or sorted(word2id.values()) != list(range(len(word2id)))):
            raise ValueError("词表编号必须连续唯一，且 PAD=0、UNK=1")
        return cls(
            word2id=word2id,
            id2word={idx: word for word, idx in word2id.items()},
            min_freq=int(payload.get("min_freq", DEFAULT_MIN_FREQ)),
            freqs=Counter(payload.get("freqs", {})),
        )

    def save(self, path: str | Path = DEFAULT_VOCAB_PATH) -> Path:
        """写入 JSON 词表文件（``ensure_ascii=False`` 便于人工检查），返回路径。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_VOCAB_PATH) -> "Vocab":
        """从 JSON 词表文件加载。"""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)


def build_vocab(
    tokenized_texts: Iterable[list[str]],
    min_freq: int = DEFAULT_MIN_FREQ,
    max_size: int | None = None,
) -> Vocab:
    """统计词频并构建词表。

    Args:
        tokenized_texts: 已分词的语料，每个元素是一条评论的词列表。
        min_freq: 最小词频，低于该值的词不进入词表（需求要求 ``≥ 3``）。
        max_size: 词表上限（不含特殊符号），``None`` 表示不限制。

    Returns:
        :class:`Vocab` 实例。

    构建步骤:
        1. ``Counter`` 统计词频（跨全部语料）；
        2. 过滤 ``freq < min_freq``；
        3. 按 ``(-freq, word)`` 排序，``max_size`` 截断；
        4. 先放 ``<pad>=0``、``<unk>=1``，再依次编号；
        5. ``freqs`` 保留原始频率供 `stats.py` 统计。
    """
    if isinstance(min_freq, bool) or not isinstance(min_freq, int) or min_freq < 3:
        raise ValueError("min_freq 必须为不小于 3 的整数")
    if max_size is not None and (isinstance(max_size, bool) or not isinstance(max_size, int) or max_size < 0):
        raise ValueError("max_size 必须为非负整数或 None")
    frequencies = Counter(word for sentence in tokenized_texts for word in sentence
                          if word and word not in {PAD_TOKEN, UNK_TOKEN})
    words = sorted((word for word, count in frequencies.items() if count >= min_freq),
                   key=lambda word: (-frequencies[word], word))
    if max_size is not None:
        words = words[:max_size]
    word2id = {word: idx for idx, word in enumerate([PAD_TOKEN, UNK_TOKEN, *words])}
    return Vocab(word2id, {idx: word for word, idx in word2id.items()}, min_freq, frequencies)
