"""/api/videos 情感阶段接入真实模型的测试（真实模型优先 + 规则回退）。

覆盖 T2 的三条验收线：

1. 注入模型（FakePredictor）后，``pipeline._predict_cleaned_records`` 与
   ``placeholder.build_detail(comments=...)`` 必须使用**模型结果**，而不是规则结果；
2. 单条预测异常退化为中性 0.5/0.5，不拖垮整份 detail；
3. 仓库真实检查点（``models/checkpoints/textcnn``）真实加载后，
   逐条预测结构正确（只验证加载/前向/契约，不写死领域准确率）。

占位模式（``ACTIVE_MODEL=__pytest_placeholder__``，见 tests/conftest.py）下
``get_predictor()`` 返回 ``None``，走规则占位；需要真实模型的用例在用例内部显式注入，
保证既有 ``tests/test_videos_*.py`` 的确定性断言不受影响。
"""

from __future__ import annotations

import pytest

from src.inference import service
from src.videos import pipeline, placeholder
from src.videos.config import PipelineConfig
from src.videos.store import VideoStore, now_iso

BVID = "BV1xx411c7mD"

#: 0 秒配置：状态机瞬间跑完（真实采集由注入的假 collector 瞬间返回，不联网）
REAL_INSTANT = PipelineConfig(0.0, 0.0, 0.0, 0.0, real_crawl=True)

#: 真实采集设置（离线：collector 注入，不触网）
REAL_SETTINGS = {
    "account": {"mode": "anonymous"},
    "crawl": {"real_crawl": True},
    "pipeline": {"auto_clean": True, "auto_analyze": True},
}

#: 规则预测器会判 negative 的明显差评（用来证明「用的是模型结果，不是规则结果」）
RULE_NEGATIVE_TEXT = "质量太差了，非常失望，不会再买了。"

#: 规则预测器会判 positive 的明显好评
RULE_POSITIVE_TEXT = "讲得太清楚了，非常有帮助，强烈推荐！"

#: `placeholder.build_detail` 的原始产物字段（契约第三节 + placeholder 诚实标注字段；
#: 顶层 pipeline 由 service 层组装，不在此列）——接入真实模型后**不得增删改名**
DETAIL_PAYLOAD_FIELDS = {
    "video",
    "analysis",
    "sentiment",
    "trend",
    "keywords",
    "model",
    "top_comments",
    "error",
    "placeholder",
}

#: 单条预测结果的契约字段（analyze.json / 既有测试锁定的键集合）
PREDICTION_FIELDS = {"text", "label", "confidence", "probs"}


class FakePredictor:
    """按统一接口契约实现的假预测器：固定 label / confidence，可指定文本抛异常。"""

    def __init__(
        self,
        label: str = "positive",
        confidence: float = 0.9,
        model_name: str = "textcnn",
        fail_texts: tuple[str, ...] = (),
    ) -> None:
        self.model_name = model_name
        self.model_type = model_name
        self.labels = ("negative", "positive")
        self.label = label
        self.confidence = confidence
        self.fail_texts = set(fail_texts)
        self.calls: list[str] = []

    def predict(self, text: str) -> dict:
        self.calls.append(text)
        if text in self.fail_texts:
            raise RuntimeError("模拟单条真实模型推理失败")
        other = 1.0 - self.confidence
        probs = (
            {"positive": self.confidence, "negative": other}
            if self.label == "positive"
            else {"positive": other, "negative": self.confidence}
        )
        return {
            "text": text,
            "label": self.label,
            "confidence": self.confidence,
            "probs": probs,
        }


def _inject_model(monkeypatch, fake: FakePredictor) -> FakePredictor:
    """把「当前生效模型」替换成 FakePredictor。

    ``pipeline.service`` 与 ``placeholder.service`` 是同一个 ``src.inference.service``
    模块对象，因此一次 monkeypatch 对两条实现路径同时生效（下方断言防止将来分叉）。
    """
    assert placeholder.service is pipeline.service is service
    monkeypatch.setattr(pipeline.service, "get_predictor", lambda: fake)
    return fake


