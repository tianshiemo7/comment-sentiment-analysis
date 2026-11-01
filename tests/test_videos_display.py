"""Representative comments must display originals, not model features."""
from copy import deepcopy

import pytest

from src.preprocessing.clean import clean_text
from src.videos.display import original_comment_text, restore_top_comments
from src.videos.pipeline import _clean_comment_records
from src.videos.placeholder import _build_real_top_comments


def test_new_representative_comments_use_raw_text_but_predictions_keep_clean_text():
    raw = "最后一波，DK赢了！[doge]"
    records, _ = _clean_comment_records([{"text": raw, "likes": 24}])
    before = deepcopy(records)
    assert "标点分隔" in records[0]["text"]
    assert records[0]["raw_text"] == raw
    result = _build_real_top_comments(records, [{"label": "positive", "confidence": 0.617}])
    assert result == {"positive": [
        {"text": raw, "like": 24, "label": "positive", "confidence": 0.617},
    ], "negative": []}
    assert records == before


@pytest.mark.parametrize("source_kind", ["clean", "crawl"])
def test_historical_comments_recover_exact_original_and_keep_metadata(source_kind):
    raw = "如果没赢，DK会怎样？😂\n真的！"
    normalized = clean_text(raw)
    source = {"text": normalized, "raw_text": raw} if source_kind == "clean" else {"text": raw}
    payload = {"negative": [
        {"text": normalized, "like": 853, "confidence": 0.823, "label": "negative"},
    ], "positive": []}
    before = deepcopy(payload)
    result = restore_top_comments(payload, [source])
    assert result["negative"] == [{**payload["negative"][0], "text": raw}]
    assert payload == before
    assert restore_top_comments(result, [source]) == result


def test_historical_comments_without_source_hide_markers_without_inventing_punctuation():
    text = "最后一波 标点分隔 dk赢了 标点感叹 表情狗头"
    payload = {"positive": [{"text": text, "like": 24, "label": "positive", "confidence": 0.617}]}
    result = restore_top_comments(payload)
    assert result["positive"][0] == {**payload["positive"][0], "text": "最后一波 dk赢了"}
    assert payload["positive"][0]["text"] == text


def test_original_text_can_literally_mention_a_processing_marker():
    raw = "评论中为什么会出现 标点分隔 这个词？"
    source = {"text": clean_text(raw), "raw_text": raw}
    payload = {"positive": [{"text": source["text"], "like": 1, "label": "positive", "confidence": 0.9}]}
    assert restore_top_comments(payload, [source])["positive"][0]["text"] == raw


def test_ambiguous_originals_are_not_arbitrarily_attributed():
    records = [{"text": "DK赢了！"}, {"text": "dk赢了!"}]
    payload = {"positive": [{"text": clean_text(records[0]["text"]), "like": 1}]}
    assert restore_top_comments(payload, records)["positive"][0]["text"] == "dk赢了"


@pytest.mark.parametrize("record,expected", [
    ({"raw_text": "原文！", "text": "模型文本"}, "原文！"),
    ({"text_raw": "原文？", "text": "模型文本"}, "原文？"),
    ({"raw_text": "", "text": "原始评论！"}, "原始评论！"),
    ({}, ""),
])
def test_original_comment_text_priority(record, expected):
    assert original_comment_text(record) == expected
