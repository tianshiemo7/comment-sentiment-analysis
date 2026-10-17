"""词向量命令行入口：训练 Word2Vec 并生成词表对齐的 embedding 矩阵。

用法::

    python -m src.embeddings --train-file data/splits/train.csv \
        --vocab data/processed/vocab.json --out models/word2vec
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..preprocessing.dataset import load_records
from ..preprocessing.processor import TextPreprocessor
from ..preprocessing.vocab import Vocab
from .matrix import MATRIX_FILE, build_embedding_matrix, save_embedding_matrix
from .word2vec import PARAMS_FILE, Word2VecParams, load_word2vec, train_word2vec

DEFAULT_TRAIN = Path("data") / "splits" / "train.csv"
DEFAULT_VOCAB = Path("data") / "processed" / "vocab.json"
DEFAULT_PREPROCESSOR = Path("data") / "processed" / "preprocessor.json"
DEFAULT_OUT = Path("models") / "word2vec"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.embeddings",
        description="在训练集分词语料上训练 Word2Vec，并生成词表对齐的 embedding 矩阵",
    )
    parser.add_argument("--train-file", default=str(DEFAULT_TRAIN))
    parser.add_argument("--vocab", default=str(DEFAULT_VOCAB))
    parser.add_argument("--preprocessor", default=str(DEFAULT_PREPROCESSOR))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--embed-dim", type=int, default=128)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--sg", type=int, default=1, choices=[0, 1])
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--oov", default="mean", choices=["mean", "zeros", "random"])
    return parser


def _sentences(records, processor):
    sentences: list[list[str]] = []
    for record in records:
        tokens = record.tokens.split() if isinstance(record.tokens, str) else list(record.tokens or [])
        if not tokens and processor is not None:
            _cleaned, tokens = processor.transform(record.text)
        if tokens:
            sentences.append(tokens)
    return sentences


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        train_path = Path(args.train_file)
        if not train_path.is_file():
            raise FileNotFoundError(
                f"训练集不存在：{train_path}。请先运行 python -m src.preprocessing --input <标注CSV>。"
            )
        processor = (
            TextPreprocessor.load(Path(args.preprocessor))
            if Path(args.preprocessor).is_file()
            else None
        )
        records = load_records(train_path)
        sentences = _sentences(records, processor)
        if not sentences:
            raise ValueError("训练集没有可用的分词语料")

        params = Word2VecParams(
            vector_size=args.embed_dim,
            window=args.window,
            min_count=args.min_count,
            sg=args.sg,
            epochs=args.epochs,
            workers=args.workers,
            seed=args.seed,
        )
        out_dir = Path(args.out)
        model_path = train_word2vec(sentences, params, out_dir=out_dir)
        summary = {
            "sentences": len(sentences),
            "model": str(model_path),
            "params": str(out_dir / PARAMS_FILE),
        }

        vocab_path = Path(args.vocab)
        if vocab_path.is_file():
            vocab = Vocab.load(vocab_path)
            model = load_word2vec(model_path)
            matrix = build_embedding_matrix(
                vocab, model.wv, args.embed_dim, seed=args.seed, oov=args.oov
            )
            matrix_path = save_embedding_matrix(matrix, out_dir / MATRIX_FILE)
            coverage = sum(1 for word in vocab.word2id if word in model.wv)
            summary.update({
                "embedding_matrix": str(matrix_path),
                "matrix_shape": list(matrix.shape),
                "vocab_covered": coverage,
                "vocab_size": len(vocab),
                "oov_strategy": args.oov,
            })
        else:
            print(f"[embeddings] 跳过矩阵构造：词表不存在 {vocab_path}", file=sys.stderr)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"词向量训练失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 手动执行入口
    raise SystemExit(main())
