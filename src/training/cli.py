"""训练命令行入口。

用法::

    python -m src.training.cli --model textcnn --epochs 20 --batch-size 64
    python -m src.training.cli --model bilstm_attention --lr 1e-3 --seed 42

一站式跑完：加载预处理产物 → 训练（早停 + best 检查点）→ 测试集评估 →
指标/错误样例落盘 → 训练曲线、混淆矩阵、正负词云出图。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import TrainConfig

DEFAULT_PROCESSED_DIR = Path("data") / "processed"


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m src.training.cli",
        description="训练 TextCNN / BiLSTM-Attention 情感分类模型（训练 + 测试评估 + 出图）",
    )
    parser.add_argument("--model", default="textcnn", choices=["textcnn", "bilstm_attention"])
    parser.add_argument("--train", default=str(Path("data") / "splits" / "train.csv"))
    parser.add_argument("--val", default=str(Path("data") / "splits" / "val.csv"))
    parser.add_argument("--test", default=str(Path("data") / "splits" / "test.csv"))
    parser.add_argument("--vocab", default=str(DEFAULT_PROCESSED_DIR / "vocab.json"))
    parser.add_argument("--preprocessor", default=str(DEFAULT_PROCESSED_DIR / "preprocessor.json"))
    parser.add_argument("--embeddings", default=None, help="预训练词向量矩阵 .npy（可选）")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--max-len", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--embed-dim", type=int, default=128)
    parser.add_argument("--num-filters", type=int, default=100)
    parser.add_argument("--filter-sizes", type=int, nargs="+", default=[2, 3, 4])
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--num-layers", type=int, default=1)
    parser.add_argument("--attention-dim", type=int, default=64)
    parser.add_argument("--dropout", type=float, default=0.5)
    parser.add_argument("--checkpoint-dir", default=str(Path("models") / "checkpoints"))
    parser.add_argument("--log-dir", default=str(Path("outputs") / "logs"))
    parser.add_argument("--figures-dir", default=str(Path("outputs") / "figures"))
    parser.add_argument("--stats-dir", default=str(Path("outputs") / "stats"))
    parser.add_argument("--predictions-dir", default=str(Path("outputs") / "predictions"))
    parser.add_argument("--no-figures", action="store_true", help="只训练与评估，不出图")
    return parser


def _config_from_args(args: argparse.Namespace) -> TrainConfig:
    config = TrainConfig(
        model_name=args.model,
        train_path=Path(args.train),
        val_path=Path(args.val),
    )
    if args.epochs is not None:
        config.epochs = args.epochs
    if args.batch_size is not None:
        config.batch_size = args.batch_size
    if args.lr is not None:
        config.learning_rate = args.lr
    if args.max_len is not None:
        config.max_len = args.max_len
    if args.seed is not None:
        config.seed = args.seed
    if args.patience is not None:
        config.patience = args.patience
    if args.weight_decay is not None:
        config.weight_decay = args.weight_decay
    config.device = args.device
    config.log_dir = Path(args.log_dir)
    config.checkpoint_dir = Path(args.checkpoint_dir)
    config.validate()
    return config


def _load_processor(path: Path):
    if not path.is_file():
        return None
    from ..preprocessing.processor import TextPreprocessor  # noqa: PLC0415

    return TextPreprocessor.load(path)


def _ensure_tokens(records, processor):
    missing = [record for record in records if not (record.tokens and str(record.tokens).strip())]
    if not missing:
        return records
    if processor is None:
        raise ValueError(
            "训练集缺少 tokens 列，且没有可用的 data/processed/preprocessor.json；"
            "请先运行 python -m src.preprocessing --input <标注CSV> 生成完整预处理产物。"
        )
    for record in missing:
        _cleaned, tokens = processor.transform(record.text)
        record.tokens = " ".join(tokens)
    return records


def _build_model(args: argparse.Namespace, vocab):
    pretrained = None
    if args.embeddings:
        from ..embeddings.matrix import load_embedding_matrix  # noqa: PLC0415

        pretrained = load_embedding_matrix(args.embeddings)
    if args.model == "textcnn":
        from ..models.cnn import TextCNN  # noqa: PLC0415

        model = TextCNN(
            vocab_size=len(vocab),
            embed_dim=args.embed_dim,
            num_filters=args.num_filters,
            filter_sizes=tuple(args.filter_sizes),
            dropout=args.dropout,
            pretrained_embeddings=pretrained,
        )
    else:
        from ..models.bilstm_attention import BiLSTMAttention  # noqa: PLC0415

        model = BiLSTMAttention(
            vocab_size=len(vocab),
            embed_dim=args.embed_dim,
            hidden_dim=args.hidden_dim,
            num_layers=args.num_layers,
            dropout=args.dropout,
            attention_dim=args.attention_dim,
            pretrained_embeddings=pretrained,
        )
    return model


def _counts(labels) -> dict[str, int]:
    counts = {"negative": 0, "positive": 0}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return counts


def _update_comparison(model_name: str, metrics: dict, stats_dir: Path) -> Path | None:
    """把本次指标并入 model_comparison.csv（保留其它模型已产出的行）。"""
    from ..evaluation.report import compare_models, save_metrics_table  # noqa: PLC0415

    path = stats_dir / "model_comparison.csv"
    merged: dict[str, dict] = {}
    if path.is_file():
        import csv  # noqa: PLC0415

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    name = row.get("model")
                    if not name:
                        continue
                    merged[name] = {
                        key: float(row[key])
                        for key in ("accuracy", "precision", "recall", "f1")
                        if row.get(key) not in (None, "")
                    }
        except (OSError, ValueError):
            merged = {}
    merged[model_name] = {key: float(value) for key, value in metrics.items() if key in {"accuracy", "precision", "recall", "f1"}}
    if not merged:
        return None
    return save_metrics_table(compare_models(merged), path)


def _evaluate_test(model, args, vocab, config, processed: dict) -> dict | None:
    test_path = Path(args.test)
    if not test_path.is_file():
        print(f"[eval] 跳过测试集评估：{test_path} 不存在")
        return None
    from ..evaluation.metrics import error_analysis, compute_metrics, confusion_matrix  # noqa: PLC0415
    from ..evaluation.report import save_error_cases, save_metrics_json  # noqa: PLC0415
    from ..preprocessing.dataset import load_records  # noqa: PLC0415
    from .trainer import _require_torch, build_dataloader, predict_loader  # noqa: PLC0415

    test_records = _ensure_tokens(load_records(test_path), processed.get("processor"))
    loader = build_dataloader(test_records, config, shuffle=False, vocab=vocab)
    torch = _require_torch()
    result = predict_loader(model, loader, criterion=torch.nn.CrossEntropyLoss())
    metrics = compute_metrics(result["y_true"], result["y_pred"])
    matrix = confusion_matrix(result["y_true"], result["y_pred"])
    stats_dir = Path(args.stats_dir)
    stats_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = save_metrics_json(
        metrics | {"loss": round(result["loss"], 6), "test_size": len(test_records), "source": str(test_path)},
        config.model_name,
        stats_dir,
    )
    cases = error_analysis(
        [record.text for record in test_records],
        result["y_true"],
        result["y_pred"],
        result["probabilities"],
    )
    cases_path = save_error_cases(cases, config.model_name, Path(args.predictions_dir))
    comparison_path = _update_comparison(config.model_name, metrics, stats_dir)
    return {
        "metrics": metrics,
        "matrix": matrix,
        "metrics_path": str(metrics_path),
        "cases_path": str(cases_path),
        "comparison_path": str(comparison_path) if comparison_path else None,
        "test_size": len(test_records),
    }


def _render_figures(history, model_name: str, matrix, train_records, figures_dir: Path) -> list[str]:
    from ..visualization import (  # noqa: PLC0415 - 可选绘图依赖在函数内导入
        plot_accuracy_curve,
        plot_confusion_matrix,
        plot_loss_curve,
        plot_wordclouds_by_label,
    )

    figures_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, plotter in (("loss", plot_loss_curve), ("accuracy", plot_accuracy_curve)):
        per_model = figures_dir / f"{name}_curve_{model_name}.png"
        plotter(history, per_model)
        written.append(str(per_model))
        fixed = figures_dir / f"{name}_curve.png"
        plotter(history, fixed)
        written.append(str(fixed))
    if matrix is not None:
        matrix_path = figures_dir / f"confusion_matrix_{model_name}.png"
        plot_confusion_matrix(matrix["matrix"], matrix_path)
        written.append(str(matrix_path))
    try:
        clouds = plot_wordclouds_by_label(train_records, figures_dir)
        written.extend(str(path) for path in clouds.values())
    except ValueError as exc:
        print(f"[figures] 跳过词云：{exc}")
    return written


def _run(args: argparse.Namespace) -> int:
    from ..preprocessing.dataset import load_records  # noqa: PLC0415
    from ..preprocessing.vocab import Vocab  # noqa: PLC0415
    from .trainer import build_dataloader, train_model  # noqa: PLC0415

    config = _config_from_args(args)
    vocab_path = Path(args.vocab)
    if not vocab_path.is_file():
        raise FileNotFoundError(
            f"词表不存在：{vocab_path}。请先运行 "
            "python -m src.preprocessing --input <标注CSV> 生成 data/splits 与词表。"
        )
    vocab = Vocab.load(vocab_path)
    processor = _load_processor(Path(args.preprocessor))

    train_records = _ensure_tokens(load_records(Path(args.train)), processor)
    val_records = _ensure_tokens(load_records(Path(args.val)), processor)
    print(
        f"[data] train={len(train_records)}（{_counts(r.label for r in train_records)}） "
        f"val={len(val_records)}（{_counts(r.label for r in val_records)}） vocab={len(vocab)}"
    )

    model = _build_model(args, vocab)
    train_loader = build_dataloader(train_records, config, shuffle=True, vocab=vocab)
    val_loader = build_dataloader(val_records, config, shuffle=False, vocab=vocab)
    result = train_model(model, train_loader, val_loader, config, vocab=vocab)
    print(f"[train] 完成：best_epoch={result['best_epoch']} best_val_acc={result['best_score']:.4f}")

    evaluation = _evaluate_test(model, args, vocab, config, {"processor": processor})
    figures: list[str] = []
    if not args.no_figures:
        try:
            figures = _render_figures(
                result["history"],
                config.model_name,
                evaluation["matrix"] if evaluation else None,
                train_records,
                Path(args.figures_dir),
            )
        except ImportError as exc:
            print(f"[figures] 跳过绘图：{exc}")

    summary = {
        "model": config.model_name,
        "best_epoch": result["best_epoch"],
        "best_val_acc": round(float(result["best_score"]), 6),
        "checkpoint": result["checkpoint_path"],
        "log": result["log_path"],
        "test_metrics": evaluation["metrics"] if evaluation else None,
        "confusion_matrix": evaluation["matrix"] if evaluation else None,
        "metrics_json": evaluation["metrics_path"] if evaluation else None,
        "comparison_csv": evaluation["comparison_path"] if evaluation else None,
        "error_cases": evaluation["cases_path"] if evaluation else None,
        "figures": figures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """解析参数并启动训练。

    Returns:
        进程退出码，0 表示成功。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(f"训练失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - 手动执行入口
    raise SystemExit(main())
