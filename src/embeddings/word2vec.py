"""Word2Vec 训练、加载与相似词自检。

对应需求：记录维度、窗口大小、最小词频等参数（写入 word2vec_params.json）。
本模块所有 gensim 导入都放在函数内部，未安装 gensim 时仅调用会报错。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

WORD2VEC_DIR = Path("models") / "word2vec"
MODEL_FILE = "word2vec.model"
PARAMS_FILE = "word2vec_params.json"

logger = logging.getLogger(__name__)


@dataclass
class Word2VecParams:
    """Word2Vec 超参数（默认值参考常用中文短文本配置，可按实验调整）。

    Attributes:
        vector_size: 词向量维度。
        window: 上下文窗口大小。
        min_count: 最小词频（与词表 min_freq ≥ 3 对应）。
        sg: 1 = Skip-gram，0 = CBOW。
        negative: 负采样个数。
        epochs: 训练轮数。
        workers: 并行线程数。
        seed: 随机种子，保证可复现。
    """

    vector_size: int = 128
    window: int = 5
    min_count: int = 3
    sg: int = 1
    negative: int = 5
    epochs: int = 10
    workers: int = 4
    seed: int = 42

    def to_dict(self) -> dict:
        return asdict(self)


def _require_gensim():
    try:
        from gensim.models import Word2Vec  # noqa: PLC0415 - 函数内导入，避免导入期依赖
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "训练/加载 Word2Vec 需要 gensim，请执行：python -m pip install gensim"
        ) from exc
    return Word2Vec


def train_word2vec(
    sentences: Iterable[Sequence[str]],
    params: Word2VecParams | None = None,
    *,
    out_dir: str | Path = WORD2VEC_DIR,
) -> Path:
    """在分词语料上训练 Word2Vec 并保存，返回模型文件路径。

    Args:
        sentences: 分词后的句子集合（``Iterable[list[str]]``）。
        params: 超参数，缺省用 :class:`Word2VecParams` 默认值。
        out_dir: 输出目录，默认 ``models/word2vec/``。

    Returns:
        保存后的模型文件路径（``models/word2vec/word2vec.model``）。
        同目录还会写入 ``word2vec_params.json``（超参 + 语料规模），供报告引用。
    """
    Word2Vec = _require_gensim()
    params = params or Word2VecParams()
    # gensim 需要可重复迭代的语料：一次性物化成 list[list[str]]
    corpus = [list(sentence) for sentence in sentences if sentence]
    if not corpus:
        raise ValueError("训练 Word2Vec 的语料为空")
    model = Word2Vec(
        sentences=corpus,
        vector_size=params.vector_size,
        window=params.window,
        min_count=params.min_count,
        sg=params.sg,
        negative=params.negative,
        epochs=params.epochs,
        workers=params.workers,
        seed=params.seed,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / MODEL_FILE
    model.save(str(model_path))
    payload = params.to_dict() | {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_sentences": len(corpus),
        "corpus_tokens": sum(len(sentence) for sentence in corpus),
        "vocab_size": len(model.wv),
    }
    (out / PARAMS_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return model_path


def load_word2vec(path: str | Path = WORD2VEC_DIR / MODEL_FILE):
    """加载已训练的 Word2Vec 模型。

    Raises:
        FileNotFoundError: 模型文件不存在（提示先训练）。
    """
    Word2Vec = _require_gensim()
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Word2Vec 模型不存在：{path}。请先运行 "
            "python -m src.embeddings（或 bash scripts/train.sh）训练词向量。"
        )
    return Word2Vec.load(str(path))


def evaluate_word2vec(model, words: Sequence[str], topn: int = 10) -> dict[str, list[str]]:
    """相似词自检：返回 ``{word: [近义词...]}``，用于报告中的定性分析。

    OOV 词（未登录到词向量表）跳过，并在日志里记录，不中断评估。
    """
    result: dict[str, list[str]] = {}
    missing: list[str] = []
    for word in words:
        if word not in model.wv:
            missing.append(word)
            continue
        result[word] = [item for item, _score in model.wv.most_similar(word, topn=topn)]
    if missing:
        logger.warning("相似词评估跳过 OOV 词 %d 个：%s", len(missing), ", ".join(missing))
    return result
