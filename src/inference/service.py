"""推理服务核心层：真实检查点加载与预测逻辑（与 Web 框架解耦）。

## 两条预测路径（诚实标注，不做假）

1. **真实模型**：``models/checkpoints/<name>/best.pt`` 存在且 PyTorch 可用时，
   :func:`load_model` 读取检查点、重建 ``TextCNN`` / ``BiLSTMAttention``、
   加载权重并封装成 :class:`Predictor`；:func:`predict_text` / :func:`predict_batch`
   走真实前向，:func:`resolve_method` 返回模型名。
2. **规则 / 词典占位**：检查点不存在或 torch 未安装时，:func:`load_model` 返回
   ``None``（服务继续可用，不抛 ``ImportError``），预测降级到
   :func:`predict_rule_based`，结果为 ``method="rule_placeholder"``。
   **占位预测器不是训练模型**，只用于无权重环境的骨架演示与接口联调，
   准确率远低于真实模型。

## 检查点格式（与 `src.training.trainer` 保存的一致）

``torch.load(best.pt)`` 返回 dict，键与用途：

- ``model_state``：模型 ``state_dict()``（必需）；
- ``config``：训练配置，读 ``max_len``（缺失时用 200）；
- ``vocab``：``Vocab.to_dict()`` 词表快照（优先使用，缺失时回退
  ``<checkpoint>/preprocessing/vocab.json`` → ``data/processed/vocab.json``）；
- ``hyperparams``：模型构造超参（``vocab_size`` / ``embed_dim`` / ``filter_sizes`` …）；
- ``model_type`` / ``model_name`` / ``epoch`` / ``metrics``：元信息与训练指标。

真实训练目录通常还带 ``preprocessing/`` 资源包（``vocab.json`` /
``preprocessor.json`` / ``label_map.json`` / ``manifest.json``），由
``src.preprocessing.processor.load_preprocessing_bundle`` 校验 manifest 哈希后使用；
没有该目录时回退 ``TextPreprocessor.from_resources(max_len=...)``。
（仓库当前的冒烟检查点没有 ``preprocessing/``，真实大头权重目录则带。）

## 降级与报错边界（可用性优先，但不掩盖坏权重）

- 检查点**不存在**或 **torch 缺失** → :func:`load_model` 返回 ``None``（降级规则占位）；
- 检查点**存在但损坏 / 格式不支持 / 与词表或超参不匹配** → 抛 ``RuntimeError``
  （可读提示，绝不把坏权重静默当成占位模式）；
- 推理设备由环境变量 ``INFERENCE_DEVICE`` 控制：默认 ``cpu``，``auto`` 时有 CUDA 用
  CUDA；显式 ``cuda`` 但不可用时告警并回退 ``cpu``；
- 清洗分词后为空的文本由 :class:`Predictor` 返回确定性的 ``0.5 / 0.5`` 中性兜底
  （不抛异常），保证批量接口健壮。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from ..models.base import load_checkpoint, require_torch
from ..models.bilstm_attention import BiLSTMAttention
from ..models.cnn import TextCNN
from ..preprocessing.processor import TextPreprocessor, load_preprocessing_bundle
from ..preprocessing.tokenize import encode_ids
from ..preprocessing.vocab import Vocab

logger = logging.getLogger("src.inference")

#: 标签顺序固定（与 src/models/base.py 的 LABELS 一致，不得调换）
LABELS = ("negative", "positive")

#: 默认模型（可用环境变量 ACTIVE_MODEL 覆盖，见 .env.example）
DEFAULT_MODEL_NAME = "bilstm_attention"
ENV_ACTIVE_MODEL = "ACTIVE_MODEL"

#: 推理设备环境变量：``cpu``（默认）/ ``auto`` / ``cuda`` / ``cuda:0``
ENV_INFERENCE_DEVICE = "INFERENCE_DEVICE"

#: 预测来源标识：只在**降级到规则 / 词典占位预测器**（不是训练模型）时使用。
#: 真实模型可用后 :func:`resolve_method` 返回模型名。
METHOD_RULE_PLACEHOLDER = "rule_placeholder"

#: 支持的模型清单（`GET /api/models` 的输出顺序）
SUPPORTED_MODELS: tuple[tuple[str, str], ...] = (
    ("textcnn", "cnn"),
    ("bilstm_attention", "bilstm_attention"),
)

#: 检查点支持的模型类型（与 `src/models/*.py` 的 ``model_type`` 一致）
SUPPORTED_MODEL_TYPES: tuple[str, ...] = ("textcnn", "bilstm_attention")

#: 仓库根目录：所有路径从源码位置解析，保证从任意工作目录启动服务都能找到权重
REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DIR = REPO_ROOT / "models" / "checkpoints"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
STATS_DIR = REPO_ROOT / "outputs" / "stats"

#: 检查点未记录 max_len 时的默认序列长度（与 .env.example 的 MAX_LEN 一致）
DEFAULT_MAX_LEN = 200

#: :meth:`Predictor.predict_batch` 的默认批大小（避免一次性占用过多内存）
DEFAULT_BATCH_SIZE = 64

#: 清洗分词后为空的文本的确定性兜底概率（两类各 0.5，标签取 positive）
EMPTY_TOKEN_PROBS: dict[str, float] = {"positive": 0.5, "negative": 0.5}

#: 未训练时返回的占位指标
EMPTY_METRICS: dict[str, float] = {
    "accuracy": 0.0,
    "precision": 0.0,
    "recall": 0.0,
    "f1": 0.0,
}


def get_active_model_name() -> str:
    """返回当前生效的模型名（环境变量 ``ACTIVE_MODEL``，默认 ``bilstm_attention``）。"""
    return os.getenv(ENV_ACTIVE_MODEL, DEFAULT_MODEL_NAME)


# --------------------------------------------------------------------------- #
# 真实模型预测器：预处理 → 编码 → 前向 → softmax
# --------------------------------------------------------------------------- #
@dataclass
class Predictor:
    """真实检查点封装：输出与规则占位预测器同构（``text/label/confidence/probs``）。

    Attributes:
        model_name: 检查点名（``ACTIVE_MODEL`` / 请求指定的名字），即 ``method`` 的值。
        model_type: 模型类型，``textcnn`` / ``bilstm_attention``。
        model: 构建好的模型包装对象（``TextCNN`` / ``BiLSTMAttention``，已 ``eval()``）。
        vocab: :class:`src.preprocessing.vocab.Vocab` 词表（与权重配套）。
        max_len: 编码后的固定序列长度（来自检查点 ``config.max_len``）。
        labels: 标签顺序（``negative`` / ``positive``，与模型输出列一一对应）。
        processor: 与检查点配套的清洗 + 分词处理器。
        device: 推理设备字符串（``cpu`` / ``cuda`` …）。
        batch_size: :meth:`predict_batch` 的单批条数。
        torch: ``torch`` 模块（延迟导入后由 :func:`load_model` 注入）。
    """

    model_name: str
    model_type: str
    model: Any
    vocab: Any
    max_len: int
    labels: tuple[str, ...] = LABELS
    processor: TextPreprocessor | None = None
    device: str = "cpu"
    batch_size: int = DEFAULT_BATCH_SIZE
    torch: Any = None

    # ------------------------------------------------------------------ #
    # 推理
    # ------------------------------------------------------------------ #
    def _tokens(self, text: str) -> list[str]:
        """清洗 + 分词；无有效内容时返回空列表（由调用方做中性兜底）。"""
        if self.processor is None:  # pragma: no cover - load_model 保证非空
            raise RuntimeError("Predictor 缺少预处理资源，无法推理")
        _cleaned, tokens = self.processor.transform(text)
        return tokens

    def _forward_logits(self, input_ids: Any) -> Any:
        """统一两种模型的前向：BiLSTM-Attention 返回 ``(logits, attn)``，只取 logits。"""
        output = self.model.forward(input_ids)
        if isinstance(output, tuple):
            output = output[0]
        return output

    def _probabilities(self, ids_batch: Sequence[Sequence[int]]) -> list[dict[str, Any]]:
        """对一批 id 序列做一次前向，返回逐条 ``label / confidence / probs``。

        ``probs`` 四舍五入到 6 位，且用互补方式计算另一类概率，保证两类和恒为 1；
        ``label`` 取最大概率对应的标签（与占位预测器一致的 ``>=`` 平局约定）。
        """
        torch = self.torch
        if torch is None:  # pragma: no cover - load_model 保证注入
            raise RuntimeError("Predictor 缺少 torch 模块，无法推理")
        tensor = torch.tensor(
            [list(ids) for ids in ids_batch], dtype=torch.long, device=self.device
        )
        with torch.no_grad():
            logits = self._forward_logits(tensor)
            probabilities = torch.softmax(logits.float(), dim=1).cpu().tolist()
        results: list[dict[str, Any]] = []
        for row in probabilities:
            positive = round(float(row[1]), 6)
            negative = round(1.0 - positive, 6)
            label = self.labels[1] if positive >= negative else self.labels[0]
            results.append(
                {
                    "label": label,
                    "confidence": max(positive, negative),
                    "probs": {"positive": positive, "negative": negative},
                }
            )
        return results

    @staticmethod
    def _empty_result(text: str) -> dict[str, Any]:
        """清洗分词后为空：确定性返回两类各 0.5（标签 positive），不抛异常。"""
        return {
            "text": text,
            "label": "positive",
            "confidence": 0.5,
            "probs": dict(EMPTY_TOKEN_PROBS),
        }

    def predict(self, text: str) -> dict[str, Any]:
        """预测单条文本，返回 ``{"text","label","confidence","probs"}``。"""
        text_value = "" if text is None else str(text)
        tokens = self._tokens(text_value)
        if not tokens:
            return self._empty_result(text_value)
        ids = encode_ids(tokens, self.vocab, self.max_len)
        prediction = self._probabilities([ids])[0]
        return {"text": text_value, **prediction}

    def predict_batch(self, texts: Sequence[str]) -> list[dict[str, Any]]:
        """批量预测（按 ``batch_size`` 分批前向），每项 ``index/text/label/confidence``。

        空文本 / 清洗后无有效内容的条目返回确定性兜底（``confidence=0.5``），
        不中断整批；返回顺序与输入下标严格一致。
        """
        text_list = ["" if text is None else str(text) for text in texts]
        pending: list[tuple[int, str, list[int]]] = []
        results: dict[int, dict[str, Any]] = {}
        for index, text_value in enumerate(text_list):
            tokens = self._tokens(text_value)
            if not tokens:
                results[index] = {
                    "index": index,
                    "text": text_value,
                    "label": "positive",
                    "confidence": 0.5,
                }
                continue
            pending.append((index, text_value, encode_ids(tokens, self.vocab, self.max_len)))

        batch_size = max(1, int(self.batch_size))
        for start in range(0, len(pending), batch_size):
            chunk = pending[start : start + batch_size]
            for (index, text_value, _ids), prediction in zip(
                chunk, self._probabilities([row[2] for row in chunk])
            ):
                results[index] = {
                    "index": index,
                    "text": text_value,
                    "label": prediction["label"],
                    "confidence": prediction["confidence"],
                }
        return [results[index] for index in range(len(text_list)) if index in results]


# --------------------------------------------------------------------------- #
# 检查点加载（任何格式 / 一致性问题都转成可读的 RuntimeError）
# --------------------------------------------------------------------------- #
#: 构造 TextCNN 时允许从 hyperparams 透传的键
_TEXT_CNN_KEYS: tuple[str, ...] = (
    "embed_dim",
    "num_filters",
    "filter_sizes",
    "num_classes",
    "dropout",
    "padding_idx",
    "freeze_embeddings",
)

#: 构造 BiLSTMAttention 时允许从 hyperparams 透传的键
_BILSTM_KEYS: tuple[str, ...] = (
    "embed_dim",
    "hidden_dim",
    "num_layers",
    "num_classes",
    "dropout",
    "attention_dim",
    "bidirectional",
    "padding_idx",
    "freeze_embeddings",
)


def _checkpoint_path(model_name: str) -> Path:
    """检查点路径 ``models/checkpoints/<name>/best.pt``。"""
    return CHECKPOINT_DIR / model_name / "best.pt"


def _resolve_inference_device(torch_module: Any) -> str:
    """解析 ``INFERENCE_DEVICE``：默认 cpu；``auto`` 时有 CUDA 用 CUDA。"""
    requested = (os.getenv(ENV_INFERENCE_DEVICE, "") or "").strip().lower() or "cpu"
    cuda = getattr(torch_module, "cuda", None)
    cuda_available = bool(cuda is not None and cuda.is_available())
    if requested == "auto":
        return "cuda" if cuda_available else "cpu"
    if requested.startswith("cuda"):
        if not cuda_available:
            logger.warning("INFERENCE_DEVICE=%s 但当前环境没有可用 CUDA，回退 CPU", requested)
            return "cpu"
        return requested
    if requested in {"cpu", "mps"}:
        return requested
    logger.warning("无法识别的 INFERENCE_DEVICE=%r，回退 CPU", requested)
    return "cpu"


def _load_processor_and_vocab(
    checkpoint_dir: Path, model_name: str
) -> tuple[TextPreprocessor | None, Vocab | None]:
    """优先加载检查点自带的 ``preprocessing/`` 资源包（校验 manifest 哈希）。

    Returns:
        ``(processor | None, vocab | None)``；资源包缺失时返回 ``(None, None)``，
        校验失败时 ``logging.warning`` 后降级（仍尽量使用其中未损坏的 ``vocab.json``）。
    """
    bundle_dir = checkpoint_dir / "preprocessing"
    if not bundle_dir.is_dir():
        return None, None
    try:
        processor, vocab, _labels = load_preprocessing_bundle(bundle_dir)
        return processor, vocab
    except Exception as exc:  # noqa: BLE001 - 资源包问题只降级，不阻断服务
        logger.warning(
            "检查点 %s 的 preprocessing/ 资源包校验失败，回退仓库默认预处理资源：%s（%s: %s）",
            model_name,
            bundle_dir,
            type(exc).__name__,
            exc,
        )
    vocab_path = bundle_dir / "vocab.json"
    if vocab_path.is_file():
        try:
            return None, Vocab.load(vocab_path)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"检查点 {model_name} 的预处理词表损坏：{vocab_path}"
                f"（{type(exc).__name__}: {exc}）"
            ) from exc
    return None, None


def _load_vocab(
    checkpoint_dir: Path,
    state: dict[str, Any],
    model_name: str,
    bundle_vocab: Vocab | None,
) -> Vocab:
    """按优先级加载词表：检查点内嵌快照 → ``preprocessing/vocab.json`` → ``data/processed/vocab.json``。"""
    raw = state.get("vocab")
    if isinstance(raw, dict) and raw:
        try:
            return Vocab.from_dict(raw)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"检查点 {model_name} 内嵌词表损坏：{type(exc).__name__}: {exc}"
            ) from exc
    if bundle_vocab is not None:
        return bundle_vocab
    fallback = PROCESSED_DIR / "vocab.json"
    if fallback.is_file():
        try:
            return Vocab.load(fallback)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"仓库默认词表损坏：{fallback}（{type(exc).__name__}: {exc}）"
            ) from exc
    raise RuntimeError(
        f"检查点 {model_name} 缺少配套词表：检查点内无 vocab 快照，"
        f"{checkpoint_dir / 'preprocessing' / 'vocab.json'} 与 {fallback} 均不存在"
    )


def _build_model_instance(model_type: str, hyperparams: dict[str, Any], vocab_size: int) -> Any:
    """按检查点 ``hyperparams`` 重建模型对象（过滤掉构造器不接受 / 缺省的键）。"""
    allowed = _TEXT_CNN_KEYS if model_type == "textcnn" else _BILSTM_KEYS
    kwargs: dict[str, Any] = {}
    for key in allowed:
        if key not in hyperparams:
            continue
        value = hyperparams[key]
        if key == "filter_sizes":
            value = tuple(int(size) for size in value)
        kwargs[key] = value
    kwargs["vocab_size"] = int(vocab_size)

    num_classes = kwargs.get("num_classes")
    if num_classes is not None and int(num_classes) != len(LABELS):
        raise RuntimeError(
            f"检查点 num_classes={num_classes}，但服务只支持二分类（标签顺序 {LABELS}）"
        )
    if model_type == "textcnn":
        return TextCNN(**kwargs)
    return BiLSTMAttention(**kwargs)


def _build_predictor(
    model_name: str,
    checkpoint_path: Path,
    state: dict[str, Any],
    torch_module: Any,
) -> Predictor:
    """由检查点 state 重建 :class:`Predictor`；格式 / 一致性问题统一抛 ``RuntimeError``。"""
    if not isinstance(state, dict):
        raise RuntimeError(
            f"检查点格式不受支持：{checkpoint_path} 的顶层不是 dict（{type(state).__name__}）"
        )
    model_state = state.get("model_state")
    if not isinstance(model_state, dict) or not model_state:
        raise RuntimeError(f"检查点缺少 model_state 权重：{checkpoint_path}")

    model_type = str(state.get("model_type") or state.get("model_name") or model_name).strip()
    if model_type not in SUPPORTED_MODEL_TYPES:
        raise RuntimeError(
            f"不支持的模型类型：{model_type!r}（检查点 {checkpoint_path}；"
            f"当前仅支持 {' / '.join(SUPPORTED_MODEL_TYPES)}）"
        )

    raw_config = state.get("config")
    config = raw_config if isinstance(raw_config, dict) else {}
    raw_hyperparams = state.get("hyperparams")
    hyperparams = raw_hyperparams if isinstance(raw_hyperparams, dict) else {}

    raw_max_len = config.get("max_len")
    try:
        max_len = int(raw_max_len) if raw_max_len is not None else DEFAULT_MAX_LEN
    except (TypeError, ValueError):
        max_len = DEFAULT_MAX_LEN
    if max_len < 1:
        max_len = DEFAULT_MAX_LEN

    checkpoint_dir = checkpoint_path.parent
    processor, bundle_vocab = _load_processor_and_vocab(checkpoint_dir, model_name)
    vocab = _load_vocab(checkpoint_dir, state, model_name, bundle_vocab)

    expected_vocab_size = hyperparams.get("vocab_size")
    if expected_vocab_size is not None:
        try:
            expected_vocab_size = int(expected_vocab_size)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"检查点 hyperparams['vocab_size'] 非法：{expected_vocab_size!r}"
            ) from exc
        if expected_vocab_size != len(vocab):
            raise RuntimeError(
                f"词表规模与检查点 hyperparams 不一致：权重声明 vocab_size={expected_vocab_size}，"
                f"实际词表 {len(vocab)} 项（检查点 {checkpoint_path}）"
            )

    model = _build_model_instance(model_type, hyperparams, len(vocab))
    try:
        model.build()
        model.load_state_dict(model_state)
        model.eval()
    except Exception as exc:  # noqa: BLE001 - 权重与结构不匹配必须显式暴露
        raise RuntimeError(
            f"检查点权重与模型结构不匹配：{checkpoint_path}"
            f"（{type(exc).__name__}: {exc}）"
        ) from exc

    device = _resolve_inference_device(torch_module)
    if device != "cpu":
        try:
            model.to(device)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"无法把模型迁移到推理设备 {device}：{type(exc).__name__}: {exc}"
            ) from exc

    if processor is None:
        # 冒烟检查点 / 早期训练产物没有 preprocessing/：回退仓库默认资源
        try:
            processor = TextPreprocessor.from_resources(max_len=max_len)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"无法加载仓库默认预处理资源（resources/）：{type(exc).__name__}: {exc}"
            ) from exc

    logger.info(
        "已加载真实模型：%s（type=%s, device=%s, max_len=%d, vocab=%d）",
        model_name,
        model_type,
        device,
        max_len,
        len(vocab),
    )
    return Predictor(
        model_name=model_name,
        model_type=model_type,
        model=model,
        vocab=vocab,
        max_len=max_len,
        labels=LABELS,
        processor=processor,
        device=device,
        torch=torch_module,
    )


def load_model(model_name: str | None = None) -> Predictor | None:
    """加载检查点并返回 :class:`Predictor`；不可用时返回 ``None``（降级规则占位）。

    返回 ``None`` 的两种情况（服务保持可用，**不抛异常**）：

    - ``models/checkpoints/<name>/best.pt`` 不存在（未训练 / 名字写错）；
    - PyTorch 未安装（``ImportError``，会 ``logging.warning`` 并提示安装方式）。

    检查点存在但**损坏 / 格式不支持 / 与词表或超参不匹配**时抛 ``RuntimeError``
    （可读提示），避免把坏权重静默降级成占位结果。

    Args:
        model_name: 模型名，``None`` 时取 :func:`get_active_model_name`。

    Returns:
        :class:`Predictor`，或 ``None``（降级规则占位）。
    """
    name = str(model_name or get_active_model_name() or DEFAULT_MODEL_NAME).strip()
    if not name:
        name = DEFAULT_MODEL_NAME
    path = _checkpoint_path(name)
    if not path.is_file():
        return None
    try:
        torch_module = require_torch()
    except ImportError as exc:
        logger.warning("PyTorch 不可用，模型 %s 降级为规则占位预测：%s", name, exc)
        return None
    try:
        state = load_checkpoint(path)
    except FileNotFoundError:
        return None  # 检查点刚被删除：与「不存在」同义
    except ImportError:  # pragma: no cover - require_torch 已在上面检查
        return None
    except Exception as exc:  # noqa: BLE001 - 损坏的权重必须显式暴露
        raise RuntimeError(
            f"检查点损坏或无法读取：{path}（{type(exc).__name__}: {exc}）"
        ) from exc
    try:
        return _build_predictor(name, path, state, torch_module)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底转成可读错误，不泄漏裸异常
        raise RuntimeError(
            f"检查点格式不受支持或已损坏：{path}（{type(exc).__name__}: {exc}）"
        ) from exc


@lru_cache(maxsize=4)
def load_model_cached(model_name: str | None = None) -> Predictor | None:
    """带缓存的 :func:`load_model` 包装，避免每次 HTTP 请求重复读盘。

    缓存键是 ``model_name``（``None`` 也是一个键）。检查点文件在服务运行期间被替换
    时缓存不会自动失效：需重启服务，或调用 ``load_model_cached.cache_clear()``
    （测试在切换 ``ACTIVE_MODEL`` 后必须清缓存，见 ``tests/conftest.py``）。
    """
    return load_model(model_name)


def get_predictor() -> Predictor | None:
    """返回当前活跃模型（``ACTIVE_MODEL``）的 :class:`Predictor`（带缓存）。

    检查点不存在或 torch 缺失时返回 ``None``，调用方应回退
    :func:`predict_rule_based`（服务保持可用，而不是启动失败）。
    """
    return load_model_cached(get_active_model_name())


def is_mock_mode(model: Any) -> bool:
    """``model is None`` 即**降级（规则占位）模式**；真实模型应为对象。"""
    return model is None


def resolve_method(model: Any = None, model_name: str | None = None) -> str:
    """返回本次预测的**来源标识**，供 ``method`` 字段如实标注。

    - 降级模式（``load_model`` 返回 ``None`` / ``get_predictor()`` 为 ``None``）
      → :data:`METHOD_RULE_PLACEHOLDER`；
    - 真实模型 → 实际模型名：优先显式 ``model_name``，其次 ``Predictor.model_name``，
      最后回退当前生效模型名（``ACTIVE_MODEL``）。
    """
    if is_mock_mode(model):
        return METHOD_RULE_PLACEHOLDER
    if model_name:
        return model_name
    predictor_name = getattr(model, "model_name", None)
    if predictor_name:
        return str(predictor_name)
    return get_active_model_name()



def predict_text(text: str, model: Any = None, model_name: str | None = None) -> dict[str, Any]:
    """预测单条评论情感。

    Args:
        text: 评论文本（调用方应已去除首尾空白）。
        model: :class:`Predictor`（或任何带 ``predict(text)`` 的对象）；
            ``None`` 走规则占位分支。
        model_name: 模型名（仅用于日志与真实实现的检查点定位）。

    Returns:
        ``{"text": str, "label": "positive"|"negative", "confidence": float,
           "probs": {"positive": float, "negative": float}}``

    - ``model is None`` → :func:`predict_rule_based`（规则词典**占位**，接口联调用，
      不是训练模型）；
    - 否则调用模型的 ``predict(text)``：分词（`src.preprocessing.tokenize`）→
      ``encode_ids``（补到 max_len）→ 前向得 logits → ``softmax`` → 取最大类；
      BiLSTM-Attention 的 ``(logits, attn)`` 已在 :class:`Predictor` 内部只取 logits。
    """
    if is_mock_mode(model):
        return predict_rule_based(text)
    predictor = getattr(model, "predict", None)
    if not callable(predictor):
        raise TypeError(
            f"model 必须是 None 或带 predict(text) 的预测器对象，收到 {type(model).__name__}"
        )
    result = dict(predictor(text))
    result.setdefault("text", text)
    return result


def predict_batch(
    texts: Sequence[str],
    model: Any = None,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """批量预测，返回逐条结果（含 ``index``）。

    真实模型有 ``predict_batch`` 时优先用它（`Predictor` 内部分批前向，减少 Python
    循环开销）；否则逐条调用 :func:`predict_text`，结果与单条接口完全一致。

    Args:
        texts: 文本列表。
        model / model_name: 同 :func:`predict_text`。

    Returns:
        ``[{"index": 0, "text": str, "label": str, "confidence": float}, ...]``
    """
    if not is_mock_mode(model):
        batch_predict = getattr(model, "predict_batch", None)
        if callable(batch_predict):
            return [dict(item) for item in batch_predict(list(texts))]
    results: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        prediction = predict_text(text, model=model, model_name=model_name)
        results.append(
            {
                "index": index,
                "text": prediction["text"],
                "label": prediction["label"],
                "confidence": prediction["confidence"],
            }
        )
    return results


def available_models() -> list[dict[str, Any]]:
    """汇总可用模型信息，供 ``GET /api/models`` 使用。

    指标优先读取 ``outputs/stats/metrics_<model>.json`` 的 ``metrics`` 字段
    （由 `src.evaluation.report.save_metrics_json` 产出）；
    检查点 ``models/checkpoints/<model>/best.pt`` 存在才认为 ``ready=True``
    （``ready=False`` 时该模型预测会降级为规则占位）。
    """
    models: list[dict[str, Any]] = []
    for name, model_type in SUPPORTED_MODELS:
        metrics = dict(EMPTY_METRICS)
        metrics_path = STATS_DIR / f"metrics_{name}.json"
        if metrics_path.exists():
            try:
                payload = json.loads(metrics_path.read_text(encoding="utf-8"))
                stored = payload.get("metrics", payload)
                for key in EMPTY_METRICS:
                    if key in stored:
                        metrics[key] = float(stored[key])
            except (json.JSONDecodeError, TypeError, ValueError):
                # 指标文件损坏时保持占位值，不影响服务可用性
                pass
        models.append(
            {
                "name": name,
                "type": model_type,
                "metrics": metrics,
                "ready": (CHECKPOINT_DIR / name / "best.pt").exists(),
            }
        )
    return models


# --------------------------------------------------------------------------- #
# 规则 / 词典占位预测器（**占位，不是训练模型**）
#
# 词表刻意保持精简（四类合计 100 余词），只覆盖电商 / 服务评论的典型表达：
#
# - 情感词给基础强度权重（正/负）；
# - 程度副词（很 / 非常 / 太 …）把紧随其后的情感词成倍放大或缩小；
# - 否定词（不 / 没 / 不太 …）把紧随其后的情感词极性翻转；
# - 先按标点切成小句再统计，避免「不好，但是很好」里前一句的否定词
#   跨越小句去翻转后一句的情感词。
#
# 真实模型可用时（``load_model`` 返回 ``Predictor``）本段不会参与预测，
# 但**保留为降级分支**：检查点缺失 / torch 缺失时服务仍可用。
# --------------------------------------------------------------------------- #

POSITIVE_WORDS: dict[str, float] = {
    "好": 1.0,
    "不错": 1.2,
    "棒": 1.2,
    "赞": 1.1,
    "优秀": 1.3,
    "完美": 1.3,
    "满意": 1.2,
    "喜欢": 1.2,
    "推荐": 1.1,
    "值得": 1.0,
    "划算": 1.1,
    "便宜": 1.0,
    "实惠": 1.1,
    "超值": 1.2,
    "给力": 1.2,
    "好用": 1.1,
    "实用": 1.0,
    "耐用": 1.0,
    "舒服": 1.0,
    "舒适": 1.0,
    "漂亮": 1.0,
    "好看": 1.1,
    "精致": 1.1,
    "清晰": 1.0,
    "流畅": 1.1,
    "稳定": 1.0,
    "正品": 1.0,
    "完好": 1.0,
    "无损": 1.0,
    "惊喜": 1.2,
    "开心": 1.1,
    "高兴": 1.1,
    "放心": 1.0,
    "感动": 1.2,
    "省心": 1.1,
    "贴心": 1.1,
    "耐心": 1.0,
    "热情": 1.0,
    "靠谱": 1.1,
    "专业": 1.1,
    "细心": 1.0,
    "到位": 1.0,
    "及时": 1.0,
    "迅速": 1.0,
    "解决": 1.1,
    "有效": 1.1,
    "改善": 1.0,
    "提升": 1.0,
    "回购": 1.1,
    "还会再来": 1.1,
    "可以": 0.8,
    "新鲜": 1.0,
}

NEGATIVE_WORDS: dict[str, float] = {
    "差": 1.2,
    "慢": 1.0,
    "坏": 1.2,
    "短": 1.0,
    "贵": 0.8,
    "失望": 1.3,
    "差评": 1.3,
    "差劲": 1.3,
    "糟糕": 1.3,
    "垃圾": 1.4,
    "烂": 1.3,
    "坑": 1.2,
    "劣质": 1.3,
    "粗糙": 1.0,
    "丑": 1.1,
    "难用": 1.2,
    "卡顿": 1.2,
    "死机": 1.3,
    "故障": 1.2,
    "破损": 1.3,
    "划痕": 1.0,
    "掉色": 1.1,
    "缩水": 1.0,
    "后悔": 1.2,
    "生气": 1.2,
    "恼火": 1.2,
    "无语": 1.0,
    "敷衍": 1.2,
    "忽悠": 1.3,
    "欺骗": 1.4,
    "虚假": 1.3,
    "假货": 1.3,
    "冷漠": 1.1,
    "傲慢": 1.1,
    "推诿": 1.2,
    "拖延": 1.0,
    "不理": 1.1,
    "不理人": 1.2,
    "崩溃": 1.2,
    "恶心": 1.3,
    "浪费": 1.1,
    "不值": 1.1,
    "一般般": 0.8,
    "一般": 0.6,
    "凑合": 0.6,
    "遗憾": 1.0,
    "讨厌": 1.3,
    "烦": 1.0,
    "难受": 1.1,
    "吵": 1.0,
    "臭": 1.2,
    "脏": 1.2,
    "陈旧": 1.0,
    "小气": 1.0,
}

#: 程度副词 → 情感强度倍数（"有点" 之类的弱化词 < 1）
DEGREE_ADVERBS: dict[str, float] = {
    "极其": 1.8,
    "非常": 1.6,
    "特别": 1.6,
    "超级": 1.6,
    "极": 1.6,
    "十分": 1.5,
    "太": 1.5,
    "格外": 1.5,
    "超": 1.4,
    "相当": 1.4,
    "尤其": 1.4,
    "很": 1.3,
    "挺": 1.2,
    "真的": 1.2,
    "实在": 1.2,
    "比较": 0.9,
    "还算": 0.9,
    "还": 0.85,
    "有点": 0.7,
    "稍微": 0.7,
    "略": 0.7,
}

#: 否定词（按长度优先匹配，"不太" 优先于 "不"）
NEGATION_WORDS: tuple[str, ...] = (
    "不是",
    "不太",
    "不够",
    "没有",
    "难以",
    "不",
    "没",
    "无",
    "非",
    "未",
    "别",
    "莫",
    "勿",
)

#: 转折连词：转折之前的情感修饰不应跨越转折去影响后面的情感词
CONTRAST_WORDS: tuple[str, ...] = ("但是", "不过", "然而", "可惜", "只是", "但", "却")

#: 否定词 / 程度副词的观察窗口（情感词前多少个字符）
NEGATION_WINDOW = 4

#: 情感分 → 概率的中性平滑项：score=0 时概率恰为 0.5，且概率严格落在 (0,1)
NEUTRAL_SMOOTHING = 2.0

#: 概率上下限（双保险：即使公式变化也不会给出 0 / 1 的极端置信度）
MIN_PROB = 0.01
MAX_PROB = 0.99

#: 各情感词 → 带符号权重（正数正面、负数负面）；长词优先匹配（"一般般" 先于 "一般"）
_SENTIMENT_LEXICON: dict[str, float] = {
    **POSITIVE_WORDS,
    **{word: -weight for word, weight in NEGATIVE_WORDS.items()},
}

_SENTIMENT_WORD_RE = re.compile(
    "|".join(re.escape(word) for word in sorted(_SENTIMENT_LEXICON, key=len, reverse=True))
)

#: 小句切分：句号 / 问号 / 叹号 / 分号 / 逗号 / 顿号 / 换行
_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;，,、\n\r]+")

#: 按长度倒序的词表，便于做「后缀匹配」
_DEGREE_ADVERBS_BY_LENGTH = tuple(sorted(DEGREE_ADVERBS, key=len, reverse=True))
_NEGATION_BY_LENGTH = tuple(sorted(NEGATION_WORDS, key=len, reverse=True))


def _drop_contrast_prefix(prefix: str) -> str:
    """只保留最后一个转折连词之后的部分（转折前的修饰语不跨转折生效）。"""
    cut = 0
    for marker in CONTRAST_WORDS:
        index = prefix.rfind(marker)
        if index >= 0:
            cut = max(cut, index + len(marker))
    return prefix[cut:]


def _degree_multiplier(prefix: str) -> float:
    """情感词紧邻前缀若以程度副词结尾，返回其倍数，否则 1.0。"""
    for adverb in _DEGREE_ADVERBS_BY_LENGTH:
        if prefix.endswith(adverb):
            return DEGREE_ADVERBS[adverb]
    return 1.0


def _tail_degree_adverb(prefix: str) -> str | None:
    """若前缀以程度副词结尾，返回该副词，否则 ``None``。"""
    for adverb in _DEGREE_ADVERBS_BY_LENGTH:
        if prefix.endswith(adverb):
            return adverb
    return None


def _split_trailing_negation(prefix: str) -> tuple[str, bool]:
    """剥离紧邻情感词的否定词，返回（剥离后的前缀, 是否被否定）。

    只认**紧邻**情感词的否定词，并且词长更长的修饰语优先，否则单字否定词会误伤
    别的词：``非常`` 不该被当成 ``非``，``特别`` 不该被当成 ``别``；而 ``不太``
    作为否定词比 ``太`` 长，因此按否定处理。剥离否定词后再看程度副词，
    于是「非常不满意」= ``-(满意 × 1.6)``。
    """
    degree = _tail_degree_adverb(prefix)
    for negation in _NEGATION_BY_LENGTH:
        if prefix.endswith(negation):
            if degree is not None and len(degree) > len(negation):
                return prefix, False  # 「特别」「非常」等程度副词优先
            return prefix[: -len(negation)], True
    return prefix, False


def _score_clause(clause: str) -> float:
    """对单个小句累加带符号情感分。"""
    score = 0.0
    for match in _SENTIMENT_WORD_RE.finditer(clause):
        word = match.group()
        prefix = _drop_contrast_prefix(
            clause[max(0, match.start() - NEGATION_WINDOW) : match.start()]
        )
        # 词表里本身就含否定成分的词（「不错」「不理人」）不再被上下文否定翻转，
        # 否则会出现「也不理人」被判成正面的双重否定误判。
        if any(word.startswith(negation) for negation in NEGATION_WORDS):
            score += _SENTIMENT_LEXICON[word] * _degree_multiplier(prefix)
            continue
        prefix, negated = _split_trailing_negation(prefix)
        value = _SENTIMENT_LEXICON[word] * _degree_multiplier(prefix)
        score += -value if negated else value
    return score


def rule_based_score(text: str) -> float:
    """返回规则占位预测器的**原始情感分**（>0 偏正面，<0 偏负面，=0 中性）。

    仅用于调试、测试与解释，不对外暴露为接口字段。
    """
    clauses = [clause for clause in _CLAUSE_SPLIT_RE.split(text or "") if clause.strip()]
    return sum(_score_clause(clause) for clause in clauses)


def _score_to_probability(score: float) -> float:
    """把无界情感分压到 ``(0, 1)`` 的正面概率（sigmoid 形状的有理近似）。

    ``score = 0``（无词表命中）时返回 0.5；分数越大越接近 1，越小越接近 0，
    但由于分母始终比分子大 2，结果永远不会是 0 或 1，另加 [MIN, MAX] 兜底。
    """
    probability = 0.5 + 0.5 * (score / (abs(score) + NEUTRAL_SMOOTHING))
    return round(min(max(probability, MIN_PROB), MAX_PROB), 4)


def predict_rule_based(text: str) -> dict[str, Any]:
    """规则 / 词典**占位**预测器：返回与真实模型同构的预测结果。

    **这不是训练模型**：仅用内置情感词表 + 程度副词 + 否定翻转 + 按小句统计，
    让骨架演示时「明显好评 → positive、明显差评 → negative」，方向符合直觉。
    真实模型可用时 :func:`predict_text` 走真实前向分支；只有检查点缺失或 torch
    未安装时才调用本函数（**保留的降级分支**，保证服务始终可用）。

    Args:
        text: 评论文本。

    Returns:
        ``{"text": str, "label": "positive"|"negative", "confidence": float,
           "probs": {"positive": float, "negative": float}}``

    特性：
        - **确定性**：同一文本每次返回完全相同的结果（无随机数）；
        - 概率严格落在 ``(0, 1)``，不会出现 0 / 1 的极端置信度；
        - 无任何词表命中时按中性处理（正面概率 0.5，标签取 positive）。
    """
    score = rule_based_score(text)
    prob_positive = _score_to_probability(score)
    prob_negative = round(1.0 - prob_positive, 4)
    label = "positive" if prob_positive >= prob_negative else "negative"
    return {
        "text": text,
        "label": label,
        "confidence": max(prob_positive, prob_negative),
        "probs": {"positive": prob_positive, "negative": prob_negative},
    }
