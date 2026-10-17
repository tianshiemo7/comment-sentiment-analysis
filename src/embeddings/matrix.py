"""词表对齐的 embedding 矩阵构造。

模型 Embedding 层的权重矩阵形状为 ``[vocab_size, embed_dim]``，
行号与 `src.preprocessing.vocab.Vocab` 的 id 一一对应（0 = <pad>，1 = <unk>）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..preprocessing.tokenize import PAD_TOKEN, UNK_TOKEN

MATRIX_FILE = "embedding_matrix.npy"


def _require_numpy() -> Any:
    """导入并返回 numpy；缺失时抛出带安装提示的 ImportError。

    放在函数内是为了让 ``import src.embeddings`` 在最小依赖环境下也能成功。
    """
    try:
        import numpy  # noqa: PLC0415 - 函数内导入
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "构造词向量矩阵需要 numpy，请执行：python -m pip install numpy"
        ) from exc
    return numpy


def build_embedding_matrix(
    vocab,
    wv,
    embed_dim: int,
    *,
    seed: int = 42,
    oov: str = "mean",
) -> Any:
    """构造 ``[len(vocab), embed_dim]`` 的预训练权重矩阵。

    Args:
        vocab: :class:`~src.preprocessing.vocab.Vocab` 实例（提供 ``word2id``）。
        wv: gensim ``KeyedVectors``（提供 ``__contains__`` 与 ``__getitem__``）。
        embed_dim: 词向量维度，必须与 ``wv.vector_size`` 一致。
        seed: OOV 随机初始化的随机种子。
        oov: OOV 策略，``"mean"`` 用全表均值向量，``"zeros"`` 全零，``"random"`` 小随机数。

    Returns:
        ``np.ndarray``，形状 ``[len(vocab), embed_dim]``，``dtype=np.float32``。
    """
    numpy = _require_numpy()
    if oov not in {"mean", "zeros", "random"}:
        raise ValueError(f"未知 OOV 策略：{oov!r}，只支持 mean / zeros / random")
    word2id = getattr(vocab, "word2id", vocab)
    if not isinstance(word2id, dict) or PAD_TOKEN not in word2id or UNK_TOKEN not in word2id:
        raise ValueError("词表必须包含 <pad> 与 <unk>")
    vector_size = getattr(wv, "vector_size", None)
    if vector_size is not None and int(vector_size) != int(embed_dim):
        raise ValueError(
            f"词向量维度 {int(vector_size)} 与 embed_dim={int(embed_dim)} 不一致"
        )
    if isinstance(embed_dim, bool) or int(embed_dim) < 1:
        raise ValueError("embed_dim 必须为正整数")

    matrix = numpy.zeros((len(word2id), int(embed_dim)), dtype=numpy.float32)
    known_ids: list[int] = []
    for word, idx in word2id.items():
        if word in (PAD_TOKEN, UNK_TOKEN):
            continue
        if word in wv:
            matrix[idx] = numpy.asarray(wv[word], dtype=numpy.float32)
            known_ids.append(idx)

    if known_ids:
        mean_vector = matrix[known_ids].mean(axis=0)
    else:
        mean_vector = numpy.zeros(int(embed_dim), dtype=numpy.float32)
    if oov == "random":
        rng = numpy.random.default_rng(seed)
        fill_vector = rng.normal(0.0, 0.1, size=int(embed_dim)).astype(numpy.float32)
    elif oov == "mean":
        fill_vector = mean_vector.astype(numpy.float32)
    else:  # zeros
        fill_vector = numpy.zeros(int(embed_dim), dtype=numpy.float32)

    # <pad> 行必须为零向量，与 nn.Embedding(padding_idx=0) 配合
    matrix[word2id[PAD_TOKEN]] = 0.0
    covered = set(known_ids) | {word2id[PAD_TOKEN]}
    for _word, idx in word2id.items():
        if idx not in covered:
            matrix[idx] = fill_vector
    return matrix


def save_embedding_matrix(matrix: Any, path: str | Path) -> Path:
    """保存矩阵为 ``.npy``，返回路径（自动创建父目录）。"""
    numpy = _require_numpy()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.save(path, matrix)
    return path


def load_embedding_matrix(path: str | Path) -> Any:
    """加载 ``.npy`` embedding 矩阵。

    Raises:
        FileNotFoundError: 文件不存在（提示先运行词向量训练）。
    """
    numpy = _require_numpy()
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"embedding 矩阵不存在：{path}。请先运行 python -m src.embeddings 生成。"
        )
    matrix = numpy.load(path, allow_pickle=False)
    if matrix.ndim != 2:
        raise ValueError(f"embedding 矩阵应为二维，实际形状 {matrix.shape}")
    return matrix