def _seed_task(store: VideoStore, bvid: str = BVID) -> dict:
    """往存储里塞一条 queued 任务（模拟提交后的状态，run_id = 1）。"""
    task = {
        "bvid": bvid,
        "title": None,
        "owner": None,
        "cover_url": None,
        "status": "queued",
        "progress": 0,
        "message": "排队中…",
        "phases": pipeline.initial_phases(),
        "comment_count": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "error": None,
        "run_id": 1,
        "seq": 1,
    }
    store.upsert_task(task)
    return task


def _raw_records() -> list[dict]:
    """模拟 ``fetch_video_comments`` 的清洗前记录（字段贴近爬虫 CSV）。"""
    return [
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1001",
            "text": RULE_NEGATIVE_TEXT,
            "likes": "88",
            "ctime_utc": "2026-10-15T08:00:00+00:00",
        },
        {
            "bvid": BVID,
            "video_title": "真实视频标题（爬虫返回）",
            "rpid": "1002",
            "text": RULE_POSITIVE_TEXT,
            "likes": "120",
            "ctime_utc": "2026-10-16T08:00:00+00:00",
        },
    ]


# --------------------------------------------------------------------------- #
# 1. 分步流水线：predictions 使用模型结果（而不是规则结果），method 如实标注
# --------------------------------------------------------------------------- #
def test_predict_cleaned_records_uses_real_model(monkeypatch):
    """FakePredictor 固定 positive/0.9：文本即使规则判 negative，也必须输出模型结果。"""
    # 先确认基线：同一文本在规则占位下确实是 negative（证明差异来自模型切换）
    assert service.predict_rule_based(RULE_NEGATIVE_TEXT)["label"] == "negative"
    fake = _inject_model(monkeypatch, FakePredictor(label="positive", confidence=0.9))

    records = [{"text": RULE_NEGATIVE_TEXT}, {"text": RULE_POSITIVE_TEXT}]
    predictions = pipeline._predict_cleaned_records(records)

    assert len(predictions) == 2
    assert [item["label"] for item in predictions] == ["positive", "positive"]
    assert [item["confidence"] for item in predictions] == [0.9, 0.9]
    for prediction in predictions:
        assert set(prediction) == PREDICTION_FIELDS
        assert prediction["probs"] == {"positive": 0.9, "negative": pytest.approx(0.1)}
    # 确实逐条走的是模型，而不是规则预测器
    assert fake.calls == [RULE_NEGATIVE_TEXT, RULE_POSITIVE_TEXT]
    # method 如实反映来源（真实模式为模型名，回退为 rule_placeholder）
    assert pipeline._resolve_prediction_method(fake) == "textcnn"
    assert pipeline._resolve_prediction_method(None) == "rule_placeholder"


def test_predict_cleaned_records_degrades_single_item_on_error(monkeypatch):
    """单条模型推理异常 → 中性 0.5/0.5；其余条不受影响，任务不失败。"""
    fake = FakePredictor(label="positive", confidence=0.9, fail_texts=(RULE_POSITIVE_TEXT,))
    _inject_model(monkeypatch, fake)

    records = [{"text": RULE_NEGATIVE_TEXT}, {"text": RULE_POSITIVE_TEXT}]
    predictions = pipeline._predict_cleaned_records(records)

    assert predictions[0]["label"] == "positive"
    assert predictions[0]["confidence"] == 0.9
    # 失败的单条退化为中性
    assert predictions[1] == {
        "text": RULE_POSITIVE_TEXT,
        "label": "positive",
        "confidence": 0.5,
        "probs": {"positive": 0.5, "negative": 0.5},
    }


