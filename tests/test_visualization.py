"""可视化测试：曲线 / 混淆矩阵 / Attention / 词云产出非空 PNG。"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("matplotlib")

from src.preprocessing.dataset import Record  # noqa: E402
from src.visualization.curves import (  # noqa: E402
    load_history,
    plot_accuracy_curve,
    plot_loss_curve,
)
from src.visualization.matrices import (  # noqa: E402
    plot_attention_heatmap,
    plot_confusion_matrix,
)
from src.visualization.wordclouds import (  # noqa: E402
    word_frequencies,
)

HISTORY = [
    {"epoch": 1, "train_loss": 0.8, "val_loss": 0.9, "val_acc": 0.5, "lr": 0.001},
    {"epoch": 2, "train_loss": 0.5, "val_loss": 0.6, "val_acc": 0.7, "lr": 0.001},
    {"epoch": 3, "train_loss": 0.3, "val_loss": 0.5, "val_acc": 0.8, "lr": 0.001},
]


def test_plot_loss_and_accuracy_curves(tmp_path):
    loss_path = plot_loss_curve(HISTORY, tmp_path / "loss_curve.png")
    accuracy_path = plot_accuracy_curve(HISTORY, tmp_path / "accuracy_curve.png")
    assert loss_path.is_file() and loss_path.stat().st_size > 0
    assert accuracy_path.is_file() and accuracy_path.stat().st_size > 0


def test_plot_accuracy_curve_without_train_acc(tmp_path):
    history = [{"epoch": 1, "train_loss": 0.8, "val_loss": 0.9, "val_acc": 0.5}]
    path = plot_accuracy_curve(history, tmp_path / "accuracy_curve.png")
    assert path.is_file() and path.stat().st_size > 0


def test_plot_curves_reject_empty_history(tmp_path):
    with pytest.raises(ValueError):
        plot_loss_curve([], tmp_path / "loss.png")
    with pytest.raises(ValueError):
        plot_accuracy_curve([], tmp_path / "acc.png")


def test_plot_confusion_matrix_and_normalized(tmp_path):
    matrix = [[8, 2], [3, 7]]
    raw = plot_confusion_matrix(matrix, tmp_path / "confusion_matrix.png")
    normalized = plot_confusion_matrix(
        matrix, tmp_path / "confusion_matrix_norm.png", normalize=True
    )
    assert raw.is_file() and raw.stat().st_size > 0
    assert normalized.is_file() and normalized.stat().st_size > 0


def test_plot_confusion_matrix_validation(tmp_path):
    with pytest.raises(ValueError):
        plot_confusion_matrix([[1, 2, 3]], tmp_path / "x.png")
    with pytest.raises(ValueError):
        plot_confusion_matrix([[1, 2], [3, 4]], tmp_path / "x.png", labels=("negative",))


def test_plot_attention_heatmap_top_k(tmp_path):
    tokens = [f"词{index}" for index in range(10)]
    weights = [index / 45 for index in range(10)]
    path = plot_attention_heatmap(tokens, weights, tmp_path / "attention_heatmap.png", top_k=5)
    assert path.is_file() and path.stat().st_size > 0
    with pytest.raises(ValueError):
        plot_attention_heatmap(["a"], [0.5, 0.5], tmp_path / "x.png")
    with pytest.raises(ValueError):
        plot_attention_heatmap([], [], tmp_path / "x.png")


def test_load_history_roundtrip_and_missing_file(tmp_path):
    log_path = tmp_path / "train.json"
    log_path.write_text(json.dumps({"history": HISTORY}), encoding="utf-8")
    assert load_history(log_path) == HISTORY
    assert load_history(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert load_history(broken) == []


def test_word_frequencies_counts_and_truncates():
    frequencies = word_frequencies(
        [["好", "好", "差"], ["好", "不错", "<pad>"]], top_n=2
    )
    assert frequencies == {"好": 3, "差": 1}
    assert "<pad>" not in frequencies


def test_word_frequencies_remove_markers_before_ranking_and_keep_negation():
    tokens = ["标点分隔"] * 100 + ["标点感叹", "标点疑问", "表情笑哭", "表情未知", "<unk>", "<PAD>"]
    tokens += ["讲解", "讲解", "没有", "不", "标点", "表情", "表情丰富"]
    assert word_frequencies([tokens], top_n=2) == {"讲解": 2, "没有": 1}
    complete = word_frequencies([tokens], top_n=None)
    assert set(complete) == {"讲解", "没有", "不", "标点", "表情", "表情丰富"}


def test_wordcloud_direct_frequencies_cannot_bypass_marker_filter(tmp_path, monkeypatch):
    pytest.importorskip("wordcloud")
    from wordcloud import WordCloud
    from src.visualization.wordclouds import plot_wordcloud

    captured = []
    original = WordCloud.generate_from_frequencies
    def capture(self, frequencies, *args, **kwargs):
        captured.append(dict(frequencies))
        return original(self, frequencies, *args, **kwargs)
    monkeypatch.setattr(WordCloud, "generate_from_frequencies", capture)
    plot_wordcloud({"标点分隔": 1000, "表情笑哭": 100, "清晰": 3, "没有": 2}, tmp_path / "cloud.png")
    assert captured[0] == {"清晰": 3, "没有": 2}
    assert all(set(frequencies) == {"清晰", "没有"} for frequencies in captured)


def test_project_keyword_ranking_ignores_processing_markers():
    from src.artifacts.pipeline import _build_keywords
    rows = [{"tokens": "标点分隔 标点分隔 标点感叹 表情狗头 清晰 清晰 没有"},
            {"tokens": "标点疑问 表情未知 糟糕"}]
    predictions = [{"label": "positive"}, {"label": "negative"}]
    result = _build_keywords(rows, predictions)
    assert result['positive'][0] == {"word": "清晰", "count": 2, "weight": 1.0}
    assert {item["word"] for item in result["positive"]} == {"清晰", "没有"}
    assert result["negative"] == [{"word": "糟糕", "count": 1, "weight": 1.0}]


@pytest.mark.parametrize("use_fallback", [False, True])
def test_video_keywords_do_not_split_markers_into_fake_content(monkeypatch, use_fallback):
    from importlib import import_module
    from src.videos.placeholder import _keywords_from_texts

    def unavailable(*args, **kwargs):
        raise ImportError("simulate missing jieba")
    if use_fallback:
        monkeypatch.setattr(import_module('src.preprocessing.tokenize'), "tokenize", unavailable)
    words = {item["word"] for item in _keywords_from_texts(["讲解清晰 标点分隔 表情笑哭 标点疑问"]) }
    assert "讲解" in words and "清晰" in words
    assert not {"标点", "分隔", "疑问", "表情", "笑哭"} & words


def test_wordclouds_by_label(tmp_path):
    pytest.importorskip("wordcloud")
    from src.visualization.wordclouds import plot_wordclouds_by_label

    records = [
        Record("好 好 不错", "positive", "好 好 不错", {}),
        Record("差 坏 差", "negative", "差 坏 差", {}),
    ]
    paths = plot_wordclouds_by_label(records, tmp_path)
    assert set(paths) == {"positive", "negative"}
    assert paths["positive"].is_file() and paths["positive"].stat().st_size > 0
    assert paths["negative"].is_file() and paths["negative"].stat().st_size > 0


def test_wordclouds_by_label_validation(tmp_path):
    pytest.importorskip("wordcloud")
    from src.visualization.wordclouds import plot_wordcloud, plot_wordclouds_by_label

    records = [Record("好 好", "positive", "好 好", {})]
    with pytest.raises(ValueError):
        plot_wordclouds_by_label(records, tmp_path)
    with pytest.raises(ValueError):
        plot_wordcloud({}, tmp_path / "empty.png")
