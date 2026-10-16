"""`/api/solo-comments` 系列接口契约测试（对照 `docs/API契约.md` 4.5.2）。

覆盖：保存（字段与校验 400）、列表（倒序 / 分页）、删除单条与批量删除（404 / 400）、
落盘位置（``data/uploads/solo_comments.json``）以及左栏「单独评论」段。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.artifacts import service as artifacts_service
from src.artifacts.config import ArtifactsConfig
from src.artifacts.schemas import SOLO_ID_RE, METHOD_RULE_PLACEHOLDER
from src.artifacts.service import ArtifactService
from src.inference.app import app

#: 单条评论对象字段（契约 4.5.2，一个不多一个不少）
SOLO_FIELDS = {"id", "text", "label", "confidence", "probs", "method", "created_at"}

VALID_PAYLOAD = {
    "text": "这个商品质量很好",
    "label": "positive",
    "confidence": 0.8084,
    "probs": {"positive": 0.8084, "negative": 0.1916},
    "method": METHOD_RULE_PLACEHOLDER,
}


@pytest.fixture
def solo_client(tmp_path):
    """注入临时落盘目录的 TestClient（lifespan 会启动 / 关停流水线线程）。"""
    service = ArtifactService(
        root=tmp_path / "artifacts-solo",
        config=ArtifactsConfig(clean_seconds=0.0, sentiment_seconds=0.0, stats_seconds=0.0),
    )
    artifacts_service.set_service(service)
    with TestClient(app) as client:
        yield client, service


def create(client: TestClient, **overrides) -> dict:
    """保存一条评论并断言 200。"""
    payload = {**VALID_PAYLOAD, **overrides}
    response = client.post("/api/solo-comments", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 保存
# --------------------------------------------------------------------------- #
def test_create_returns_contract_fields(solo_client):
    client, _ = solo_client
    item = create(client)

    assert set(item) == SOLO_FIELDS
    assert SOLO_ID_RE.match(item["id"])
    assert item["text"] == "这个商品质量很好"
    assert item["label"] == "positive"
    assert item["confidence"] == pytest.approx(0.8084)
    assert item["probs"] == {"positive": 0.8084, "negative": 0.1916}
    assert item["method"] == METHOD_RULE_PLACEHOLDER
    assert item["created_at"]


def test_create_accepts_neutral_and_defaults(solo_client):
    """label 枚举包含 neutral；method 缺省时如实记为 rule_placeholder。"""
    client, _ = solo_client
    item = create(client, text="今天下午三点开组会。", label="neutral", confidence=0.5,
                  probs={"positive": 0.5, "negative": 0.5}, method=None)
    assert item["label"] == "neutral"
    assert item["method"] == METHOD_RULE_PLACEHOLDER


def test_create_strips_text(solo_client):
    client, _ = solo_client
    item = create(client, text="  这个商品质量很好  ")
    assert item["text"] == "这个商品质量很好"


@pytest.mark.parametrize("payload", [
    {"text": ""},
    {"text": "   "},
    {"text": None},
    {"text": "好", "label": "very_positive"},
    {"text": "好", "label": None},
    {"text": "好", "label": "positive", "confidence": 1.5},
    {"text": "好", "label": "positive", "confidence": -0.1},
    {"text": "好", "label": "positive", "confidence": "0.5"},
    {"text": "好", "label": "positive", "confidence": 0.5, "probs": {"positive": 2.0}},
    {"text": "好", "label": "positive", "confidence": 0.5, "probs": [0.5, 0.5]},
    {"text": "好", "label": "positive", "confidence": 0.5, "method": 123},
])
def test_create_validation_returns_400(solo_client, payload):
    client, _ = solo_client
    response = client.post("/api/solo-comments", json=payload)
    assert response.status_code == 400, response.text


def test_create_with_blank_method_falls_back_to_placeholder(solo_client):
    """method 缺失 / 空串时如实记为 rule_placeholder（当前唯一预测来源）。"""
    client, _ = solo_client
    item = create(client, method="")
    assert item["method"] == METHOD_RULE_PLACEHOLDER


def test_create_persists_to_solo_comments_file(solo_client):
    client, service = solo_client
    item = create(client)
    path = service.store.solo_path
    assert path.name == "solo_comments.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert [entry["id"] for entry in payload] == [item["id"]]


# --------------------------------------------------------------------------- #
# 列表
# --------------------------------------------------------------------------- #
def test_list_contract_and_pagination(solo_client):
    client, _ = solo_client
    created = [create(client, text=f"第 {index} 条评论，很好用") for index in range(3)]

    body = client.get("/api/solo-comments").json()
    assert set(body) == {"total", "offset", "limit", "items"}
    assert body["total"] == 3
    assert body["limit"] == 50
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert set(item) == SOLO_FIELDS
    assert {item["id"] for item in body["items"]} == {item["id"] for item in created}

    # 按 created_at 倒序：同一秒创建时用 id 兜底，保证「最新在前」稳定
    keys = [(item["created_at"], item["id"]) for item in body["items"]]
    assert keys == sorted(keys, reverse=True)

    page = client.get("/api/solo-comments?offset=1&limit=1").json()
    assert page["total"] == 3
    assert page["offset"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] == body["items"][1]["id"]

    overflow = client.get("/api/solo-comments?offset=99").json()
    assert overflow["items"] == []
    assert overflow["total"] == 3

    assert client.get("/api/solo-comments?limit=501").status_code == 400
    assert client.get("/api/solo-comments?limit=0").status_code == 422
    assert client.get("/api/solo-comments?offset=-1").status_code == 422


def test_list_empty(solo_client):
    client, _ = solo_client
    body = client.get("/api/solo-comments").json()
    assert body == {"total": 0, "offset": 0, "limit": 50, "items": []}


# --------------------------------------------------------------------------- #
# 删除
# --------------------------------------------------------------------------- #
def test_delete_one(solo_client):
    client, _ = solo_client
    first = create(client)
    second = create(client, text="第二条评论，很差劲", label="negative", confidence=0.9,
                    probs={"positive": 0.1, "negative": 0.9})

    response = client.delete(f"/api/solo-comments/{first['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 1}

    body = client.get("/api/solo-comments").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == second["id"]

    # 再删一次 → 404
    assert client.delete(f"/api/solo-comments/{first['id']}").status_code == 404


@pytest.mark.parametrize("comment_id", ["c_20000101_0000_abcdef", "not-an-id", "..%2Fetc"])
def test_delete_missing_or_malformed_is_404(solo_client, comment_id):
    client, _ = solo_client
    assert client.delete(f"/api/solo-comments/{comment_id}").status_code == 404


def test_bulk_delete(solo_client):
    client, _ = solo_client
    items = [create(client, text=f"批量删除测试 {index}") for index in range(3)]
    targets = [items[0]["id"], items[2]["id"]]

    response = client.delete("/api/solo-comments", params={"ids": ",".join(targets)})
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 2}

    body = client.get("/api/solo-comments").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == items[1]["id"]

    # 幂等：再删同样两个（都不存在）→ 仍 200，只是删到 0 条
    response = client.delete("/api/solo-comments", params={"ids": ",".join(targets)})
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 0}


def test_bulk_delete_is_idempotent(solo_client):
    """批量删除幂等：缺省 / 空 / 不存在的 id 都返回 200 + 真实删除数（可为 0）。"""
    client, _ = solo_client

    # ids 缺省
    response = client.delete("/api/solo-comments")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 0}

    # ids 为空串 / 纯空白（前端「清空全部」在列表为空时就是这个请求）
    for raw in ("", "   ", ",", " , "):
        response = client.delete("/api/solo-comments", params={"ids": raw})
        assert response.status_code == 200, response.text
        assert response.json() == {"deleted": True, "deleted_count": 0}

    # 合法但不存在 → 200 + 0
    response = client.delete("/api/solo-comments", params={"ids": "c_20000101_0000_abcdef"})
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 0}

    # 部分存在 → 200 + 只统计真实删除条数
    existing = create(client)
    response = client.delete(
        "/api/solo-comments", params={"ids": f"c_20000101_0000_abcdef,{existing['id']}"}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "deleted_count": 1}


def test_bulk_delete_rejects_malformed_ids(solo_client):
    """形态非法的 id 是客户端 bug，仍显式 400。"""
    client, _ = solo_client
    assert client.delete("/api/solo-comments", params={"ids": "not-an-id"}).status_code == 400
    assert client.delete(
        "/api/solo-comments", params={"ids": "c_20000101_0000_abcdef,bad"}
    ).status_code == 400
    assert client.delete("/api/solo-comments", params={"ids": "..%2Fetc"}).status_code == 400


# --------------------------------------------------------------------------- #
# 与左栏三段的关系
# --------------------------------------------------------------------------- #
def test_solo_group_in_artifacts_list(solo_client):
    client, _ = solo_client
    first = create(client)
    second = create(client, text="第二条")

    groups = client.get("/api/artifacts").json()["groups"]
    solo = next(group for group in groups if group["key"] == "solo")
    assert solo["label"] == "单独评论"
    assert {item["id"] for item in solo["items"]} == {first["id"], second["id"]}
    for item in solo["items"]:
        assert set(item) == SOLO_FIELDS
