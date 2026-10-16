"""推理服务核心层：模型加载与预测逻辑（与 Web 框架解耦）。

**当前为占位实现，不是训练模型**：:func:`load_model` 始终返回 ``None``，
预测走 :func:`predict_rule_based` —— 一个**规则 / 词典占位预测器**
（内置小型中文情感词表 + 程度副词加权 + 否定词翻转 + 按小句切分统计）。
它只保证「明显好评 → positive、明显差评 → negative」这一方向正确，
不是任何形式的机器学习模型，准确率远低于真实模型，仅用于骨架演示与接口联调。
同一文本每次返回完全相同的概率（确定性），且概率严格落在 ``(0, 1)`` 内。

替换为真实模型的步骤（只改本文件即可）：

1. :func:`load_model`：读 ``models/checkpoints/<name>/best.pt``（`:mod:`src.models.base.load_checkpoint`）
   与词表 ``data/processed/vocab.json``，构造 `TextCNN` / `BiLSTMAttention`，
   ``load_state_dict`` 后 ``eval()``，返回一个可调用的预测器对象；
2. :func:`predict_text`：分词 → ``encode_ids`` → 前向 → ``softmax`` → 极性与置信度；
   ``load_model`` 一旦返回非 ``None`` 对象，:func:`predict_text` 即走真实分支，
   占位函数 :func:`predict_rule_based` 不再被调用（可删除，或保留为
   ``SENTIMENT_MOCK=1`` 的降级分支）。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

#: 标签顺序固定（与 src/models/base.py 的 LABELS 一致，不得调换）
LABELS = ("negative", "positive")

#: 默认模型（可用环境变量 ACTIVE_MODEL 覆盖，见 .env.example）
DEFAULT_MODEL_NAME = "bilstm_attention"
ENV_ACTIVE_MODEL = "ACTIVE_MODEL"

#: 支持的模型清单（`GET /api/models` 的输出顺序）
SUPPORTED_MODELS: tuple[tuple[str, str], ...] = (
    ("textcnn", "cnn"),
    ("bilstm_attention", "bilstm_attention"),
)

CHECKPOINT_DIR = Path("models") / "checkpoints"
STATS_DIR = Path("outputs") / "stats"

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


def load_model(model_name: str | None = None) -> Any:
    """加载并返回用于预测的模型对象。

    Args:
        model_name: 模型名，``None`` 时取 :func:`get_active_model_name`。

    Returns:
        ``None`` 表示**占位（mock）模式**；真实实现应返回一个具备
        ``predict(text) -> {"label": str, "confidence": float, "probs": dict}`` 的对象。

    TODO(实现):
        1. ``name = model_name or get_active_model_name()``；
        2. ``path = CHECKPOINT_DIR / name / "best.pt"``，不存在则返回 ``None``（降级到 mock）；
        3. ``torch = src.models.base.require_torch()``，``state = load_checkpoint(path)``；
        4. 按 ``name`` 构造 ``TextCNN`` / ``BiLSTMAttention``，``build()`` + ``load_state_dict``；
        5. ``model.eval()``，与词表一起封装为 ``Predictor`` 返回；
        6. torch 缺失或权重损坏时捕获异常并重新抛 ``RuntimeError``（提示检查点路径与安装命令）。
    """
    return None


@lru_cache(maxsize=4)
def load_model_cached(model_name: str | None = None) -> Any:
    """带缓存的 :func:`load_model` 包装，避免每次 HTTP 请求重复读盘。

    注意：模型文件在服务运行期间被替换时，缓存不会自动失效，
    需重启服务（或后续加 mtime 校验）。
    """
    return load_model(model_name)


def is_mock_mode(model: Any) -> bool:
    """``model is None`` 即为 mock 模式（真实模型应为对象）。"""
    return model is None


def predict_text(text: str, model: Any = None, model_name: str | None = None) -> dict[str, Any]:
    """预测单条评论情感。

    Args:
        text: 评论文本（调用方应已去除首尾空白）。
        model: :func:`load_model` 的返回值；``None`` 走 mock 分支。
        model_name: 模型名（仅用于日志与真实实现的检查点定位）。

    Returns:
        ``{"text": str, "label": "positive"|"negative", "confidence": float,
           "probs": {"positive": float, "negative": float}}``

    TODO(实现):
        - ``model is None`` 时返回 :func:`predict_rule_based`（规则词典占位，接口联调用）；
        - 否则：分词（`src.preprocessing.tokenize`）→ ``encode_ids``（补到 max_len）
          → 前向得 logits → ``softmax`` → 取最大类；
        - BiLSTM-Attention 的 ``forward`` 返回 ``(logits, attn)``，此处只取 logits；
        - 置信度 = 最大类概率，``probs`` 保留两位小数以内的 float。
    """
    if is_mock_mode(model):
        return predict_rule_based(text)
    raise NotImplementedError(
        "TODO: 真实模型推理尚未实现，请实现 src/inference/service.py:load_model 与 predict_text "
        "（当前 load_model 始终返回 None，因此本分支不会被触发）"
    )


def predict_batch(
    texts: Sequence[str],
    model: Any = None,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """批量预测，返回逐条结果（含 ``index``）。

    Args:
        texts: 文本列表。
        model / model_name: 同 :func:`predict_text`。

    Returns:
        ``[{"index": 0, "text": str, "label": str, "confidence": float}, ...]``

    TODO(实现):
        - 真实实现应按 ``batch_size`` 分批前向（避免一次性占用过多显存/内存），
          例如每批 64 条；
        - 目前逐条调用 :func:`predict_text`，结果与单条接口完全一致。
    """
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
    检查点 ``models/checkpoints/<model>/best.pt`` 存在才认为 ``ready=True``。

    TODO(实现): 真实训练产出指标后，此处会自然读出真实数值，无需改接口。
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
# 真实模型接入后（``load_model`` 返回非 ``None``），本段与
# :func:`predict_rule_based` 可以整体删除。
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
    真实模型接入后 :func:`load_model` 返回非 ``None``，:func:`predict_text`
    走真实前向分支，本函数不再被调用（可删除）。

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
