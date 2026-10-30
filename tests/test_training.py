"""训练闭环测试：早停、DataLoader、训练循环与 CLI 端到端（torch 缺失时自动跳过）。"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("torch")

from src.preprocessing.dataset import Record, save_records  # noqa: E402
from src.preprocessing.vocab import Vocab  # noqa: E402
from src.training.config import TrainConfig  # noqa: E402
from src.training.trainer import (  # noqa: E402
    EarlyStopping,
    build_dataloader,
    evaluate,
    set_seed,
    train_model,
    train_one_epoch,
)


def _vocab():
    word2id = {
        "<pad>": 0,
        "<unk>": 1,
        "好": 2,
        "坏": 3,
        "一般": 4,
        "不错": 5,
        "差": 6,
    }
    return Vocab(word2id, {index: word for word, index in word2id.items()}, 3)


def _records() -> list[Record]:
    texts = ["好 不错 一般", "差 坏 一般", "好 好 不错", "差 差 一般"]
    labels = ["positive", "negative", "positive", "negative"]
    return [
        Record(text, label, text, {"comment_id": str(index)})
        for index, (text, label) in enumerate(zip(texts, labels))
    ]


def _config(tmp_path, **overrides) -> TrainConfig:
    config = TrainConfig(
        model_name="textcnn",
        max_len=8,
        batch_size=2,
        epochs=2,
        patience=2,
        log_every=0,
        log_dir=tmp_path / "logs",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    config.validate()
    return config


def test_set_seed_is_reproducible_for_numpy_and_torch():
    import numpy
    import torch

    set_seed(7)
    first = (numpy.random.rand(3).tolist(), torch.rand(3).tolist())
    set_seed(7)
    second = (numpy.random.rand(3).tolist(), torch.rand(3).tolist())
    assert first == second


def test_early_stopping_tracks_best_and_stops():
    stopper = EarlyStopping(patience=2, min_delta=0.0)
    assert stopper.step(0.5, 1, {"w": 1}) is True
    assert stopper.best_epoch == 1
    assert stopper.step(0.5, 2, {"w": 2}) is False
    assert stopper.should_stop is False
    assert stopper.step(0.4, 3, {"w": 3}) is False
    assert stopper.should_stop is True
    assert stopper.best_state == {"w": 1}  # 深拷贝最优权重，不被后续覆盖


def test_build_dataloader_shapes_and_mask(tmp_path):
    config = _config(tmp_path)
    loader = build_dataloader(_records(), config, shuffle=False, vocab=_vocab())
    input_ids, attention_mask, lengths, labels = next(iter(loader))
    assert tuple(input_ids.shape) == (2, 8)
    assert tuple(attention_mask.shape) == (2, 8)
    assert tuple(labels.shape) == (2,)
    assert int(lengths.max()) <= 8
    assert int(attention_mask.sum(dim=1).max()) == 3


def test_build_dataloader_validation(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(ValueError):
        build_dataloader(_records(), config, shuffle=False)
    with pytest.raises(ValueError):
        build_dataloader([], config, shuffle=False, vocab=_vocab())
    untokenized = [Record("好", "positive", None, {"comment_id": "1"})]
    with pytest.raises(ValueError, match="分词结果"):
        build_dataloader(untokenized, config, shuffle=False, vocab=_vocab())


def test_train_model_writes_checkpoint_and_log(tmp_path):
    from src.models.cnn import TextCNN

    config = _config(tmp_path)
    vocab = _vocab()
    train_records = _records() + _records()
    val_records = _records()
    train_loader = build_dataloader(train_records, config, shuffle=True, vocab=vocab)
    val_loader = build_dataloader(val_records, config, shuffle=False, vocab=vocab)
    model = TextCNN(vocab_size=len(vocab), embed_dim=8, num_filters=2, filter_sizes=(2,))

    result = train_model(model, train_loader, val_loader, config, vocab=vocab)
    assert 1 <= len(result["history"]) <= config.epochs
    assert result["best_epoch"] >= 1
    assert result["checkpoint_path"] is not None
    checkpoint = tmp_path / "checkpoints" / "textcnn" / "best.pt"
    assert checkpoint.is_file()
    log_path = tmp_path / "logs" / result["log_path"].split("logs")[-1].lstrip("\\/")
    assert log_path.is_file()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["model"] == "textcnn"
    assert len(payload["history"]) == len(result["history"])
    for row in payload["history"]:
        assert {"epoch", "train_loss", "train_acc", "val_loss", "val_acc", "lr"} <= set(row)
        assert 0 <= row["train_acc"] <= 1

    metrics = evaluate(model, val_loader, __import__("torch").nn.CrossEntropyLoss(), config)
    assert 0.0 <= metrics["accuracy"] <= 1.0


def test_training_accuracy_counts_predictions_and_preserves_scalar_loss_api(tmp_path):
    import torch

    class FixedClassifier(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.logits = torch.nn.Parameter(torch.tensor([[3., -3.], [3., -3.], [3., -3.], [-3., 3.]]))

        def forward(self, ids):
            return self.logits[ids[:, 0]]

    ids = torch.arange(4).reshape(4, 1)
    labels = torch.tensor([0, 0, 1, 1])
    loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(
        ids, torch.ones_like(ids), torch.ones(4, dtype=torch.long), labels), batch_size=2)
    model = FixedClassifier()
    optimizer = torch.optim.SGD(model.parameters(), lr=0)
    criterion = torch.nn.CrossEntropyLoss()
    expected_loss = criterion(model(ids), labels).item()
    config = _config(tmp_path, grad_clip=0)
    result = train_one_epoch(model, loader, optimizer, criterion, config, return_metrics=True)
    assert result["accuracy"] == .75
    assert result["loss"] == pytest.approx(expected_loss)
    assert train_one_epoch(model, loader, optimizer, criterion, config) == pytest.approx(expected_loss)


def test_cli_end_to_end(tmp_path):
    from src.training.cli import main

    vocab = _vocab()
    vocab_path = vocab.save(tmp_path / "vocab.json")
    train_path = save_records(_records() + _records(), tmp_path / "train.csv")
    val_path = save_records(_records(), tmp_path / "val.csv")
    test_path = save_records(_records(), tmp_path / "test.csv")

    exit_code = main(
        [
            "--model",
            "textcnn",
            "--train",
            str(train_path),
            "--val",
            str(val_path),
            "--test",
            str(test_path),
            "--vocab",
            str(vocab_path),
            "--preprocessor",
            str(tmp_path / "missing_preprocessor.json"),
            "--epochs",
            "1",
            "--batch-size",
            "2",
            "--max-len",
            "8",
            "--embed-dim",
            "8",
            "--num-filters",
            "2",
            "--filter-sizes",
            "2",
            "--checkpoint-dir",
            str(tmp_path / "checkpoints"),
            "--log-dir",
            str(tmp_path / "logs"),
            "--stats-dir",
            str(tmp_path / "stats"),
            "--predictions-dir",
            str(tmp_path / "predictions"),
            "--no-figures",
        ]
    )
    assert exit_code == 0
    metrics = json.loads((tmp_path / "stats" / "metrics_textcnn.json").read_text(encoding="utf-8"))
    assert metrics["model"] == "textcnn"
    assert {"accuracy", "precision", "recall", "f1"} <= set(metrics["metrics"])
    assert (tmp_path / "stats" / "model_comparison.csv").is_file()
    assert (tmp_path / "predictions" / "error_cases_textcnn.csv").is_file()
    assert (tmp_path / "checkpoints" / "textcnn" / "best.pt").is_file()
