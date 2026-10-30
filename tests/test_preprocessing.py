"""Data semantics, leakage prevention, and training/inference artifact contracts."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from src.preprocessing import TextPreprocessor, Vocab, build_vocab, load_encoded_split, load_preprocessing_bundle, prepare_comments, prepare_dataset
from src.preprocessing.clean import clean_text, deduplicate, remove_html, remove_url
from src.preprocessing.dataset import Record, detect_text_column, load_records, save_records
from src.preprocessing.split import split_dataset, validate_split_integrity
from src.preprocessing.stats import dataset_stats, length_stats
from src.preprocessing.tokenize import encode_ids, load_stopwords, tokenize


def write_csv(path, rows, fields=("comment_id", "video_id", "text", "label")):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def fixture_rows(count=100, group_size=10):
    return [{"comment_id": f"c{index}", "video_id": f"v{index // group_size}",
             "text": f"shared tutorial {'great' if index % 2 else 'bad'} privateword{index}",
             "label": "positive" if index % 2 else "negative"} for index in range(count)]


@pytest.fixture
def english_processor():
    return TextPreprocessor.from_resources(lang="en", max_len=12)


def test_cleaning_preserves_opinion_after_url_and_negation():
    text = clean_text('<p>没有逻辑</p><script>恶意脚本</script> https://b23.tv/abc，真的不好看 ＵＰ主')
    assert "没有逻辑" in text and "真的不好看" in text
    assert "https" not in text and "恶意脚本" not in text and "up主" in text
    assert "差评" in remove_url("www.example.com，差评")
    assert "example.com" not in remove_url("example.com")
    assert "&" in remove_html("<p>A&amp;B</p>")


def test_emoji_and_unknown_emoji_survive_as_tokens():
    cleaned = clean_text("喜欢[doge]😂🫠！？")
    assert all(token in cleaned for token in ["表情狗头", "表情笑哭", "表情未知", "标点感叹", "标点疑问"])
    assert clean_text(None) == ""
    assert deduplicate([" 赞 ", "赞", "", None, "差"]) == ["赞", "差"]


@pytest.mark.parametrize("text,expected", [
    ("isn't good", "is not good"), ("don't like it", "do not like it"),
    ("can't recommend", "can not recommend"), ("won’t work", "will not work"),
    ("couldn’t agree", "could not agree"), ("cannot play", "can not play"),
    ("ISN'T bad", "is not bad"),
])
def test_english_contractions_preserve_negation(text, expected):
    assert clean_text(text) == expected
    assert tokenize(text, lang="en", stopwords={"not"}) == expected.split()
    assert "not" in TextPreprocessor.from_resources(lang="en").transform(text)[1]


def test_html_decodes_once_and_unclosed_tags_preserve_opinion():
    assert "不好看" in remove_html("<script>这个视频不好看")
    assert "不喜欢" in remove_html("<style>不喜欢这个片段")
    assert "不好看" in remove_html("&lt;script&gt;这个视频不好看")
    assert "隐藏" not in remove_html("<SCRIPT>隐藏</SCRIPT>很好")
    assert "隐藏" not in remove_html("<style>隐藏</style>很好")
    assert remove_html("A&amp;lt;B") == "A&lt;B"
    assert clean_text("ISN'T good", lower=False) == "IS NOT good"


def test_separator_and_unmapped_symbols_are_retained_as_tokens():
    assert clean_text("⭐ ⭕ ©", emoji_map={}).split() == ["表情未知"] * 3
    assert "标点分隔" in tokenize(clean_text("不喜欢，但是结局很好；值得看"))


def test_tokenizer_protects_negation_and_keeps_single_character_sentiment(tmp_path):
    path = tmp_path / "stopwords.txt"
    path.write_text("# 注释\n的\n没有\n但是\n很\n", encoding="utf-8")
    stops = load_stopwords(path)
    assert "的" in stops and not {"没有", "但是", "很"} & stops
    words = tokenize("没有 但是 的 赞 差 三连 表情狗头", stopwords={"没有", "但是", "的"})
    assert {"没有", "但是", "赞", "差", "三连", "表情狗头"} <= set(words)
    assert "的" not in words
    assert tokenize("NOT good, BUT useful!", lang="en", stopwords={"not", "but"}) == ["not", "good", "but", "useful"]
    assert "没有" not in load_stopwords(tmp_path / "missing.txt")


def test_vocabulary_frequency_ties_special_tokens_and_encoding(tmp_path):
    vocab = build_vocab([["乙", "甲", "乙", "<pad>"], ["甲", "乙", "甲", "稀有"]])
    assert vocab.word2id == {"<pad>": 0, "<unk>": 1, "乙": 2, "甲": 3}
    assert vocab.freqs["稀有"] == 1 and "稀有" not in vocab
    assert encode_ids(["甲", "未知"], vocab, 4) == [3, 1, 0, 0]
    assert encode_ids(["乙"] * 8, vocab, 2) == [2, 2]
    assert Vocab.load(vocab.save(tmp_path / "vocab.json")).word2id == vocab.word2id
    assert len(build_vocab([["a"] * 3, ["b"] * 3], max_size=1)) == 3


@pytest.mark.parametrize("kwargs", [{"min_freq": 2}, {"min_freq": True}, {"max_size": -1}])
def test_bad_vocab_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        build_vocab([["a"] * 3], **kwargs)


@pytest.mark.parametrize("length", [0, -1, True])
def test_bad_sequence_length_rejected(length):
    with pytest.raises(ValueError):
        encode_ids(["a"], {"<pad>": 0, "<unk>": 1}, length)


def test_csv_metadata_and_labels_roundtrip(tmp_path):
    source = write_csv(tmp_path / "raw.csv", [
        {"comment_id": "12345678901234567890", "video_id": "BV001", "text": "很好\n继续更新", "label": " 正面 "},
        {"comment_id": "2", "video_id": "BV002", "text": "", "label": "negative"},
    ])
    records = load_records(source, keep_empty=True)
    assert records[0].label == "positive" and records[0].meta["input_row"] == 2
    assert records[0].meta["comment_id"] == "12345678901234567890"
    assert len(records) == 2 and len(load_records(source)) == 1
    loaded = load_records(save_records(records, tmp_path / "saved.csv"), keep_empty=True)
    assert loaded[0].text == records[0].text
    assert loaded[0].meta == {key: str(value) if value is not None else "" for key, value in records[0].meta.items()}


def test_csv_utf16_and_ambiguous_encoding(tmp_path):
    source = tmp_path / "encoded.csv"
    source.write_text("text,label\n不好看,negative\n", encoding="utf-16")
    assert load_records(source)[0].text == "不好看"
    source.write_bytes("text,label\n不好看,negative\n".encode("utf-16-le"))
    with pytest.raises(ValueError, match="UTF-16"):
        load_records(source)


def test_malformed_csv_rows_are_audited_without_losing_good_rows(tmp_path, english_processor):
    source = tmp_path / "bad_row.csv"
    source.write_text("text,label\nuseful tutorial,positive\npoor tutorial,negative,extra\n", encoding="utf-8")
    result = prepare_comments(source, output_root=tmp_path / "result", processor=english_processor)
    assert result["stats"]["total"] == 1
    assert result["stats"]["exclusion_reasons"] == {"malformed_column_count": 1}
    excluded = load_records(tmp_path / "result/data/processed/comments_excluded.csv")[0]
    assert excluded.meta["input_row"] == "3" and "extra" in excluded.meta["extra_columns"]


def test_text_column_selection_never_guesses_id(tmp_path):
    assert detect_text_column(["id", "评论"]) == "评论"
    with pytest.raises(ValueError, match="文本列"):
        detect_text_column(["id", "title"])
    source = tmp_path / "chosen.csv"
    source.write_text("id,title,label\n1,好看,positive\n", encoding="utf-8")
    assert load_records(source, text_column="title")[0].text == "好看"
    source.write_text("text,Text\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_records(source)


def test_xlsx_sheet_selection_and_invalid_cells(tmp_path):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "评论"
    sheet.append(["comment_id", "text_raw", "label"])
    sheet.append(["001", "不好看", 0])
    sheet.append(["002", 123, 1])
    path = tmp_path / "comments.xlsx"
    workbook.save(path)
    records = load_records(path, sheet_name="评论", keep_empty=True)
    assert records[0].label == "negative" and records[0].meta["comment_id"] == "001"
    assert records[1].meta["invalid_text_type"] == "int"
    with pytest.raises(ValueError, match="工作表"):
        load_records(path, sheet_name="不存在")
    path.write_bytes(b"not an xlsx")
    with pytest.raises(ValueError, match="XLSX"):
        load_records(path)


def test_statistics_handle_empty_truncation_and_oov():
    assert length_stats([])["mean"] == 0
    assert length_stats([["x"] * n for n in range(5)])["p90"] == pytest.approx(3.6)
    vocab = build_vocab([["known"] * 3])
    stats = dataset_stats([["known", "new"], ["known"]], ["positive", "negative"], vocab, max_len=1, out_path=None)
    assert stats["truncated_ratio"] == .5 and stats["oov_ratio"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="标签数量"):
        dataset_stats([["a"]], [], out_path=None)


def test_stratified_split_is_repeatable_and_balanced():
    records = [Record(f"comment {i}", "positive" if i % 2 else "negative") for i in range(100)]
    first = split_dataset(records)
    assert {key: len(value) for key, value in first.items()} == {"train": 70, "val": 10, "test": 20}
    assert first == split_dataset(records)
    assert first != split_dataset(records, seed=99)
    for items in first.values():
        counts = Counter(record.label for record in items)
        assert counts["positive"] == counts["negative"]


def test_group_split_keeps_videos_out_of_other_splits():
    records = [Record(row["text"], row["label"], meta={"video_id": row["video_id"], "comment_id": row["comment_id"]}) for row in fixture_rows()]
    splits = split_dataset(records)
    validate_split_integrity(splits, group_key="video_id")
    assert {key: len(value) for key, value in splits.items()} == {"train": 70, "val": 10, "test": 20}
    records[0].meta.pop("video_id")
    with pytest.raises(ValueError, match="部分缺失"):
        split_dataset(records)


@pytest.mark.parametrize("ratios", [(1, 1, 1), (-.1, .3, .8), (float("nan"), .1, .9)])
def test_invalid_ratios_rejected(ratios):
    with pytest.raises(ValueError):
        split_dataset([], ratios)


def test_leakage_validation_rejects_cross_split_ids():
    splits = {"train": [Record("A", "positive", meta={"comment_id": "same"})],
              "test": [Record("B", "negative", meta={"comment_id": "same"})]}
    with pytest.raises(ValueError, match="泄漏"):
        validate_split_integrity(splits)


def test_pipeline_outputs_model_inputs_and_shared_inference(tmp_path, english_processor):
    source = write_csv(tmp_path / "source.csv", fixture_rows())
    root = tmp_path / "result"
    result = prepare_dataset(source, output_root=root, processor=english_processor, allow_small_dataset=True)
    assert result["stats"]["total"] == 100 and not result["stats"]["course_requirements_met"]
    assert result["manifest"]["vocab_fit_split"] == "train"
    processor = TextPreprocessor.load(root / "data/processed/preprocessor.json")
    vocab = Vocab.load(root / "data/processed/vocab.json")
    with pytest.warns(UserWarning, match="演示"):
        bundled_processor, bundled_vocab, _ = load_preprocessing_bundle(root / "data/processed")
    assert bundled_processor.encode("new tutorial", bundled_vocab) == processor.encode("new tutorial", vocab)
    test_records = load_records(root / "data/splits/test.csv")
    arrays = load_encoded_split(root / "data/splits/test.npz")
    assert arrays["input_ids"].shape == arrays["attention_mask"].shape == (20, 12)
    assert arrays["input_ids"].dtype == np.int64
    assert set(arrays["labels"].tolist()) == {0, 1}
    for idx, record in enumerate(test_records):
        encoded = processor.encode(record.meta["text_raw"], vocab)
        assert encoded["input_ids"] == arrays["input_ids"][idx].tolist()
        assert sum(encoded["attention_mask"]) == arrays["lengths"][idx]
        unique_word = next(word for word in record.tokens.split() if word.startswith("privateword"))
        assert unique_word not in vocab.freqs  # not even counted from validation/test
    assert all((arrays["input_ids"][arrays["attention_mask"] == 0] == 0))
    assert json.loads((root / "data/processed/label_map.json").read_text()) == {"negative": 0, "positive": 1}
    with pytest.raises(FileExistsError):
        prepare_dataset(source, output_root=root, processor=processor, allow_small_dataset=True)
    second = prepare_dataset(source, output_root=tmp_path / "again", processor=processor, allow_small_dataset=True)
    assert second["manifest"]["dataset_version"] == result["manifest"]["dataset_version"]
    manifest, stats = result["manifest"], result["stats"]
    assert manifest["demo_mode"] and not manifest["course_requirements_met"]
    assert manifest["course_check_issues"] == stats["course_check_issues"]
    assert manifest["input_rows"] == manifest["accepted_rows"] == 100
    assert manifest["excluded_rows"] == 0
    assert manifest["split_counts"] == {"train": 70, "val": 10, "test": 20}
    assert manifest["task"]["num_classes"] == 2 and manifest["task"]["label_map"] == {"negative": 0, "positive": 1}
    assert stats["oov_scope"] == "train"
    assert stats["oov_ratio"] == stats["splits"]["train"]["oov_ratio"]
    assert stats["test_oov_ratio"] == stats["splits"]["test"]["oov_ratio"]
    (root / "data/processed/vocab.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"):
        load_preprocessing_bundle(root / "data/processed")


def test_pipeline_audits_conflicts_duplicates_and_missing_data(tmp_path, english_processor):
    rows = fixture_rows()
    rows += [dict(rows[1], comment_id="duplicate"),
             {"comment_id": "empty", "video_id": "v0", "text": "", "label": "positive"},
             {"comment_id": "neutral", "video_id": "v0", "text": "what is this", "label": "neutral"},
             {"comment_id": "a", "video_id": "v0", "text": "conflicting opinion", "label": "positive"},
             {"comment_id": "b", "video_id": "v0", "text": "conflicting opinion", "label": "negative"}]
    source = write_csv(tmp_path / "source.csv", rows)
    result = prepare_dataset(source, output_root=tmp_path / "out", processor=english_processor, allow_small_dataset=True)
    assert result["stats"]["total"] == 100
    assert result["stats"]["exclusion_reasons"] == {"duplicate_text": 1, "empty_after_cleaning": 1, "neutral": 1, "conflicting_text_labels": 2}


def test_formal_gate_does_not_publish_an_undersized_dataset(tmp_path, english_processor):
    source = write_csv(tmp_path / "small.csv", fixture_rows())
    output = tmp_path / "unpublished"
    with pytest.raises(ValueError, match="8000"):
        prepare_dataset(source, output_root=output, processor=english_processor)
    assert not output.exists()


def test_formal_12000_row_dataset_satisfies_course_sizes(tmp_path, english_processor):
    # Synthetic data validates pipeline invariants, not model accuracy or real acquisition.
    source = write_csv(tmp_path / "synthetic.csv", fixture_rows(12000, group_size=200))
    output = tmp_path / "formal"
    result = prepare_dataset(source, output_root=output, processor=english_processor)
    assert result["stats"]["course_requirements_met"]
    assert {name: stats["total"] for name, stats in result["stats"]["splits"].items()} == {"train": 8400, "val": 1200, "test": 2400}
    assert result["manifest"]["course_requirements_met"] and not result["manifest"]["demo_mode"]
    demo = prepare_dataset(source, output_root=tmp_path / "demo", processor=english_processor, allow_small_dataset=True)
    assert demo["manifest"]["course_requirements_met"] and demo["manifest"]["demo_mode"]
    assert demo["manifest"]["dataset_version"] != result["manifest"]["dataset_version"]


def test_demo_cannot_publish_empty_validation_or_test_split(tmp_path, english_processor):
    source = write_csv(tmp_path / "too_small.csv", fixture_rows(2, group_size=1))
    output = tmp_path / "unpublished"
    with pytest.raises(ValueError, match="为空"):
        prepare_dataset(source, output_root=output, processor=english_processor, allow_small_dataset=True)
    assert not output.exists()


def test_cli_failure_is_nonzero_and_demo_command_runs(tmp_path, capsys):
    from src.preprocessing.__main__ import main
    source = Path(__file__).resolve().parents[1] / "data/sample/bilibili_demo.csv"
    assert main(["--input", str(source), "--output-root", str(tmp_path / "formal")]) == 1
    assert "数据处理失败" in capsys.readouterr().err
    assert main(["--input", str(source), "--output-root", str(tmp_path / "demo"), "--allow-small-dataset"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["accepted"] == 24 and report["excluded"] == 3


@pytest.mark.parametrize("overrides,seed,min_freq", [([], 17, 4), (["--seed", "99", "--min-freq", "5"], 99, 5)])
def test_cli_environment_defaults_and_explicit_overrides(tmp_path, overrides, seed, min_freq):
    source = write_csv(tmp_path / "source.csv", fixture_rows())
    output = tmp_path / "result"
    env = dict(os.environ, SEED="17", MIN_FREQ="4", PYTHONIOENCODING="utf-8")
    command = [sys.executable, "-m", "src.preprocessing", "--input", str(source), "--mode", "train",
               "--output-root", str(output), "--allow-small-dataset", "--lang", "en", *overrides]
    run = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    manifest = json.loads((output / "data/processed/manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == seed and manifest["min_freq"] == min_freq


def test_unlabelled_crawler_csv_outputs_pending_comments_not_training_data(tmp_path):
    source = Path(__file__).resolve().parents[1] / "data/sample/crawler_comments_sample.csv"
    result = prepare_comments(source, output_root=tmp_path)
    assert result["stats"]["total"] == 6 and result["stats"]["excluded_rows"] == 2
    assert result["stats"]["labels"] == {} and not result["stats"]["vocab_fitted"]
    assert result["stats"]["exclusion_reasons"] == {"duplicate_text": 1, "empty_after_cleaning": 1}
    records = load_records(tmp_path / "data/processed/comments_cleaned.csv")
    assert all(record.label is None for record in records)
    assert "raw003" in {record.meta["comment_id"] for record in records}  # question is not discarded
    assert "没有说明白" in next(record.text for record in records if record.meta["comment_id"] == "raw002")
    assert all(record.tokens and record.meta["text_raw"] for record in records)
    assert not (tmp_path / "data/splits").exists()
    assert not (tmp_path / "data/processed/vocab.json").exists()
    assert not (tmp_path / "data/processed/preprocessor.json").exists()
    assert (tmp_path / "data/processed/comments_preprocessor.json").is_file()
    with pytest.raises(FileExistsError):
        prepare_comments(source, output_root=tmp_path)


def test_auto_mode_accepts_unlabelled_csv_without_course_size_gate(tmp_path, capsys):
    from src.preprocessing.__main__ import main
    source = Path(__file__).resolve().parents[1] / "data/sample/crawler_comments_sample.csv"
    assert main(["--input", str(source), "--output-root", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "clean" and summary["accepted"] == 6
    assert summary["next_step"] == "sentiment_prediction" and not summary["vocab_fitted"]


def test_mixed_label_file_needs_explicit_mode(tmp_path, capsys):
    from src.preprocessing.__main__ import main
    source = write_csv(tmp_path / "mixed.csv", [
        {"comment_id": "one", "video_id": "v1", "text": "great tutorial", "label": "positive"},
        {"comment_id": "two", "video_id": "v2", "text": "how to use this", "label": ""},
    ])
    assert main(["--input", str(source), "--output-root", str(tmp_path / "auto")]) == 1
    assert "混有" in capsys.readouterr().err
    assert main(["--input", str(source), "--output-root", str(tmp_path / "clean"), "--mode", "clean"]) == 0
    rows = load_records(tmp_path / "clean/data/processed/comments_cleaned.csv")
    assert len(rows) == 2 and all(record.label is None for record in rows)
    assert next(record.meta["provided_label"] for record in rows if record.meta["comment_id"] == "one") == "positive"


def test_bilibili_crawler_schema_preserves_ids_and_thread_metadata(tmp_path, english_processor):
    source = tmp_path / "crawler.csv"
    source.write_text("bvid,rpid,level,parent_rpid,text,ctime_utc,collected_at_utc\n"
                      "BVexample,12345678901234567890,reply,999,a useful tutorial,2026-10-01T00:00:00Z,2026-10-16T00:00:00Z\n",
                      encoding="utf-8-sig")
    record = load_records(source)[0]
    assert record.meta["comment_id"] == "12345678901234567890"
    assert record.meta["video_id"] == "BVexample"
    assert record.meta["posted_at"] == record.meta["ctime_utc"]
    assert record.meta["collected_at"] == record.meta["collected_at_utc"]
    prepare_comments(source, output_root=tmp_path / "output", processor=english_processor)
    processed = load_records(tmp_path / "output/data/processed/comments_cleaned.csv")[0]
    assert processed.meta["comment_id"] == processed.meta["rpid"] == "12345678901234567890"
    assert processed.meta["level"] == "reply" and processed.meta["parent_rpid"] == "999"
