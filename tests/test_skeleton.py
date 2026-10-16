"""骨架自检测试：验证模块结构、无 torch 可用性与已实现的公共工具。

覆盖内容与 `task-1` 的交付清单一一对应：

1. `src/` 下 8 个子包都有 ``__init__.py`` 与 ``README.md``；
2. 所有子包（含 `src.inference.app`）在不安装 torch 的环境下也能导入
   （`test_require_torch_reports_missing_dependency` 用猴子补丁模拟 torch 缺失）；
3. 两个模型类：``__init__`` 记录超参、``estimate_params`` 公式正确、``forward`` 明确抛 TODO；
4. 已实现的公共工具：词表存取、上传文件文本列识别、规则占位预测的确定性与取值范围。
"""

from __future__ import annotations

import builtins
import importlib

import pytest

#: task-1 要求交付的 8 个子包
PACKAGES = (
    "src.crawler",
    "src.preprocessing",
    "src.embeddings",
    "src.models",
    "src.training",
    "src.evaluation",
    "src.visualization",
    "src.inference",
)


@pytest.mark.parametrize("package", PACKAGES)
def test_package_has_init_and_readme(package):
    module = importlib.import_module(package)
    assert module.__file__ is not None
    readme = module.__file__.replace("__init__.py", "README.md")
    from pathlib import Path

    assert Path(readme).is_file(), f"{package} 缺少 README.md"
    assert Path(readme).read_text(encoding="utf-8").strip()


def test_inference_app_imports_without_torch_blocked(monkeypatch):
    """模拟「无 torch」环境导入 FastAPI 应用（验收命令之一）。"""
    import sys

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("模拟环境未安装 torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # 清掉缓存，确保是真正重新执行一次模块导入
    for name in [n for n in sys.modules if n == "src.inference.app"]:
        del sys.modules[name]
    module = importlib.import_module("src.inference.app")
    assert hasattr(module, "app")


def test_require_torch_reports_missing_dependency(monkeypatch):
    """torch 缺失时应给出带安装命令的 ImportError，而不是裸 ImportError。"""
    from src.models.base import require_torch

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("模拟环境未安装 torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="PyTorch"):
        require_torch()


# --------------------------------------------------------------------------- #
# 模型骨架
# --------------------------------------------------------------------------- #
def test_textcnn_records_hyperparams_and_estimates_params():
    from src.models.cnn import TextCNN

    model = TextCNN(vocab_size=100, embed_dim=8, num_filters=4, filter_sizes=(2, 3))
    assert model.hyperparams["filter_sizes"] == (2, 3)
    assert model.hyperparams["num_classes"] == 2

    params = model.estimate_params()
    assert params["embedding"] == 100 * 8
    assert params["conv"] == 4 * 8 * 2 + 4 * 8 * 3
    assert params["classifier"] == 2 * 4 * 2
    assert params["total"] == sum(params[k] for k in ("embedding", "conv", "classifier"))


def test_bilstm_attention_records_hyperparams_and_estimates_params():
    from src.models.bilstm_attention import BiLSTMAttention

    model = BiLSTMAttention(
        vocab_size=100, embed_dim=8, hidden_dim=4, num_layers=1, attention_dim=3
    )
    assert model.output_dim == 8  # 双向 → 2H

    params = model.estimate_params()
    assert params["embedding"] == 100 * 8
    assert params["lstm"] == 2 * 4 * (8 * 4 + 4 * 4)
    assert params["attention"] == 3 * 8 + 3
    assert params["classifier"] == 2 * 8
    assert params["total"] == sum(
        params[k] for k in ("embedding", "lstm", "attention", "classifier")
    )


def test_model_forward_is_explicit_todo():
    from src.models.bilstm_attention import BiLSTMAttention
    from src.models.cnn import TextCNN

    with pytest.raises(NotImplementedError):
        TextCNN(vocab_size=10).forward([[1, 2, 3]])
    with pytest.raises(NotImplementedError):
        BiLSTMAttention(vocab_size=10).forward([[1, 2, 3]])


def test_bilstm_attention_validates_num_layers():
    from src.models.bilstm_attention import BiLSTMAttention

    with pytest.raises(ValueError):
        BiLSTMAttention(vocab_size=10, num_layers=0)


# --------------------------------------------------------------------------- #
# 已实现的公共工具
# --------------------------------------------------------------------------- #
def test_vocab_roundtrip(tmp_path):
    from src.preprocessing.vocab import Vocab

    vocab = Vocab(
        word2id={"<pad>": 0, "<unk>": 1, "很好": 2, "差": 3},
        id2word={0: "<pad>", 1: "<unk>", 2: "很好", 3: "差"},
        min_freq=3,
    )
    path = vocab.save(tmp_path / "vocab.json")

    loaded = Vocab.load(path)
    assert loaded.word2id == vocab.word2id
    assert loaded.min_freq == 3
    assert loaded.to_id("不存在") == 1  # OOV → <unk>
    assert loaded.to_word(99) == "<unk>"
    assert len(loaded) == 4


def test_batch_detect_text_column_prefers_text():
    from src.inference.batch import detect_text_column

    assert detect_text_column(["id", "Text"]) == "Text"
    assert detect_text_column(["id", "comment"]) == "comment"
    assert detect_text_column(["id", "评论"]) == "评论"
    assert detect_text_column(["id", "内容"]) == "内容"
    assert detect_text_column(["只有一列"]) == "只有一列"


def test_batch_read_texts_skips_empty_rows():
    from src.inference.batch import read_texts

    content = "id,text\n1,很好\n2,\n3,一般\n".encode("utf-8")
    assert read_texts("a.csv", content) == ["很好", "一般"]


def test_batch_read_texts_rejects_unsupported_suffix():
    from src.inference.batch import UnsupportedFileTypeError, read_texts

    with pytest.raises(UnsupportedFileTypeError):
        read_texts("a.pdf", b"whatever")


def test_rule_based_predict_is_stable_and_normalized():
    from src.inference.service import predict_rule_based

    first = predict_rule_based("这是一条测试评论")
    second = predict_rule_based("这是一条测试评论")
    assert first == second

    assert first["label"] in {"positive", "negative"}
    probs = first["probs"]
    assert probs["positive"] + probs["negative"] == pytest.approx(1.0)
    assert 0.0 < probs["positive"] < 1.0  # 概率不为 0 / 1
    assert 0.0 <= first["confidence"] <= 1.0
    assert first["confidence"] == pytest.approx(max(probs.values()))


def test_available_models_returns_two_entries():
    from src.inference.service import available_models

    models = available_models()
    assert {item["name"] for item in models} == {"textcnn", "bilstm_attention"}
    for item in models:
        assert set(item["metrics"]) == {"accuracy", "precision", "recall", "f1"}
        assert isinstance(item["ready"], bool)


def test_train_config_validate_rejects_bad_values():
    from src.training.config import TrainConfig

    TrainConfig().validate()  # 默认值合法
    with pytest.raises(ValueError):
        TrainConfig(batch_size=0).validate()
    with pytest.raises(ValueError):
        TrainConfig(learning_rate=0.0).validate()


def test_set_seed_is_reproducible():
    import random

    from src.training.trainer import set_seed

    set_seed(42)
    first = [random.random() for _ in range(3)]
    set_seed(42)
    assert [random.random() for _ in range(3)] == first
