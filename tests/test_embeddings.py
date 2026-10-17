"""词向量与 embedding 矩阵测试（gensim / numpy 缺失时自动跳过）。"""

from __future__ import annotations

import pytest

numpy = pytest.importorskip("numpy")


class _FakeKeyedVectors:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.vector_size = len(next(iter(vectors.values())))

    def __contains__(self, word: str) -> bool:
        return word in self.vectors

    def __getitem__(self, word: str):
        return numpy.asarray(self.vectors[word], dtype=numpy.float32)


def _vocab():
    from src.preprocessing.vocab import Vocab

    word2id = {"<pad>": 0, "<unk>": 1, "好": 2, "坏": 3, "一般": 4}
    return Vocab(word2id, {index: word for word, index in word2id.items()}, 3)


def _fake_wv():
    return _FakeKeyedVectors(
        {"好": [1.0, 2.0, 3.0, 4.0], "坏": [2.0, 4.0, 6.0, 8.0]}
    )


def test_build_embedding_matrix_aligns_by_vocab_id_and_fills_oov():
    from src.embeddings.matrix import build_embedding_matrix

    matrix = build_embedding_matrix(_vocab(), _fake_wv(), 4, oov="mean")
    assert matrix.shape == (5, 4)
    assert matrix.dtype == numpy.float32
    assert numpy.allclose(matrix[0], 0.0)
    assert numpy.allclose(matrix[2], [1.0, 2.0, 3.0, 4.0])
    assert numpy.allclose(matrix[3], [2.0, 4.0, 6.0, 8.0])
    # <unk> 与「一般」都按均值向量填充
    expected_mean = numpy.array([1.5, 3.0, 4.5, 6.0], dtype=numpy.float32)
    assert numpy.allclose(matrix[1], expected_mean)
    assert numpy.allclose(matrix[4], expected_mean)


def test_build_embedding_matrix_oov_strategies_are_reproducible():
    from src.embeddings.matrix import build_embedding_matrix

    zeros = build_embedding_matrix(_vocab(), _fake_wv(), 4, oov="zeros")
    assert numpy.allclose(zeros[4], 0.0)

    first = build_embedding_matrix(_vocab(), _fake_wv(), 4, oov="random", seed=7)
    second = build_embedding_matrix(_vocab(), _fake_wv(), 4, oov="random", seed=7)
    assert numpy.array_equal(first, second)
    assert not numpy.allclose(first[4], 0.0)


def test_build_embedding_matrix_rejects_bad_dim_or_strategy():
    from src.embeddings.matrix import build_embedding_matrix

    with pytest.raises(ValueError):
        build_embedding_matrix(_vocab(), _fake_wv(), 8)
    with pytest.raises(ValueError):
        build_embedding_matrix(_vocab(), _fake_wv(), 4, oov="unknown")


def test_embedding_matrix_save_and_load(tmp_path):
    from src.embeddings.matrix import load_embedding_matrix, save_embedding_matrix

    matrix = numpy.arange(20, dtype=numpy.float32).reshape(5, 4)
    path = save_embedding_matrix(matrix, tmp_path / "embedding_matrix.npy")
    loaded = load_embedding_matrix(path)
    assert numpy.array_equal(loaded, matrix)
    with pytest.raises(FileNotFoundError):
        load_embedding_matrix(tmp_path / "missing.npy")


def test_word2vec_train_load_and_evaluate(tmp_path):
    pytest.importorskip("gensim")
    from src.embeddings.word2vec import (
        PARAMS_FILE,
        Word2VecParams,
        evaluate_word2vec,
        load_word2vec,
        train_word2vec,
    )

    sentences = [
        ["教程", "讲得", "清楚", "很好"],
        ["教程", "内容", "清楚", "满意"],
        ["画质", "清晰", "满意"],
        ["教程", "很好", "收藏"],
    ] * 6
    params = Word2VecParams(
        vector_size=8, window=2, min_count=1, epochs=3, workers=1, seed=42
    )
    path = train_word2vec(sentences, params, out_dir=tmp_path)
    assert path.is_file()
    assert (tmp_path / PARAMS_FILE).is_file()

    model = load_word2vec(path)
    result = evaluate_word2vec(model, ["教程", "未登录词"], topn=2)
    assert set(result) == {"教程"}
    assert len(result["教程"]) == 2


def test_word2vec_missing_model_raises(tmp_path):
    pytest.importorskip("gensim")
    from src.embeddings.word2vec import load_word2vec

    with pytest.raises(FileNotFoundError, match="Word2Vec 模型不存在"):
        load_word2vec(tmp_path / "missing.model")


def test_word2vec_requires_non_empty_corpus(tmp_path):
    pytest.importorskip("gensim")
    from src.embeddings.word2vec import train_word2vec

    with pytest.raises(ValueError):
        train_word2vec([], out_dir=tmp_path)
