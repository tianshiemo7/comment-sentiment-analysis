"""评估指标与结果落盘测试（sklearn 缺失时自动跳过）。"""

from __future__ import annotations

import csv
import json

import pytest

pytest.importorskip("sklearn")

from src.evaluation.metrics import (  # noqa: E402
    compute_metrics,
    confusion_matrix,
    error_analysis,
    ids_to_labels,
    labels_to_ids,
)
from src.evaluation.report import (  # noqa: E402
    compare_models,
    save_error_cases,
    save_metrics_json,
    save_metrics_table,
)

Y_TRUE = [0, 0, 0, 1, 1, 1, 1, 0]
Y_PRED = [0, 0, 1, 1, 1, 0, 1, 0]


def test_compute_metrics_known_values():
    metrics = compute_metrics(Y_TRUE, Y_PRED)
    assert metrics["accuracy"] == pytest.approx(6 / 8)
    assert metrics["precision"] == pytest.approx(3 / 4)
    assert metrics["recall"] == pytest.approx(3 / 4)
    assert metrics["f1"] == pytest.approx(3 / 4)
    assert {"macro_precision", "macro_recall", "macro_f1"} <= set(metrics)


def test_compute_metrics_rejects_invalid_input():
    with pytest.raises(ValueError):
        compute_metrics([0, 1], [0])
    with pytest.raises(ValueError):
        compute_metrics([], [])
    with pytest.raises(ValueError):
        compute_metrics(Y_TRUE, Y_PRED, average="micro")


def test_confusion_matrix_order_and_breakdown():
    matrix = confusion_matrix(Y_TRUE, Y_PRED)
    assert matrix["matrix"] == [[3, 1], [1, 3]]
    assert (matrix["tn"], matrix["fp"], matrix["fn"], matrix["tp"]) == (3, 1, 1, 3)
    assert matrix["labels"] == ("negative", "positive")


def test_error_analysis_sorted_by_confidence():
    probs = [0.1, 0.2, 0.9, 0.8, 0.7, 0.4, 0.6, 0.1]
    cases = error_analysis([f"文本{i}" for i in range(len(Y_TRUE))], Y_TRUE, Y_PRED, probs)
    assert len(cases) == 2
    assert cases[0]["confidence"] >= cases[1]["confidence"]
    assert set(cases[0]) == {"text", "true_label", "pred_label", "confidence"}
    assert cases[0]["pred_label"] in {"positive", "negative"}


def test_error_analysis_top_k_and_length_validation():
    cases = error_analysis(["a", "b", "c", "d", "e", "f", "g", "h"], Y_TRUE, Y_PRED, top_k=1)
    assert len(cases) == 1
    with pytest.raises(ValueError):
        error_analysis(["a"], Y_TRUE, Y_PRED)
    with pytest.raises(ValueError):
        error_analysis(["a"] * 8, Y_TRUE, Y_PRED, [0.5] * 7)


def test_label_id_conversion():
    assert labels_to_ids(["negative", "positive"]) == [0, 1]
    assert ids_to_labels([0, 1]) == ["negative", "positive"]
    with pytest.raises(ValueError):
        labels_to_ids(["neutral"])


def test_save_metrics_json_contract(tmp_path):
    metrics = compute_metrics(Y_TRUE, Y_PRED)
    path = save_metrics_json(metrics, "textcnn", tmp_path)
    assert path.name == "metrics_textcnn.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["model"] == "textcnn"
    assert payload["metrics"]["accuracy"] == pytest.approx(metrics["accuracy"])
    assert payload["updated_at"]


def test_compare_models_marks_best_metric():
    rows = compare_models(
        {
            "textcnn": {"accuracy": 0.80, "precision": 0.75, "recall": 0.70, "f1": 0.72},
            "bilstm_attention": {"accuracy": 0.90, "precision": 0.70, "recall": 0.95, "f1": 0.80},
        }
    )
    assert [row["model"] for row in rows] == ["bilstm_attention", "textcnn"]
    best = rows[0]
    assert "Accuracy" in best["notes"] and "F1" in best["notes"]
    assert "Recall" in best["notes"]
    assert "Precision" in rows[1]["notes"]


def test_save_metrics_table_writes_utf8_bom_and_fields(tmp_path):
    rows = compare_models({"textcnn": {"accuracy": 0.8, "precision": 0.7, "recall": 0.6, "f1": 0.65}})
    path = save_metrics_table(rows, tmp_path / "model_comparison.csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["model", "accuracy", "precision", "recall", "f1", "notes"]
        row = next(reader)
    assert row["model"] == "textcnn"
    assert row["accuracy"] == "0.8000"


def test_save_error_cases_csv(tmp_path):
    cases = [
        {"text": "太差了", "true_label": "negative", "pred_label": "positive", "confidence": 0.9}
    ]
    path = save_error_cases(cases, "textcnn", tmp_path)
    assert path.name == "error_cases_textcnn.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["text"] == "太差了"
    assert row["true_label"] == "negative"
