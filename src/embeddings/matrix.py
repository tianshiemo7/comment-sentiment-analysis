"""词表对齐的 embedding 矩阵构造骨架。

模型 Embedding 层的权重矩阵形状为 ``[vocab_size, embed_dim]``，
行号与 `src.preprocessing.vocab.Vocab` 的 id 一一对应（0 = `<pad>`，1 = `<unk>`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
        embed_dim: 词向量维度。
        seed: OOV 初始化的随机种子。
        oov: OOV 策略，``"mean"`` 用全表均值向量，``"zeros"`` 全零，``"random"`` 小随机数。

    Returns:
        ``np.ndarray``，``dtype=np.float32``。
    TODO(实现):
        1. 先取所有已登录词的向量算均值；
        2. ``<pad>` 行置零向量（与 ``padding_idx=0`` 配合）；
        3. 其余 OOV 词按 ``oov`` 策略填充；
        4. 返回 ``float32`` 矩阵。
    """
    raise NotImplementedError("TODO: 尚未实现 embedding 矩阵构造")


def save_embedding_matrix(matrix: Any, path: str | Path) -> Path:
    """保存矩阵为 ``.npy``，返回路径（自动创建父目录）。"""
    numpy = _require_numpy()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.save(path, matrix)
    return path


def load_embedding_matrix(path: str | Path) -> Any:
    """加载 ``.npy`` embedding 矩阵。

    TODO(实现): ``np.load(path)``；文件不存在抛 ``FileNotFoundError`` 并给出提示。
    """
    raise NotImplementedError("TODO: 尚未实现 embedding 矩阵加载")
