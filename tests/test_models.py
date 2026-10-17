"""两个深度模型的实现测试：前向形状、掩码、预训练词向量与检查点读写。"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")


def _ids(batch: int = 3, length: int = 12, vocab_size: int = 20):
    ids = torch.randint(2, vocab_size, (batch, length))
    ids[:, -4:] = 0  # 末尾补 <pad>
    return ids


def test_textcnn_forward_shape_and_build_is_idempotent():
    from src.models.cnn import TextCNN

    model = TextCNN(vocab_size=20, embed_dim=8, num_filters=4, filter_sizes=(2, 3))
    logits = model(_ids())
    assert tuple(logits.shape) == (3, 2)
    assert model.net is not None
    assert model.build() is model.net


def test_textcnn_initializes_pretrained_embeddings_and_can_freeze():
    numpy = pytest.importorskip("numpy")
    from src.models.cnn import TextCNN

    matrix = numpy.zeros((20, 8), dtype=numpy.float32)
    matrix[2] = 1.5
    model = TextCNN(
        vocab_size=20,
        embed_dim=8,
        num_filters=2,
        filter_sizes=(2,),
        pretrained_embeddings=matrix,
        freeze_embeddings=True,
    )
    model.build()
    assert not model.net.embedding.weight.requires_grad
    assert torch.allclose(
        model.net.embedding.weight[2], torch.full((8,), 1.5)
    )


def test_textcnn_rejects_mismatched_pretrained_shape():
    numpy = pytest.importorskip("numpy")
    from src.models.cnn import TextCNN

    model = TextCNN(
        vocab_size=20,
        embed_dim=8,
        pretrained_embeddings=numpy.zeros((10, 8), dtype=numpy.float32),
    )
    with pytest.raises(ValueError):
        model.build()


def test_bilstm_forward_returns_normalized_masked_attention():
    from src.models.bilstm_attention import BiLSTMAttention

    model = BiLSTMAttention(vocab_size=20, embed_dim=8, hidden_dim=4, attention_dim=3)
    model.eval()  # 关闭 dropout，保证隐式/显式掩码两次前向可比
    ids = _ids()
    logits, attention = model(ids)
    assert tuple(logits.shape) == (3, 2)
    assert tuple(attention.shape) == (3, 12)
    assert torch.allclose(attention.sum(dim=1), torch.ones(3), atol=1e-5)
    # 末尾 4 个位置是 <pad>，注意力必须为 0
    assert torch.all(attention[:, -4:] == 0)

    explicit_mask = (ids != 0).long()
    logits_masked, attention_masked = model(ids, attention_mask=explicit_mask)
    assert torch.allclose(logits, logits_masked, atol=1e-5)
    assert torch.allclose(attention, attention_masked, atol=1e-5)


def test_bilstm_unidirectional_output_dim():
    from src.models.bilstm_attention import BiLSTMAttention

    model = BiLSTMAttention(
        vocab_size=20, embed_dim=8, hidden_dim=4, bidirectional=False
    )
    assert model.output_dim == 4
    logits, _attention = model(_ids())
    assert tuple(logits.shape) == (3, 2)


def test_checkpoint_roundtrip(tmp_path):
    from src.models.base import load_checkpoint, save_checkpoint
    from src.models.cnn import TextCNN

    model = TextCNN(vocab_size=20, embed_dim=8, num_filters=2, filter_sizes=(2,))
    model.eval()
    ids = _ids()
    with torch.no_grad():
        expected = model(ids)

    path = save_checkpoint(
        {
            "model_state": model.state_dict(),
            "config": {"model_name": "textcnn"},
            "vocab": {"min_freq": 3},
            "model_name": "textcnn",
            "model_type": "textcnn",
            "epoch": 3,
            "metrics": {"val_acc": 0.75},
            "hyperparams": model.hyperparams,
        },
        tmp_path / "best.pt",
    )
    assert path.is_file()
    assert (tmp_path / "config.json").is_file()
    assert (tmp_path / "metadata.json").is_file()

    loaded = load_checkpoint(path)
    assert loaded["epoch"] == 3
    assert loaded["model_name"] == "textcnn"

    restored = TextCNN(vocab_size=20, embed_dim=8, num_filters=2, filter_sizes=(2,))
    restored.load_state_dict(loaded["model_state"])
    restored.eval()
    with torch.no_grad():
        assert torch.allclose(restored(ids), expected, atol=1e-6)


def test_load_checkpoint_missing_file_raises(tmp_path):
    from src.models.base import load_checkpoint

    with pytest.raises(FileNotFoundError, match="检查点不存在"):
        load_checkpoint(tmp_path / "missing.pt")
