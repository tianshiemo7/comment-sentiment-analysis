"""Word2Vec 训练与加载骨架。

对应需求：记录维度、窗口大小、最小词频等参数。
本模块所有 gensim 导入都放在函数内部，未安装 gensim 时仅调用会报错。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

WORD2VEC_DIR = Path("models") / "word2vec"
MODEL_FILE = "word2vec.model"
PARAMS_FILE = "word2vec_params.json"


@dataclass
class Word2VecParams:
    """Word2Vec 超参数（默认值参考常用中文短文本配置，可按实验调整）。

    Attributes:
        vector_size: 词向量维度。
        window: 上下文窗口大小。
        min_count: 最小词频（与词表 `min_freq ≥ 3` 对应）。
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


def train_word2vec(
    sentences: Iterable[Sequence[str]],
    params: Word2VecParams | None = None,
    *,
    out_dir: str | Path = WORD2VEC_DIR,
) -> Path:
    """在分词语料上训练 Word2Vec 并保存。

    Args:
        sentences: 分词后的句子集合（``Iterable[list[str]]``）。
        params: 超参数，缺省用 :class:`Word2VecParams` 默认值。
        out_dir: 输出目录，默认 ``models/word2vec/``。

    Returns:
        保存后的模型文件路径。

    TODO(实现):
        1. ``from gensim.models import Word2Vec``（函数内导入）；
        2. 用 ``params`` 构造并 ``train``（语料只迭代一次，注意 gensim 需要可重复迭代的列表）；
        3. 保存 ``out_dir/word2vec.model``；
        4. 把 :meth:`Word2VecParams.to_dict` 写入 ``out_dir/word2vec_params.json``（供报告引用）。
    """
    raise NotImplementedError("TODO: 尚未实现 Word2Vec 训练")


def load_word2vec(path: str | Path = WORD2VEC_DIR / MODEL_FILE):
    """加载已训练的 Word2Vec 模型。

    TODO(实现): ``Word2Vec.load(str(path))``；文件不存在时抛 ``FileNotFoundError``
    并提示「请先运行 scripts/train.sh 或 src.embeddings.word2vec.train_word2vec」。
    """
    raise NotImplementedError("TODO: 尚未实现 Word2Vec 加载")


def evaluate_word2vec(model, words: Sequence[str], topn: int = 10) -> dict[str, list[str]]:
    """相似词自检：返回 ``{word: [近义词...]}``，用于报告中的定性分析。

    TODO(实现): ``model.wv.most_similar(word, topn=topn)``；OOV 词跳过并记录。
    """
    raise NotImplementedError("TODO: 尚未实现相似词评估")
