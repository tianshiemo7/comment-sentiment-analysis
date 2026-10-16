"""接口冒烟测试：覆盖根 README「前后端接口契约」一节锁定的接口契约。

设计要点：

- 只依赖 ``src/inference`` 的 HTTP 层，因此**无需 torch**（服务当前是规则占位实现）；
- 断言严格按契约字段名与取值，接口被改名/改结构时立即可见；
- 额外断言业务路由集合（原 4 个 + 批量导出下载），防止后期无意新增路由破坏契约；
- 断言「明显好评 → positive、明显差评 → negative」的方向语义，确认占位预测器
  不是随机/伪随机的（旧版 md5 占位会把明显好评判成 negative）。
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from src.inference.app import SERVICE_NAME, VERSION, app

client = TestClient(app)

LABELS = {"positive", "negative"}

SAMPLE_TEXTS = [
    "这个商品质量很好，物流也快，非常满意。",
    "质量太差了，用了一天就坏了，非常失望。",
    "东西一般般，没有想象中那么好。",
]

#: 方向断言用例：文本 → 期望标签（只断言方向，不断言具体概率数值）
DIRECTION_CASES = [
    ("这个商品质量很好，物流也快，非常满意。", "positive"),
    ("质量太差了，用了一天就坏了，非常失望。", "negative"),
    ("发货慢，客服也不理人，差评。", "negative"),
    ("客服态度特别好，问题很快就解决了。", "positive"),
    ("不好用，非常不满意。", "negative"),  # 否定词翻转生效
]


# --------------------------------------------------------------------------- #
# GET /health
# --------------------------------------------------------------------------- #
def test_health_returns_contract_fields():
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"status", "service", "version"}
    assert body["status"] == "ok"
    assert body["service"] == SERVICE_NAME
    assert body["version"] == VERSION


# --------------------------------------------------------------------------- #
# POST /api/predict
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", SAMPLE_TEXTS)
def test_predict_single_text_contract(text):
    response = client.post("/api/predict", json={"text": text})
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"text", "label", "confidence", "probs"}
    assert body["text"] == text
    assert body["label"] in LABELS
    assert 0.0 <= body["confidence"] <= 1.0

    probs = body["probs"]
    assert set(probs) == {"positive", "negative"}
    assert probs["positive"] + probs["negative"] == pytest.approx(1.0, abs=1e-6)
    assert body["confidence"] == pytest.approx(max(probs.values()), abs=1e-6)

    expected_label = "positive" if probs["positive"] >= probs["negative"] else "negative"
    assert body["label"] == expected_label


def test_predict_is_deterministic_for_same_text():
    text = "服务态度特别好，下次还会再来。"
    first = client.post("/api/predict", json={"text": text}).json()
    second = client.post("/api/predict", json={"text": text}).json()
    assert first == second


@pytest.mark.parametrize("text,expected_label", DIRECTION_CASES)
def test_predict_direction_matches_intuition(text, expected_label):
    """占位预测器必须方向正确（旧版 md5 伪随机会让第一条判成 negative）。"""
    body = client.post("/api/predict", json={"text": text}).json()
    assert body["label"] == expected_label
    probs = body["probs"]
    assert (probs["positive"] > probs["negative"]) == (expected_label == "positive")
    assert 0.0 < probs["positive"] < 1.0
    assert 0.0 < probs["negative"] < 1.0


def test_predict_rejects_empty_text():
    assert client.post("/api/predict", json={"text": ""}).status_code == 422
    assert client.post("/api/predict", json={"text": "   "}).status_code == 422
    # 缺少 text 字段同样应被拒绝
    assert client.post("/api/predict", json={}).status_code == 422


# --------------------------------------------------------------------------- #
# POST /api/predict/batch
# --------------------------------------------------------------------------- #
def _batch_response(payload, filename, content_type):
    return client.post(
        "/api/predict/batch",
        files={"file": (filename, payload, content_type)},
    )


def test_predict_batch_csv_contract():
    csv_bytes = "id,text\n1,快递很快，包装完好。\n2,客服态度很差，不推荐。\n3,性价比还可以。\n".encode(
        "utf-8-sig"
    )
    response = _batch_response(csv_bytes, "comments.csv", "text/csv")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"total", "results", "export_url"}
    assert body["total"] == 3
    # 改进 B：结果落盘并返回可下载地址（不再是 null 占位）
    assert body["export_url"] is not None
    assert body["export_url"].startswith("/api/predict/batch/download/")
    file_id = body["export_url"].rsplit("/", 1)[-1]
    assert file_id.startswith("batch_prediction_") and file_id.endswith(".csv")

    texts = ["快递很快，包装完好。", "客服态度很差，不推荐。", "性价比还可以。"]
    assert [item["index"] for item in body["results"]] == [0, 1, 2]
    for item, expected_text in zip(body["results"], texts):
        assert set(item) == {"index", "text", "label", "confidence"}
        assert item["text"] == expected_text
        assert item["label"] in LABELS
        assert 0.0 <= item["confidence"] <= 1.0


def test_predict_batch_xlsx_contract():
    openpyxl = pytest.importorskip("openpyxl", reason="解析 xlsx 需要 openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["text"])
    sheet.append(["屏幕显示效果很棒。"])
    sheet.append(["电池续航太短了。"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    response = _batch_response(
        buffer.getvalue(),
        "comments.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert response.status_code == 200

    body = response.json()
    assert body["total"] == 2
    assert [item["text"] for item in body["results"]] == ["屏幕显示效果很棒。", "电池续航太短了。"]


def test_predict_batch_rejects_unsupported_file_type():
    response = _batch_response(b"not a table", "comments.pdf", "application/pdf")
    assert response.status_code == 400
    assert "不支持" in response.json()["detail"]


def test_predict_batch_rejects_empty_file():
    assert _batch_response(b"", "comments.csv", "text/csv").status_code == 400


def test_predict_batch_missing_file_field_returns_422():
    assert client.post("/api/predict/batch").status_code == 422


# --------------------------------------------------------------------------- #
# GET /api/models
# --------------------------------------------------------------------------- #
def test_models_contract():
    response = client.get("/api/models")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"models"}
    assert isinstance(body["models"], list) and body["models"]

    names = [item["name"] for item in body["models"]]
    assert "textcnn" in names and "bilstm_attention" in names

    for item in body["models"]:
        assert set(item) == {"name", "type", "metrics", "ready"}
        assert isinstance(item["ready"], bool)
        assert set(item["metrics"]) == {"accuracy", "precision", "recall", "f1"}
        for value in item["metrics"].values():
            assert isinstance(value, (int, float))
            assert 0.0 <= float(value) <= 1.0


# --------------------------------------------------------------------------- #
# 契约边界：业务路由集合（原 4 个 + 批量导出下载）
# --------------------------------------------------------------------------- #
def test_business_routes_exposed():
    documented = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    business = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
        if route.path not in documented
    }
    assert business == {
        ("GET", "/health"),
        ("POST", "/api/predict"),
        ("POST", "/api/predict/batch"),
        ("GET", "/api/predict/batch/download/{file_id}"),
        ("GET", "/api/models"),
    }


# --------------------------------------------------------------------------- #
# OpenAPI 文档可用性（前端联调依赖 schema）
# --------------------------------------------------------------------------- #
def test_openapi_schema_lists_contract_paths():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = json.loads(response.content)["paths"]
    assert {
        "/health",
        "/api/predict",
        "/api/predict/batch",
        "/api/predict/batch/download/{file_id}",
        "/api/models",
    } <= set(paths)
