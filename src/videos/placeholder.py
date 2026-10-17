"""⚠️ **占位数据生成器（不是真实爬取 + 真实模型的结果）**。

真实爬虫已在 `src/crawler/` 实现（B 站接口版 + 通用 HTML 版，含 robots / 限速 / 风控退避），
但本模块**尚未接入真实爬取**：`src/videos/pipeline.py` 仍按确定性种子造占位数据。因此本模块用
**确定性伪随机**（种子来自 `sha256(bvid)`，同一 BV 号每次结果完全一致）造出一份
**结构完整、字段齐全**的 detail，让前端在真实数据接入前就能联调。

诚实约定（必须保持）：

1. 详情响应带顶层 ``placeholder: true``，如实标注数据由占位逻辑生成；
2. 标题 / UP 主带「占位」字样，一眼可辨，不会让人误以为是真实视频；
3. 真实接入点：把 :func:`build_detail` 的数据来源换成
   ``src.crawler.fetch_video_comments`` / ``src.crawler.crawl_video`` 的产物 + 真实模型推理结果，
   并把 ``placeholder`` 置为 ``False``（字段结构无需改动）。

`model` 字段的取值规则：优先读 ``GET /api/models``（即
``outputs/stats/metrics_<model>.json``）里的**真实指标**；尚未训练（指标全 0）时退回
契约示例里的占位指标值，保证结构完整、前端图表可渲染。
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Any

from .messages import MESSAGE_COMPLETED

#: 占位标题 / UP 主（带「占位」字样，避免被误认为真实数据）
PLACEHOLDER_TITLE_TEMPLATE = "【占位数据】{bvid} 的示例视频标题"
PLACEHOLDER_OWNER = "占位UP主（真实爬虫待接入）"

#: 未训练时 `model` 用的占位指标（与契约示例一致）
PLACEHOLDER_MODEL_METRICS: dict[str, float] = {
    "accuracy": 0.912,
    "precision": 0.908,
    "recall": 0.915,
    "f1": 0.911,
}

#: 趋势图默认分桶天数（按天 `YYYY-MM-DD`）
TREND_DAYS = 14

#: 词云每侧取词数
KEYWORDS_PER_SIDE = 8

#: 代表性评论每侧条数（契约建议 ≤ 10）
TOP_COMMENTS_PER_SIDE = 5

#: 占位关键词池（真实接入后由分词 + 词频统计产出）
POSITIVE_KEYWORD_POOL = (
    "好用", "画面清晰", "讲解详细", "节奏舒服", "干货满满", "值得推荐",
    "剪辑用心", "音质不错", "内容扎实", "思路清晰", "更新稳定", "很有帮助",
)
NEGATIVE_KEYWORD_POOL = (
    "卡顿", "画质差", "广告太多", "标题党", "节奏拖沓", "音质差",
    "水视频", "更新太慢", "内容注水", "剪辑混乱", "重复啰嗦", "强行恰饭",
)

#: 占位评论池（真实接入后由爬取结果按点赞数排序产出）
POSITIVE_COMMENT_POOL = (
    ("讲得太清楚了，比我自己看书收获还大，已经三连。", 5231, 0.986),
    ("这个系列的质量一直很稳，每期都值得看。", 3120, 0.971),
    ("节奏把握得很好，干货密度高，感谢UP主。", 2455, 0.964),
    ("终于有人把这块讲明白了，收藏慢慢消化。", 1980, 0.958),
    ("看完立刻做出效果，太实用了，强烈推荐。", 1642, 0.951),
    ("剪辑很用心，字幕也清楚，观感非常舒服。", 1320, 0.947),
)
NEGATIVE_COMMENT_POOL = (
    ("中间那段广告太长了，直接劝退。", 2870, 0.975),
    ("标题和内容不符，纯标题党浪费我时间。", 2244, 0.968),
    ("更新太慢了，等得都忘了前面讲什么。", 1875, 0.961),
    ("画质有点糊，声音还忽大忽小，看着累。", 1433, 0.952),
    ("十分钟能讲完的内容硬拖了半小时。", 1102, 0.944),
    ("同样的内容翻来覆去讲，水得有点多。", 986, 0.938),
)

#: 占位评论长度统计区间（字符数）
AVG_CHARS_RANGE = (18.0, 48.0)
MAX_CHARS_RANGE = (80, 300)

#: 占位评论总数区间
COMMENT_TOTAL_RANGE = (200, 9000)


def seed_for(bvid: str) -> int:
    """由 `bvid` 派生确定性种子（不用内置 ``hash()``，它每次进程启动都变）。"""
    digest = hashlib.sha256(bvid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def planned_comment_total(bvid: str) -> int:
    """占位流水线里该视频的评论总数（确定性，与 :func:`build_detail` 的 `comment_total` 一致）。

    采集阶段用它在 `comment_count` 上逐步增长，让「已入库评论数」真实地涨起来。
    """
    return random.Random(seed_for(bvid)).randint(*COMMENT_TOTAL_RANGE)


def build_model_block() -> dict[str, Any]:
    """组装 `detail.model`：优先真实指标，未训练时退回占位指标。

    延迟导入 `src.inference.service`，避免包之间形成导入环。
    """
    # 延迟导入：`src.inference.service` 是既有推理层，这里只借它的模型指标
    from ..inference import service as inference_service

    name = inference_service.get_active_model_name()
    metrics: dict[str, float] | None = None
    try:
        for item in inference_service.available_models():
            if item.get("name") != name:
                continue
            candidate = {key: float(value) for key, value in item.get("metrics", {}).items()}
            if candidate and any(value > 0 for value in candidate.values()):
                metrics = candidate  # 训练产出过指标 → 用真实值
            break
    except Exception:  # noqa: BLE001 - 指标读取失败不能影响接口可用性
        metrics = None

    if metrics is None:
        metrics = dict(PLACEHOLDER_MODEL_METRICS)

    for key, default in PLACEHOLDER_MODEL_METRICS.items():
        metrics.setdefault(key, default)
    return {
        "name": name,
        "accuracy": float(metrics["accuracy"]),
        "precision": float(metrics["precision"]),
        "recall": float(metrics["recall"]),
        "f1": float(metrics["f1"]),
    }


def build_detail(
    bvid: str,
    *,
    finished_at: datetime | None = None,
    comment_total: int | None = None,
) -> dict[str, Any]:
    """生成一份结构完整的**占位** detail（字段与 `docs/API契约.md` 第三节一致）。

    Args:
        bvid: BV 号（同时是伪随机种子）。
        finished_at: 完成时间（默认当前本地时间，带时区）。
        comment_total: 评论总数；``None`` 时按种子生成。

    Returns:
        可直接作为 ``GET /api/videos/{bvid}/detail`` 响应体的字典，
        顶层 ``placeholder`` 恒为 ``True``。
    """
    rng = random.Random(seed_for(bvid))
    finished = (finished_at or datetime.now().astimezone())
    if finished.tzinfo is None:
        finished = finished.astimezone()

    # 评论总数用独立的 Random 实例，保证「显式传入 total」与「不传」时其余字段完全一致
    total = max(int(comment_total if comment_total is not None else planned_comment_total(bvid)), 0)

    # ---- sentiment：三个计数精确求和等于 total，比例四舍五入到 3 位 ----
    positive_ratio = rng.uniform(0.45, 0.65)
    negative_ratio = rng.uniform(0.15, 0.30)
    positive = round(total * positive_ratio)
    negative = round(total * negative_ratio)
    neutral = max(total - positive - negative, 0)
    sentiment = {
        "total": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "positive_ratio": round(positive / total, 3) if total else 0.0,
        "negative_ratio": round(negative / total, 3) if total else 0.0,
        "neutral_ratio": round(neutral / total, 3) if total else 0.0,
    }

    # ---- trend：最近 TREND_DAYS 天，按天分桶 ----
    weights = [rng.uniform(0.6, 1.4) for _ in range(TREND_DAYS)]
    weight_sum = sum(weights) or 1.0
    trend: list[dict[str, Any]] = []
    for offset, weight in enumerate(weights):
        day = finished - timedelta(days=TREND_DAYS - 1 - offset)
        day_total = round(total * weight / weight_sum)
        day_positive = round(day_total * (positive / total)) if total else 0
        day_negative = round(day_total * (negative / total)) if total else 0
        trend.append(
            {
                "bucket": day.strftime("%Y-%m-%d"),
                "positive": day_positive,
                "negative": day_negative,
                "neutral": max(day_total - day_positive - day_negative, 0),
            }
        )

    # ---- keywords：每侧 KEYWORDS_PER_SIDE 个词，weight 为 count / max_count ----
    keywords = {
        "positive": _build_keywords(rng, POSITIVE_KEYWORD_POOL, total),
        "negative": _build_keywords(rng, NEGATIVE_KEYWORD_POOL, total),
    }

    # ---- top_comments：每侧 TOP_COMMENTS_PER_SIDE 条，按点赞降序 ----
    top_comments = {
        "positive": _build_top_comments(rng, POSITIVE_COMMENT_POOL, "positive"),
        "negative": _build_top_comments(rng, NEGATIVE_COMMENT_POOL, "negative"),
    }

    pubdate = finished - timedelta(days=rng.randint(30, 400))
    length_stats = {
        "avg_chars": round(rng.uniform(*AVG_CHARS_RANGE), 1),
        "max_chars": rng.randint(*MAX_CHARS_RANGE),
    }

    return {
        "video": {
            "bvid": bvid,
            "title": PLACEHOLDER_TITLE_TEMPLATE.format(bvid=bvid),
            "owner": PLACEHOLDER_OWNER,
            "cover_url": None,
            "pubdate": pubdate.isoformat(timespec="seconds"),
            "crawled_at": finished.isoformat(timespec="seconds"),
            "comment_total": total,
        },
        "analysis": {
            "status": "completed",
            "progress": 100,
            "message": MESSAGE_COMPLETED,
            "length_stats": length_stats,
        },
        "sentiment": sentiment,
        "trend": trend,
        "keywords": keywords,
        "model": build_model_block(),
        "top_comments": top_comments,
        "error": None,
        # 诚实标注：本条 detail 由占位逻辑生成，真实爬虫接入后置为 False
        "placeholder": True,
    }


def _build_keywords(
    rng: random.Random, pool: tuple[str, ...], total: int
) -> list[dict[str, Any]]:
    """从词池取样并生成归一化 ``weight``（词云可直接用 count 或 weight）。"""
    words = list(pool)
    rng.shuffle(words)
    picked = words[:KEYWORDS_PER_SIDE]
    counts = [max(int(total * rng.uniform(0.004, 0.05)), 1) for _ in picked]
    peak = max(counts) if counts else 1
    return [
        {"word": word, "count": count, "weight": round(count / peak, 3)}
        for word, count in sorted(zip(picked, counts), key=lambda item: -item[1])
    ]


def _build_top_comments(
    rng: random.Random,
    pool: tuple[tuple[str, int, float], ...],
    label: str,
) -> list[dict[str, Any]]:
    """取点赞最高的若干条代表性评论（占位池已按点赞降序）。"""
    picked = list(pool)[:TOP_COMMENTS_PER_SIDE]
    return [
        {
            "text": text,
            "like": int(like * rng.uniform(0.85, 1.15)),
            "confidence": round(min(max(confidence * rng.uniform(0.97, 1.02), 0.5), 0.999), 3),
            "label": label,
        }
        for text, like, confidence in picked
    ]