def test_staged_analyze_artifact_marks_method(monkeypatch, tmp_path):
    """分步流水线：analyze.json 的 predictions 用模型结果，顶层 method 为模型名。"""
    fake = _inject_model(monkeypatch, FakePredictor(model_name="textcnn"))
    store = VideoStore(tmp_path / "videos")
    _seed_task(store)

    records = _raw_records()
    detail = pipeline.run_pipeline(
        store,
        BVID,
        REAL_INSTANT,
        task_settings=REAL_SETTINGS,
        collector=lambda bvid, crawl_config: records,
    )

    assert detail is not None
    assert detail["placeholder"] is False  # 评论数据真实
    analyze = store.read_stage(BVID, "analyze")
    assert analyze is not None
    assert analyze["comment_total"] == len(records)
    # 模型名如实标注（内部审计字段，不改变 HTTP 契约）
    assert analyze["method"] == "textcnn"
    assert len(analyze["predictions"]) == len(records)
    for prediction in analyze["predictions"]:
        assert set(prediction) == PREDICTION_FIELDS
        assert prediction["label"] == "positive"
        assert prediction["confidence"] == 0.9
    # 聚合层（placeholder.build_detail）同样吃模型结果：全部 positive
    assert detail["sentiment"]["positive"] == len(records)
    assert detail["sentiment"]["negative"] == 0
    assert detail["sentiment"]["neutral"] == 0
    assert fake.calls, "FakePredictor 必须被真正调用"


# --------------------------------------------------------------------------- #
# 2. 旧路径（真实评论 → build_detail）：极性同样使用模型结果
# --------------------------------------------------------------------------- #
def test_build_detail_real_comments_uses_model_predictions(monkeypatch):
    """真实评论分支的情感极性来自模型；字段集合 / placeholder 语义保持不变。"""
    assert service.predict_rule_based(RULE_NEGATIVE_TEXT)["label"] == "negative"
    _inject_model(monkeypatch, FakePredictor(label="positive", confidence=0.9))

    records = _raw_records()
    detail = placeholder.build_detail(BVID, comments=records)

    assert set(detail) == DETAIL_PAYLOAD_FIELDS  # 字段一个都不增删改名
    assert detail["placeholder"] is False  # 只表示评论数据真实
    assert detail["sentiment"]["total"] == len(records)
    # 规则预测下会各有一条 negative；模型固定 positive → 全 positive 才说明走了模型
    assert detail["sentiment"]["positive"] == len(records)
    assert detail["sentiment"]["negative"] == 0
    assert detail["sentiment"]["neutral"] == 0
    assert detail["top_comments"]["negative"] == []
    assert all(
        item["label"] == "positive" for item in detail["top_comments"]["positive"]
    )
    # top_comments 的置信度来自模型结果
    assert all(
        item["confidence"] == 0.9 for item in detail["top_comments"]["positive"]
    )
    # trend 的极性分桶同样按模型 label 走
    assert all(point["negative"] == 0 for point in detail["trend"])


def test_build_detail_degrades_single_comment_on_error(monkeypatch):
    """单条评论模型推理异常 → 中性 0.5，不影响其它评论与整份 detail。"""
    fake = FakePredictor(label="positive", confidence=0.9, fail_texts=(RULE_POSITIVE_TEXT,))
    _inject_model(monkeypatch, fake)

    detail = placeholder.build_detail(BVID, comments=_raw_records())

    assert set(detail) == DETAIL_PAYLOAD_FIELDS
    text_to_confidence = {
        item["text"]: item["confidence"]
        for item in detail["top_comments"]["positive"]
    }
    assert text_to_confidence[RULE_NEGATIVE_TEXT] == 0.9
    assert text_to_confidence[RULE_POSITIVE_TEXT] == 0.5  # 中性退化
    # 两条都是 positive label，中性兜底不产生 negative
    assert detail["sentiment"]["positive"] == 2
    assert detail["sentiment"]["negative"] == 0


def test_predictor_load_error_falls_back_to_rule(monkeypatch):
    """``get_predictor`` 抛错（权重损坏等）→ 回退规则占位，不把异常抛给调用方。"""
    calls: list[int] = []

    def boom():
        calls.append(1)
        raise RuntimeError("模拟检查点损坏")

    monkeypatch.setattr(pipeline.service, "get_predictor", boom)

    records = [{"text": RULE_NEGATIVE_TEXT}, {"text": RULE_POSITIVE_TEXT}]
    predictions = pipeline._predict_cleaned_records(records)

    assert calls == [1]  # 每次分析只尝试解析一次
    assert predictions == [service.predict_rule_based(record["text"]) for record in records]
    assert pipeline._resolve_prediction_method(None) == "rule_placeholder"


