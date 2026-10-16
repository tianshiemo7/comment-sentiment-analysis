"""③ 词向量模块：Word2Vec / GloVe 训练与加载。

入口：`word2vec.py`（训练与加载）、`matrix.py`（构造 embedding 矩阵）。
gensim 只在函数内部导入，未安装时调用才报错。
"""

from .matrix import build_embedding_matrix, load_embedding_matrix
from .word2vec import evaluate_word2vec, load_word2vec, train_word2vec

__all__ = [
    "train_word2vec",
    "load_word2vec",
    "evaluate_word2vec",
    "build_embedding_matrix",
    "load_embedding_matrix",
]
