"""词云词量与 Python 渲染词云图端点。

Task 1 先只放词量用例（词云图片端点的用例在 Task 3 追加到本文件）。
"""

from __future__ import annotations


def test_keyword_limits_are_sixty():
    """契约：视频任务每侧最多 60 个词，数据项目每侧最多 60 个词。"""
    from src.artifacts import pipeline
    from src.videos import placeholder

    assert placeholder.KEYWORDS_PER_SIDE == 60
    assert pipeline.KEYWORD_LIMIT == 60


def test_video_real_keywords_return_sixty_when_enough_words():
    """真实文本词频足够时，视频侧关键词返回满 60 个且按频次降序。"""
    from src.videos.placeholder import _keywords_from_texts

    # 用整词（ASCII）构造足量互不相同的 token：jieba 不会把它们拆开，
    # 每个词在文本里各出现 2 次，词频相同 → 排序稳定、可断言长度。
    words = [f"keyword{i}" for i in range(80)]
    text = " ".join(words * 2)
    picked = _keywords_from_texts([text])
    assert len(picked) == 60
    assert picked[0]["count"] >= picked[-1]["count"]


def test_artifact_keywords_return_sixty_when_enough_words():
    """数据项目侧词频足够时同样返回满 60 个（KEYWORD_LIMIT 契约）。"""
    from src.artifacts.pipeline import _build_keywords

    tokens = " ".join(f"keyword{i}" for i in range(80))
    rows = [{"tokens": tokens} for _ in range(2)]
    predictions = [{"label": "positive"}, {"label": "positive"}]
    result = _build_keywords(rows, predictions)
    assert len(result["positive"]) == 60
    assert result["positive"][0]["count"] >= result["positive"][-1]["count"]