def test_staged_pipeline_survives_model_load_failure(monkeypatch, tmp_path):
    """真实采集 + 模型加载失败：任务照常 completed，analyze.json 标注 rule_placeholder。"""

    def boom():
        raise RuntimeError("模拟检查点损坏")

    monkeypatch.setattr(pipeline.service, "get_predictor", boom)
    store = VideoStore(tmp_path / "videos")
    _seed_task(store)

    records = _raw_records()
    detail = pipeline.run_pipeline(
        store,
        BVID,
        REAL_INSTANT,
        task_settings=REAL_SETTINGS,
        collector=lambda bvid, crawl_config: records,
    )

    assert detail is not None  # 模型加载失败不能让任务 failed
    assert detail["placeholder"] is False
    analyze = store.read_stage(BVID, "analyze")
    assert analyze["method"] == "rule_placeholder"
    cleaned = store.read_stage(BVID, "clean")["records"]
    assert analyze["predictions"] == [
        service.predict_rule_based(str(record["text"])) for record in cleaned
    ]


# --------------------------------------------------------------------------- #
# 3. 真实模型加载路径：仓库真实检查点 → 结构正确（只验证链路，不写死准确率）
# --------------------------------------------------------------------------- #
def test_real_checkpoint_predictions_are_structurally_valid(monkeypatch):
    """真实加载 ``models/checkpoints/textcnn`` 后逐条预测：结构 / 概率 / 确定性正确。

    训练语料是酒店/外卖公开语料，**不在此断言 B 站领域准确率**，只验证
    「真实模型能加载、能推理、输出契约结构」这条链路。
    """
    predictor = service.load_model("textcnn")
    if predictor is None:  # 检查点不存在 / torch 不可用
        pytest.skip("textcnn 检查点不存在或 torch 不可用，跳过真实加载用例")

    _inject_model(monkeypatch, predictor)
    texts = ["这个视频质量很好，非常满意。", "这个视频质量太差了，非常失望。"]
    predictions = pipeline._predict_cleaned_records([{"text": text} for text in texts])

    assert len(predictions) == len(texts)
    for prediction in predictions:
        assert set(prediction) == PREDICTION_FIELDS
        assert prediction["label"] in service.LABELS
        assert 0.0 <= prediction["confidence"] <= 1.0
        assert set(prediction["probs"]) == {"positive", "negative"}
        assert sum(prediction["probs"].values()) == pytest.approx(1.0, abs=1e-3)
        assert prediction["confidence"] == pytest.approx(
            max(prediction["probs"].values()), abs=1e-3
        )

    # 同一文本两次推理结果一致（确定性，不追准确率）
    again = pipeline._predict_cleaned_records([{"text": text} for text in texts])
    assert [item["label"] for item in again] == [item["label"] for item in predictions]
    assert [item["confidence"] for item in again] == pytest.approx(
        [item["confidence"] for item in predictions]
    )

    # method 如实标注为真实模型名
    assert pipeline._resolve_prediction_method(predictor) == "textcnn"


def test_missing_checkpoint_falls_back_to_rule_placeholder(monkeypatch):
    """模型不可用（get_predictor 返回 None）时回退规则占位，结果与既有确定性一致。"""
    monkeypatch.setattr(pipeline.service, "get_predictor", lambda: None)

    records = [{"text": RULE_NEGATIVE_TEXT}, {"text": RULE_POSITIVE_TEXT}]
    predictions = pipeline._predict_cleaned_records(records)

    expected = [service.predict_rule_based(record["text"]) for record in records]
    assert predictions == expected
    assert pipeline._resolve_prediction_method(None) == "rule_placeholder"
