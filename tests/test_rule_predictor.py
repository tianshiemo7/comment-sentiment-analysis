"""规则 / 词典占位预测器（``service.predict_rule_based``）的行为测试。

关注点（**不断言具体概率数值** —— 词表会继续调整，方向语义才是指标）：

1. 方向正确：明显好评 → positive、明显差评 → negative（旧版 md5 伪随机占位
   会把「这个商品质量很好……」判成 negative，属于回归点）；
2. 否定词翻转：「不好用」「不满意」「不推荐」必须翻成负面；
3. 程度副词加权：「非常失望」比「有点失望」更负面；
4. 转折连词不跨小句生效：「不贵但是很好」应为正面；
5. 确定性 + 概率严格落在 (0, 1)；
6. 未接真实模型时 ``predict_text`` 走规则占位分支；
7. 词表规模符合占位设计（60~150 词）。
"""

from __future__ import annotations

import pytest

from src.inference import service

DIRECTION_CASES = [
    ("这个商品质量很好，物流也快，非常满意。", "positive"),
    ("质量太差了，用了一天就坏了，非常失望。", "negative"),
    ("发货慢，客服也不理人，差评。", "negative"),
    ("客服态度特别好，问题很快就解决了。", "positive"),
    ("不好用，非常不满意。", "negative"),
]


@pytest.mark.parametrize("text,expected_label", DIRECTION_CASES)
def test_direction_is_intuitive(text, expected_label):
    result = service.predict_rule_based(text)
    assert result["label"] == expected_label
    expected_sign = 1 if expected_label == "positive" else -1
    assert service.rule_based_score(text) * expected_sign > 0


@pytest.mark.parametrize("text,expected_label", DIRECTION_CASES)
def test_predict_text_falls_back_to_rule_based(text, expected_label):
    assert service.predict_text(text, model=None) == service.predict_rule_based(text)
    assert service.predict_text(text, model=None)["label"] == expected_label


def test_negation_flips_polarity():
    assert service.rule_based_score("好用，满意") > 0
    assert service.rule_based_score("不好用，不满意") < 0
    assert service.predict_rule_based("不好用，非常不满意。")["label"] == "negative"
    # 「不推荐」由「不」+ 正面词「推荐」翻转而来
    assert service.predict_rule_based("客服态度很差，不推荐。")["label"] == "negative"


def test_degree_adverb_amplifies_sentiment():
    strong = service.rule_based_score("非常失望")
    weak = service.rule_based_score("有点失望")
    assert strong < weak < 0
    assert service.rule_based_score("非常好") > service.rule_based_score("好") > 0


def test_contrast_conjunction_does_not_cross_clause():
    """转折连词之前的修饰语不应翻转转折之后的情感词。"""
    assert service.rule_based_score("不贵但是很好") > 0


def test_negation_is_not_confused_with_adverbs():
    """「非常」「特别」不能被拆成否定词「非」「别」。"""
    assert service.rule_based_score("非常满意") > 0
    assert service.rule_based_score("态度特别好") > 0


def test_deterministic_for_same_text():
    text = "服务态度特别好，下次还会再来。"
    assert service.predict_rule_based(text) == service.predict_rule_based(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "今天天气不错但和商品无关的句子",  # 命中「不错」
        "这是一条没有任何情感词的陈述。",
        "很好" * 40,
        "太差了" * 40,
        "不好用，非常不满意。",
    ],
)
def test_probabilities_strictly_inside_unit_interval(text):
    result = service.predict_rule_based(text)
    probs = result["probs"]
    assert 0.0 < probs["positive"] < 1.0
    assert 0.0 < probs["negative"] < 1.0
    assert probs["positive"] + probs["negative"] == pytest.approx(1.0, abs=1e-9)
    assert result["confidence"] == pytest.approx(max(probs.values()))
    assert result["text"] == text


def test_neutral_text_is_half_probability():
    result = service.predict_rule_based("这是一条没有任何情感词的陈述。")
    assert result["probs"] == {"positive": 0.5, "negative": 0.5}
    assert service.rule_based_score("这是一条没有任何情感词的陈述。") == 0.0


def test_lexicon_size_and_consistency():
    total = (
        len(service.POSITIVE_WORDS)
        + len(service.NEGATIVE_WORDS)
        + len(service.DEGREE_ADVERBS)
        + len(service.NEGATION_WORDS)
    )
    assert 60 <= total <= 150, f"占位词表应保持精简（60~150 词），当前 {total}"
    assert not (set(service.POSITIVE_WORDS) & set(service.NEGATIVE_WORDS))
    assert all(weight > 0 for weight in service.POSITIVE_WORDS.values())
    assert all(weight > 0 for weight in service.NEGATIVE_WORDS.values())
