"""真实检查点推理（``src.inference.service``）测试。

设计要点：

1. **只断言结构与确定性，不断言准确率**：仓库当前入库的是 2026-10-30 复跑的真实权重
   （vocab 9,753、`max_len=200`，测试 accuracy 0.8960 / 0.9008），但训练语料是
   酒店/外卖公开语料，不等价于 B 站领域效果；且固定样例的方向断言容易被权重版本影响，
   因此这里只验证「加载器 / 前向 / 接口契约 / 来源标注」正确，绝不写死准确率数字。
2. **占位模式仍确定**：``tests/conftest.py`` 的 autouse fixture 把 ``ACTIVE_MODEL``
   指向不存在的模型名；需要真实模型的用例在用例内 ``monkeypatch.setenv`` 后
   ``load_model_cached.cache_clear()``。
3. **两套检查点目录都要能加载**：仓库真实权重只带 ``best.pt`` + 资源包（词表既内嵌在
   state 里，也在 ``preprocessing/`` 里）；为覆盖「只有资源包」的目录风格，这里用
   自造的迷你检查点验证后者（含 manifest 哈希校验 + 校验失败回退仓库默认预处理资源）。
4. 缺失检查点 → 回退 ``rule_placeholder``；损坏 / 格式不支持 / 词表或超参不匹配
   → ``RuntimeError``（不静默降级）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from src.artifacts import service as artifacts_service
from src.artifacts.config import ArtifactsConfig
from src.artifacts.service import ArtifactService
from src.inference import service
from src.inference.app import app

torch = pytest.importorskip("torch", reason="真实模型推理测试需要 PyTorch")

#: 仓库入库的两个检查点名（与 SUPPORTED_MODELS 一致）
CHECKPOINT_NAMES = ("textcnn", "bilstm_attention")

#: 仓库检查点是否存在（缺失时跳过「用真实权重」的用例，而不是失败）
HAS_CHECKPOINTS = all(
    (service.CHECKPOINT_DIR / name / "best.pt").is_file() for name in CHECKPOINT_NAMES
)

needs_checkpoints = pytest.mark.skipif(
    not HAS_CHECKPOINTS,
    reason="models/checkpoints 下没有检查点，跳过真实权重相关用例",
)

POSITIVE_TEXT = "这个商品质量很好，物流也快，非常满意。"
NEGATIVE_TEXT = "质量太差了，用了一天就坏了，非常失望。"
BATCH_TEXTS = [
    POSITIVE_TEXT,
    NEGATIVE_TEXT,
    "这是一条没有明显情感词的陈述。",
    "教程讲得很清楚，看完就学会了。",
]


# --------------------------------------------------------------------------- #
# 断言工具
# --------------------------------------------------------------------------- #
def assert_valid_prediction(result: dict, *, text: str | None = None) -> None:
    """校验单条预测结果与 ``POST /api/predict`` 的契约同构（不断言方向/准确率）。"""
    assert set(result) == {"text", "label", "confidence", "probs"}
    if text is not None:
        assert result["text"] == text
    probs = result["probs"]
    assert set(probs) == {"positive", "negative"}
    assert all(0.0 <= float(value) <= 1.0 for value in probs.values())
    assert probs["positive"] + probs["negative"] == pytest.approx(1.0, abs=1e-6)
    assert result["label"] in {"positive", "negative"}
    assert result["confidence"] == pytest.approx(max(probs.values()), abs=1e-9)
    # label 必须是概率最大类（平局约定 positive，与占位预测器一致）
    expected = "positive" if probs["positive"] >= probs["negative"] else "negative"
    assert result["label"] == expected


def _clear_model_cache() -> None:
    service.load_model_cached.cache_clear()


# --------------------------------------------------------------------------- #
# 加载器：真实检查点 → Predictor
# --------------------------------------------------------------------------- #
@needs_checkpoints
@pytest.mark.parametrize("model_name", CHECKPOINT_NAMES)
def test_load_model_returns_predictor(model_name):
    predictor = service.load_model(model_name)

    assert isinstance(predictor, service.Predictor)
    # 属性契约（T2 / 其它调用方依赖）
    assert predictor.model_name == model_name
    assert predictor.model_type in service.SUPPORTED_MODEL_TYPES
    assert predictor.labels == service.LABELS
    assert predictor.max_len >= 1
    assert predictor.vocab is not None and len(predictor.vocab) >= 2
    assert predictor.processor is not None
    assert predictor.device in {"cpu", "cuda", "mps"} or predictor.device.startswith("cuda")
    # 词表必须含 PAD / UNK（编码依赖）
    assert predictor.vocab.to_id("<pad>") == 0
    assert predictor.vocab.to_id("<unk>") == 1

    first = predictor.predict(POSITIVE_TEXT)
    assert_valid_prediction(first, text=POSITIVE_TEXT)
    # 确定性：同一文本两次完全一致（eval 模式 + no_grad）
    assert predictor.predict(POSITIVE_TEXT) == first

    # resolve_method：真实模型（不传显式名字）→ Predictor.model_name
    assert service.resolve_method(predictor) == model_name
    assert service.resolve_method(predictor, model_name) == model_name


@needs_checkpoints
def test_predict_text_delegates_to_predictor():
    predictor = service.load_model("textcnn")
    result = service.predict_text(POSITIVE_TEXT, model=predictor, model_name="textcnn")
    assert result == predictor.predict(POSITIVE_TEXT)
    assert_valid_prediction(result, text=POSITIVE_TEXT)


@needs_checkpoints
def test_predict_batch_matches_single_predict():
    predictor = service.load_model("bilstm_attention")
    batch = predictor.predict_batch(BATCH_TEXTS)

    assert len(batch) == len(BATCH_TEXTS)
    assert [item["index"] for item in batch] == list(range(len(BATCH_TEXTS)))
    assert [item["text"] for item in batch] == BATCH_TEXTS
    for item in batch:
        # 批量接口每项正好 4 个字段（与 /api/predict/batch 的 results 契约一致）
        assert set(item) == {"index", "text", "label", "confidence"}
        assert item["label"] in {"positive", "negative"}
        assert 0.0 <= item["confidence"] <= 1.0

    singles = [predictor.predict(text) for text in BATCH_TEXTS]
    for item, single in zip(batch, singles):
        assert item["label"] == single["label"]
        assert item["confidence"] == pytest.approx(single["confidence"], abs=1e-5)

    # service.predict_batch 对有 predict_batch 的模型走批量分支，结果一致
    delegated = service.predict_batch(BATCH_TEXTS, model=predictor, model_name="bilstm_attention")
    assert delegated == batch


@needs_checkpoints
def test_predict_batch_small_chunks_are_consistent():
    """批大小小于文本数时，分批前向结果仍与单条一致（顺序不能乱）。"""
    predictor = service.load_model("textcnn")
    predictor.batch_size = 2  # 人为拆成 2 批
    batch = predictor.predict_batch(BATCH_TEXTS)
    singles = [predictor.predict(text) for text in BATCH_TEXTS]
    assert [item["label"] for item in batch] == [item["label"] for item in singles]
    for item, single in zip(batch, singles):
        assert item["confidence"] == pytest.approx(single["confidence"], abs=1e-5)


@needs_checkpoints
@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_tokens_fallback_is_deterministic(text):
    """清洗分词后为空：确定性 0.5 / 0.5 + positive/0.5，绝不抛异常。"""
    predictor = service.load_model("textcnn")
    result = predictor.predict(text)
    assert result["text"] == text
    assert result["label"] == "positive"
    assert result["confidence"] == 0.5
    assert result["probs"] == {"positive": 0.5, "negative": 0.5}
    assert predictor.predict(text) == result


@needs_checkpoints
def test_predict_batch_keeps_order_with_empty_tokens():
    predictor = service.load_model("textcnn")
    texts = ["", POSITIVE_TEXT, "   "]
    batch = predictor.predict_batch(texts)
    assert [item["index"] for item in batch] == [0, 1, 2]
    assert [item["text"] for item in batch] == texts
    assert batch[0]["confidence"] == 0.5
    assert batch[2]["confidence"] == 0.5


@needs_checkpoints
def test_load_model_cached_returns_same_instance():
    _clear_model_cache()
    try:
        first = service.load_model_cached("textcnn")
        second = service.load_model_cached("textcnn")
        assert first is not None and first is second
    finally:
        _clear_model_cache()


@needs_checkpoints
def test_get_predictor_uses_active_model(monkeypatch):
    monkeypatch.setenv(service.ENV_ACTIVE_MODEL, "textcnn")
    _clear_model_cache()
    try:
        predictor = service.get_predictor()
        assert isinstance(predictor, service.Predictor)
        assert predictor.model_name == "textcnn"
        assert service.resolve_method(predictor) == "textcnn"
    finally:
        _clear_model_cache()


def test_inference_device_defaults_to_cpu(monkeypatch):
    monkeypatch.delenv(service.ENV_INFERENCE_DEVICE, raising=False)
    assert service._resolve_inference_device(torch) == "cpu"


def test_inference_device_auto_follows_cuda_availability(monkeypatch):
    monkeypatch.setenv(service.ENV_INFERENCE_DEVICE, "auto")
    expected = "cuda" if torch.cuda.is_available() else "cpu"
    assert service._resolve_inference_device(torch) == expected


def test_inference_device_explicit_cuda_falls_back_without_gpu(monkeypatch):
    if torch.cuda.is_available():
        pytest.skip("当前环境有 CUDA，显式 cuda 不会回退")
    monkeypatch.setenv(service.ENV_INFERENCE_DEVICE, "cuda")
    assert service._resolve_inference_device(torch) == "cpu"


def test_inference_device_unknown_value_falls_back_to_cpu(monkeypatch):
    monkeypatch.setenv(service.ENV_INFERENCE_DEVICE, "tpu")
    assert service._resolve_inference_device(torch) == "cpu"


# --------------------------------------------------------------------------- #
# HTTP 层：/api/predict 与 /api/predict/batch 走真实模型
# --------------------------------------------------------------------------- #
@needs_checkpoints
@pytest.mark.parametrize("model_name", CHECKPOINT_NAMES)
def test_api_predict_uses_real_model(monkeypatch, model_name):
    monkeypatch.setenv(service.ENV_ACTIVE_MODEL, model_name)
    _clear_model_cache()
    try:
        client = TestClient(app)
        response = client.post("/api/predict", json={"text": POSITIVE_TEXT})
        assert response.status_code == 200
        body = response.json()

        # 既有字段集合不变（纯增量：method）
        assert set(body) == {"text", "label", "confidence", "probs", "method"}
        assert body["method"] == model_name
        assert body["text"] == POSITIVE_TEXT
        assert body["label"] in {"positive", "negative"}
        assert set(body["probs"]) == {"positive", "negative"}
        assert body["probs"]["positive"] + body["probs"]["negative"] == pytest.approx(1.0, abs=1e-6)
        assert body["confidence"] == pytest.approx(max(body["probs"].values()), abs=1e-9)

        # 同一文本两次结果完全一致（确定性）
        again = client.post("/api/predict", json={"text": POSITIVE_TEXT}).json()
        assert again == body
    finally:
        _clear_model_cache()


@needs_checkpoints
def test_api_predict_batch_uses_real_predictor(monkeypatch):
    monkeypatch.setenv(service.ENV_ACTIVE_MODEL, "textcnn")
    _clear_model_cache()
    try:
        client = TestClient(app)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "text"])
        for index, text in enumerate(BATCH_TEXTS):
            writer.writerow([index, text])
        payload = buffer.getvalue().encode("utf-8-sig")

        response = client.post(
            "/api/predict/batch",
            files={"file": ("comments.csv", payload, "text/csv")},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"total", "results", "export_url"}
        assert body["total"] == len(BATCH_TEXTS)
        assert body["export_url"] is not None
        assert [item["index"] for item in body["results"]] == list(range(len(BATCH_TEXTS)))
        assert [item["text"] for item in body["results"]] == BATCH_TEXTS
        for item in body["results"]:
            assert set(item) == {"index", "text", "label", "confidence"}
            assert item["label"] in {"positive", "negative"}
            assert 0.0 <= item["confidence"] <= 1.0
    finally:
        _clear_model_cache()


# --------------------------------------------------------------------------- #
# 缺失 / 损坏检查点的边界
# --------------------------------------------------------------------------- #
def test_missing_checkpoint_falls_back_to_rule_placeholder(monkeypatch):
    assert service.load_model("__no_such_model__") is None
    assert service.resolve_method(None) == service.METHOD_RULE_PLACEHOLDER == "rule_placeholder"

    monkeypatch.setenv(service.ENV_ACTIVE_MODEL, "__no_such_model__")
    _clear_model_cache()
    try:
        assert service.get_predictor() is None
        # predict_text(model=None) 必须与规则占位预测器完全一致（既有用例依赖）
        assert service.predict_text(POSITIVE_TEXT, model=None) == service.predict_rule_based(
            POSITIVE_TEXT
        )

        client = TestClient(app)
        body = client.post("/api/predict", json={"text": POSITIVE_TEXT}).json()
        assert body["method"] == service.METHOD_RULE_PLACEHOLDER
        assert set(body) == {"text", "label", "confidence", "probs", "method"}
    finally:
        _clear_model_cache()


@needs_checkpoints
def test_torch_missing_returns_none(monkeypatch):
    """torch 缺失只能降级（返回 None），不能抛 ImportError 让服务挂掉。"""

    def _raise_import_error() -> torch.Tensor:  # pragma: no cover - 只作为替身
        raise ImportError("simulated: PyTorch 未安装")

    monkeypatch.setattr(service, "require_torch", _raise_import_error)
    assert service.load_model("textcnn") is None


def test_corrupt_checkpoint_raises_runtime_error(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "broken"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "best.pt").write_bytes(b"this is definitely not a torch checkpoint")
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="检查点"):
        service.load_model("broken")


def test_unsupported_model_type_raises_runtime_error(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "unsupported"
    checkpoint_dir.mkdir()
    torch.save(
        {
            "model_state": {"weight": torch.zeros(1)},
            "model_type": "bert",
            "config": {"max_len": 8},
            "vocab": {"word2id": {"<pad>": 0, "<unk>": 1}, "min_freq": 3},
        },
        checkpoint_dir / "best.pt",
    )
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="不支持的模型类型"):
        service.load_model("unsupported")


def test_vocab_size_mismatch_raises_runtime_error(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "mismatch"
    checkpoint_dir.mkdir()
    torch.save(
        {
            "model_state": {"embedding.weight": torch.zeros(6, 8)},
            "model_type": "textcnn",
            "config": {"max_len": 8},
            "hyperparams": {"vocab_size": 6},
            "vocab": {
                "word2id": {"<pad>": 0, "<unk>": 1, "好": 2, "差": 3},
                "min_freq": 3,
            },
        },
        checkpoint_dir / "best.pt",
    )
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="词表规模"):
        service.load_model("mismatch")


def test_missing_vocab_raises_runtime_error(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "no-vocab"
    checkpoint_dir.mkdir()
    torch.save(
        {
            "model_state": {"embedding.weight": torch.zeros(2, 8)},
            "model_type": "textcnn",
            "config": {"max_len": 8},
            "hyperparams": {"vocab_size": 2},
        },
        checkpoint_dir / "best.pt",
    )
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path)
    monkeypatch.setattr(service, "PROCESSED_DIR", tmp_path / "empty-processed")

    with pytest.raises(RuntimeError, match="缺少配套词表"):
        service.load_model("no-vocab")


# --------------------------------------------------------------------------- #
# 真实训练目录风格：带 preprocessing/ 资源包（冒烟检查点没有）
# --------------------------------------------------------------------------- #
def _make_tiny_checkpoint(root, *, with_preprocessing: bool):
    """构造一个「真实训练目录」风格的迷你检查点，返回检查点目录。

    ``with_preprocessing=True`` 时额外写 ``preprocessing/`` 资源包
    （vocab / preprocessor / label_map / manifest 哈希齐全），且 **state 里不放
    vocab 快照**，强制走资源包；否则退回冒烟检查点风格（词表内嵌 state）。
    模型是随机初始化的小 TextCNN，只验证加载链路，不验证效果。
    """
    from src.models.cnn import TextCNN
    from src.preprocessing.processor import TextPreprocessor
    from src.preprocessing.vocab import Vocab

    vocab = Vocab(
        word2id={"<pad>": 0, "<unk>": 1, "好": 2, "差": 3, "服务": 4, "快递": 5},
        id2word={0: "<pad>", 1: "<unk>", 2: "好", 3: "差", 4: "服务", 5: "快递"},
        min_freq=3,
    )
    model = TextCNN(
        vocab_size=len(vocab), embed_dim=8, num_filters=2, filter_sizes=(2,), dropout=0.0
    )
    model.build()

    checkpoint_dir = root / "textcnn"
    checkpoint_dir.mkdir(parents=True)
    state = {
        "model_state": model.state_dict(),
        "config": {"max_len": 12},
        "hyperparams": dict(model.hyperparams),
        "model_type": "textcnn",
        "model_name": "textcnn",
        "epoch": 1,
        "metrics": {"val_acc": 0.5},
    }
    if with_preprocessing:
        bundle = checkpoint_dir / "preprocessing"
        bundle.mkdir()
        vocab.save(bundle / "vocab.json")
        TextPreprocessor.from_resources(max_len=200).save(bundle / "preprocessor.json")
        (bundle / "label_map.json").write_text(
            json.dumps({"negative": 0, "positive": 1}), encoding="utf-8"
        )
        manifest = {
            "files": {
                f"data/processed/{name}": hashlib.sha256(
                    (bundle / name).read_bytes()
                ).hexdigest()
                for name in ("vocab.json", "preprocessor.json", "label_map.json")
            }
        }
        (bundle / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    else:
        state["vocab"] = vocab.to_dict()
    torch.save(state, checkpoint_dir / "best.pt")
    return checkpoint_dir


def test_checkpoint_with_preprocessing_bundle_is_supported(tmp_path, monkeypatch):
    """真实训练目录带 preprocessing/：用资源包里的预处理与词表加载。"""
    _make_tiny_checkpoint(tmp_path / "checkpoints", with_preprocessing=True)
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path / "checkpoints")

    predictor = service.load_model("textcnn")
    assert isinstance(predictor, service.Predictor)
    # max_len 来自检查点 config；processor 来自 preprocessing/ 资源包（其 max_len=200）
    assert predictor.max_len == 12
    assert predictor.processor is not None and predictor.processor.max_len == 200
    assert [predictor.vocab.to_word(index) for index in range(len(predictor.vocab))] == [
        "<pad>", "<unk>", "好", "差", "服务", "快递",
    ]
    assert_valid_prediction(predictor.predict("服务很好"))
    assert_valid_prediction(predictor.predict("快递太差了"))


def test_broken_preprocessing_bundle_falls_back_to_repo_resources(tmp_path, monkeypatch):
    """preprocessing/ 资源包校验失败 → warning 回退仓库默认预处理，不阻断加载。"""
    checkpoint_dir = _make_tiny_checkpoint(tmp_path / "checkpoints", with_preprocessing=True)
    manifest_path = checkpoint_dir / "preprocessing" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["data/processed/preprocessor.json"] = "0" * 64  # 人为破坏哈希
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path / "checkpoints")

    predictor = service.load_model("textcnn")
    assert isinstance(predictor, service.Predictor)
    # 回退 TextPreprocessor.from_resources(max_len=predictor.max_len)
    assert predictor.processor is not None
    assert predictor.processor.max_len == predictor.max_len == 12
    # 词表仍能从资源包里的 vocab.json 读到（与 hyperparams.vocab_size 一致）
    assert len(predictor.vocab) == 6
    assert_valid_prediction(predictor.predict("服务很好"))


def test_checkpoint_without_bundle_uses_state_vocab(tmp_path, monkeypatch):
    """冒烟检查点风格（无 preprocessing/）：词表取 state 内嵌快照。"""
    _make_tiny_checkpoint(tmp_path / "checkpoints", with_preprocessing=False)
    monkeypatch.setattr(service, "CHECKPOINT_DIR", tmp_path / "checkpoints")

    predictor = service.load_model("textcnn")
    assert isinstance(predictor, service.Predictor)
    assert predictor.max_len == 12
    assert predictor.processor is not None and predictor.processor.max_len == 12
    assert len(predictor.vocab) == 6
    assert_valid_prediction(predictor.predict("服务很好"))


# --------------------------------------------------------------------------- #
# 替身：service.predict_batch 的分批 / 回退逻辑
# --------------------------------------------------------------------------- #
class FakeBatchPredictor:
    """带 ``predict_batch`` 的替身：验证 service 优先走批量接口。"""

    model_name = "fake_batch_model"

    def __init__(self) -> None:
        self.predict_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    def predict(self, text: str) -> dict:
        self.predict_calls.append(text)
        return {
            "text": text,
            "label": "positive",
            "confidence": 0.5,
            "probs": {"positive": 0.5, "negative": 0.5},
        }

    def predict_batch(self, texts):
        self.batch_calls.append(list(texts))
        return [
            {"index": index, "text": text, "label": "negative", "confidence": 0.75}
            for index, text in enumerate(texts)
        ]


class FakeSinglePredictor:
    """只有 ``predict`` 的替身：验证 service 逐条回退路径。"""

    model_name = "fake_single_model"

    def predict(self, text: str) -> dict:
        return {
            "text": text,
            "label": "negative",
            "confidence": 0.8,
            "probs": {"positive": 0.2, "negative": 0.8},
        }


def test_predict_batch_prefers_model_batch_method():
    fake = FakeBatchPredictor()
    results = service.predict_batch(["a", "b"], model=fake, model_name="fake_batch_model")
    assert fake.batch_calls == [["a", "b"]]
    assert fake.predict_calls == []  # 没有逐条调用 predict
    assert results == [
        {"index": 0, "text": "a", "label": "negative", "confidence": 0.75},
        {"index": 1, "text": "b", "label": "negative", "confidence": 0.75},
    ]
    assert service.resolve_method(fake) == "fake_batch_model"
    assert service.resolve_method(fake, "explicit_name") == "explicit_name"


def test_predict_batch_falls_back_to_predict_text():
    fake = FakeSinglePredictor()
    results = service.predict_batch(["a", "b"], model=fake)
    assert results == [
        {"index": 0, "text": "a", "label": "negative", "confidence": 0.8},
        {"index": 1, "text": "b", "label": "negative", "confidence": 0.8},
    ]
    assert service.predict_text("x", model=fake) == {
        "text": "x",
        "label": "negative",
        "confidence": 0.8,
        "probs": {"positive": 0.2, "negative": 0.8},
    }


def test_predict_text_rejects_objects_without_predict():
    with pytest.raises(TypeError, match="predict"):
        service.predict_text("x", model=object())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 数据项目流水线：sentiment.csv / analysis.json 的 method 是真实模型名
# --------------------------------------------------------------------------- #
@needs_checkpoints
def test_artifacts_pipeline_records_real_model_method(tmp_path, monkeypatch):
    monkeypatch.setenv(service.ENV_ACTIVE_MODEL, "textcnn")
    _clear_model_cache()

    artifacts = ArtifactService(
        root=tmp_path / "artifacts-real",
        config=ArtifactsConfig(clean_seconds=0.0, sentiment_seconds=0.0, stats_seconds=0.0),
    )
    artifacts_service.set_service(artifacts)
    try:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["comment_id", "text"])
        for index, text in enumerate(BATCH_TEXTS):
            writer.writerow([f"c{index}", text])
        project = artifacts.create_project(
            [("demo.csv", buffer.getvalue().encode("utf-8-sig"))], name=None
        )

        deadline = time.monotonic() + 15.0
        record: dict = {}
        while time.monotonic() < deadline:
            record = artifacts.store.get_project(project["id"]) or {}
            if record.get("status") in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert record.get("status") == "completed", record.get("error")

        # 项目对象 / sentiment.csv / analysis.json 的 method 都必须是真实模型名
        assert record["method"] == "textcnn"
        directory = artifacts.store.project_dir(project["id"])
        with (directory / "sentiment.csv").open(encoding="utf-8-sig", newline="") as handle:
            sentiment_rows = list(csv.DictReader(handle))
        assert len(sentiment_rows) == len(BATCH_TEXTS)
        assert {row["method"] for row in sentiment_rows} == {"textcnn"}
        assert {row["label"] for row in sentiment_rows} <= {"positive", "negative", "neutral"}

        analysis = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
        assert analysis["method"] == "textcnn"
        assert analysis["total"] == len(BATCH_TEXTS)
    finally:
        artifacts.shutdown()
        _clear_model_cache()
