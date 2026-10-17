"""detail 生成器：默认是**确定性占位数据**；可选接入**真实爬取的评论**。

真实爬虫已在 `src/crawler/` 实现（B 站接口版 + 通用 HTML 版，含 robots / 限速 / 风控退避），
`/api/videos` 自 P0-C 起支持 **opt-in** 真实采集（``VIDEO_REAL_CRAWL=1`` /
``PipelineConfig(real_crawl=True)``，见 `crawler_adapter.py`）：

- ``comments=None``（默认）：仍用**确定性伪随机**（种子来自 ``sha256(bvid)``，同一 BV 号
  每次结果完全一致）造出一份**结构完整、字段齐全**的 detail，输出与接入真实采集前
  **逐字段一致**，让前端在默认配置下继续联调；
- ``comments`` 非空：用真实评论记录生成 ``comment_total`` / ``top_comments`` /
  ``keywords`` / ``trend`` / ``length_stats``（真实文本 + 真实 ``ctime_utc``）。

诚实约定（必须保持）：

1. 详情响应带顶层 ``placeholder``：占位数据为 ``true``；使用真实爬取评论时为 ``false``
   （如实标注「评论数据来自真实爬虫」）；
2. **``placeholder=false`` 只表示评论数据真实，不表示模型结果真实**：正 / 负极性仍来自
   ``src.inference.service.predict_rule_based``（规则 / 词典占位预测，不是训练模型），
   `model` 块在未训练时也可能仍是占位指标——真实模型接入后两者会自然变化；
3. 占位标题 / UP 主带「占位」字样；真实评论路径下标题使用爬虫返回的
   ``video_title``（拿不到时回退占位标题），UP 主 / 发布日期爬虫 CSV 不含，置 ``None``；
4. 真实接入点：把各阶段的数据来源换成真实产物（`src.preprocessing` / `src.inference`），
   字段结构无需改动。

`model` 字段的取值规则：优先读 ``GET /api/models``（即
``outputs/stats/metrics_<model>.json``）里的**真实指标**；尚未训练（指标全 0）时退回
契约示例里的占位指标值，保证结构完整、前端图表可渲染。
"""

from __future__ import annotations

import hashlib
import random
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
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
    comments: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成一份结构完整的 detail（字段与 `docs/API契约.md` 第三节一致）。

    两种数据来源：

    - ``comments=None``（默认）：**占位数据**，确定性伪随机；输出与接入真实采集前
      **逐字段一致**，顶层 ``placeholder`` 为 ``True``；
    - ``comments`` 非空：使用真实爬取记录（`crawler_adapter.collect_video_comments`
      的产物）。``comment_total = len(comments)``，``top_comments`` / ``keywords`` /
      ``trend`` / ``length_stats`` 均由真实文本与真实 ``ctime_utc`` 生成，
      顶层 ``placeholder`` 置为 ``False``。

    **诚实约定**：``placeholder=False`` **只表示评论数据来自真实爬虫**；正 / 负极性
    仍来自 ``src.inference.service.predict_rule_based``（规则 / 词典占位预测，不是
    训练模型），``model`` 块在未训练时也可能仍是占位指标。真实模型接入后两者自然变化。

    Args:
        bvid: BV 号（占位路径同时是伪随机种子）。
        finished_at: 完成时间（默认当前本地时间，带时区）。
        comment_total: 评论总数；占位路径 ``None`` 时按种子生成。
            真实评论路径固定用 ``len(comments)``（保证 sentiment 计数自洽）。
        comments: 真实评论记录列表（字段见
            :data:`src.videos.crawler_adapter.STANDARD_FIELDS`）；``None`` 或空列表
            时走占位逻辑（保持既有行为）。

    Returns:
        可直接作为 ``GET /api/videos/{bvid}/detail`` 响应体的字典。
    """
    rng = random.Random(seed_for(bvid))
    finished = (finished_at or datetime.now().astimezone())
    if finished.tzinfo is None:
        finished = finished.astimezone()

    real_comments = [comment for comment in (comments or []) if isinstance(comment, Mapping)]
    has_real_data = bool(real_comments)
    predictions = _predict_real_comments(real_comments) if has_real_data else []

    if has_real_data:
        # ---- 真实评论路径：总数 = 真实条数，sentiment 计数天然自洽 ----
        total = len(real_comments)
        sentiment = _build_real_sentiment(predictions, total)
        positive = sentiment["positive"]
        negative = sentiment["negative"]
        trend = _build_real_trend(real_comments, predictions)
        if not trend:
            # 没有任何可解析的 ctime_utc 时，回退到占位趋势逻辑（同样确定性）
            trend = _build_placeholder_trend(rng, finished, total, positive, negative)
        keywords = _build_real_keywords(real_comments, predictions)
        top_comments = _build_real_top_comments(real_comments, predictions)
        length_stats = _build_real_length_stats(real_comments)
        pubdate: datetime | None = None  # 爬虫 CSV 只含评论时间，不含视频发布日期
        video_title = _real_video_title(real_comments) or PLACEHOLDER_TITLE_TEMPLATE.format(
            bvid=bvid
        )
        video_owner: str | None = None  # 爬虫 CSV 不含 UP 主，诚实地置 None
        placeholder_flag = False
    else:
        # ---- 占位路径：与接入真实采集前逐字段一致 ----
        # 评论总数用独立的 Random 实例，保证「显式传入 total」与「不传」时其余字段完全一致
        total = max(
            int(comment_total if comment_total is not None else planned_comment_total(bvid)), 0
        )

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
        trend = _build_placeholder_trend(rng, finished, total, positive, negative)

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
        video_title = PLACEHOLDER_TITLE_TEMPLATE.format(bvid=bvid)
        video_owner = PLACEHOLDER_OWNER
        placeholder_flag = True

    return {
        "video": {
            "bvid": bvid,
            "title": video_title,
            "owner": video_owner,
            "cover_url": None,
            "pubdate": pubdate.isoformat(timespec="seconds") if pubdate is not None else None,
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
        # 诚实标注：占位数据为 True；使用真实爬取评论时为 False
        # （False 只表示评论数据真实，sentiment 仍是规则占位预测，见 docstring）
        "placeholder": placeholder_flag,
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


def _build_placeholder_trend(
    rng: random.Random,
    finished: datetime,
    total: int,
    positive: int,
    negative: int,
) -> list[dict[str, Any]]:
    """占位趋势：最近 :data:`TREND_DAYS` 天按天分桶（确定性伪随机权重）。"""
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
    return trend


# --------------------------------------------------------------------------- #
# 真实评论 → detail 字段（离线、确定性；只依赖评论记录本身）
# --------------------------------------------------------------------------- #
def _predict_real_comments(
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """对真实评论逐条做**规则占位**情感预测（延迟导入，避免包间导入环）。

    真实模型接入后只需把这里换成 ``src.inference.service.predict_text`` 的真实分支；
    单条预测异常时退化为中性（0.5），不影响整份 detail 的构建。
    """
    from ..inference.service import predict_rule_based

    predictions: list[dict[str, Any]] = []
    for comment in comments:
        text = str(comment.get("text") or "")
        try:
            predictions.append(predict_rule_based(text))
        except Exception:  # noqa: BLE001 - 单条预测失败不能拖垮整个详情
            predictions.append(
                {
                    "text": text,
                    "label": "positive",
                    "confidence": 0.5,
                    "probs": {"positive": 0.5, "negative": 0.5},
                }
            )
    return predictions


def _build_real_sentiment(
    predictions: Sequence[Mapping[str, Any]], total: int
) -> dict[str, Any]:
    """由规则占位预测聚合 sentiment（规则预测只有 positive / negative，无中性）。"""
    positive = sum(1 for item in predictions if item.get("label") == "positive")
    negative = sum(1 for item in predictions if item.get("label") == "negative")
    neutral = max(total - positive - negative, 0)
    return {
        "total": total,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "positive_ratio": round(positive / total, 3) if total else 0.0,
        "negative_ratio": round(negative / total, 3) if total else 0.0,
        "neutral_ratio": round(neutral / total, 3) if total else 0.0,
    }


def _build_real_trend(
    comments: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按评论发表时间（``ctime_utc``）按天分桶；没有任何有效时间时返回空列表。"""
    buckets: dict[str, dict[str, Any]] = {}
    for comment, prediction in zip(comments, predictions):
        day = _bucket_day(comment.get("ctime_utc"))
        if day is None:
            continue
        bucket = buckets.setdefault(
            day, {"bucket": day, "positive": 0, "negative": 0, "neutral": 0}
        )
        label = prediction.get("label")
        bucket[label if label in ("positive", "negative") else "neutral"] += 1
    return [buckets[key] for key in sorted(buckets)]


def _bucket_day(value: Any) -> str | None:
    """把 ``ctime_utc``（ISO8601 字符串）转成 UTC 当天 ``YYYY-MM-DD``；非法返回 None。"""
    moment = _parse_iso_moment(value)
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _parse_iso_moment(value: Any) -> datetime | None:
    """尽量解析 ISO8601 时间（接受 ``Z`` 结尾与无时区值，无时区按 UTC 处理）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _build_real_keywords(
    comments: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按情感极性分组，用真实评论文本统计词频（词云数据）。"""
    positive_texts = [
        str(comment.get("text") or "")
        for comment, prediction in zip(comments, predictions)
        if prediction.get("label") == "positive"
    ]
    negative_texts = [
        str(comment.get("text") or "")
        for comment, prediction in zip(comments, predictions)
        if prediction.get("label") == "negative"
    ]
    return {
        "positive": _keywords_from_texts(positive_texts),
        "negative": _keywords_from_texts(negative_texts),
    }


def _keywords_from_texts(texts: Sequence[str]) -> list[dict[str, Any]]:
    """真实文本 → 词频 top-N（``count`` 为真实出现次数，``weight`` 归一化到峰值 1）。"""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(_tokenize_real_text(text))
    if not counts:
        return []
    peak = max(counts.values())
    picked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:KEYWORDS_PER_SIDE]
    return [
        {"word": word, "count": int(count), "weight": round(count / peak, 3)}
        for word, count in picked
    ]


def _tokenize_real_text(text: str) -> list[str]:
    """真实文本分词：先去掉 URL/@提及/[表情] 噪声，再复用 jieba（失败时二元组兜底）。"""
    cleaned = _strip_keyword_noise(text)
    tokens: list[str] = []
    try:
        from ..preprocessing.tokenize import tokenize as tokenize_text

        tokens = tokenize_text(cleaned, lang="zh")
    except Exception:  # noqa: BLE001 - jieba / 资源缺失时退回内置兜底分词
        tokens = []
    if not tokens:
        tokens = _fallback_tokens(cleaned)
    return [token for token in tokens if _is_keyword_token(token)]


def _strip_keyword_noise(text: str) -> str:
    """去掉 URL、@用户、[B站表情]——词云应展示内容词，而不是链接和表情名。"""

    cleaned = _REAL_URL_RE.sub(" ", str(text))
    cleaned = _REAL_MENTION_RE.sub(" ", cleaned)
    cleaned = _REAL_EMOTICON_RE.sub(" ", cleaned)
    return cleaned


def _fallback_tokens(text: str) -> list[str]:
    """无 jieba 时的兜底分词：中文按 2 字滑窗 + 整段（≤4 字），英文按单词。"""
    tokens: list[str] = []
    for match in _KEYWORD_RUN_RE.finditer(text):
        piece = match.group(0)
        if _is_cjk_run(piece):
            if 2 <= len(piece) <= 4:
                tokens.append(piece)
            tokens.extend(piece[index : index + 2] for index in range(len(piece) - 1))
        else:
            tokens.append(piece)
    return tokens


def _is_cjk_run(piece: str) -> bool:
    return bool(piece) and all("\u3400" <= char <= "\u9fff" for char in piece)


def _is_keyword_token(token: Any) -> bool:
    """关键词过滤：长度 ≥ 2、含字母/数字、非纯数字、不在停用词表。"""
    word = str(token).strip().lower()
    if len(word) < 2:
        return False
    if not any(char.isalnum() for char in word):
        return False
    if word.isdigit():
        return False
    if _URL_FRAGMENT_RE.fullmatch(word):
        return False
    return word not in _KEYWORD_STOPWORDS


def _real_video_title(comments: Sequence[Mapping[str, Any]]) -> str:
    """取爬虫记录里的真实视频标题（缺失 / 全空时返回空串）。"""
    for comment in comments:
        title = str(comment.get("video_title") or "").strip()
        if title:
            return title
    return ""


def _build_real_top_comments(
    comments: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """每侧取点赞最高的若干条真实评论（contract：每侧 ≤ 10）。"""
    grouped: dict[str, list[dict[str, Any]]] = {"positive": [], "negative": []}
    for comment, prediction in zip(comments, predictions):
        label = prediction.get("label")
        if label not in grouped:
            label = "positive"
        confidence = _as_float(prediction.get("confidence"), default=0.5)
        grouped[label].append(
            {
                "text": str(comment.get("text") or ""),
                "like": _as_int(comment.get("likes")),
                "confidence": round(min(max(confidence, 0.0), 1.0), 3),
                "label": label,
            }
        )
    result: dict[str, list[dict[str, Any]]] = {}
    for label, items in grouped.items():
        items.sort(key=lambda item: (-item["like"], item["text"]))
        result[label] = items[:TOP_COMMENTS_PER_SIDE]
    return result


def _build_real_length_stats(
    comments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """真实评论长度统计（字符数）。"""
    lengths = [len(str(comment.get("text") or "")) for comment in comments]
    if not lengths:
        return {"avg_chars": 0.0, "max_chars": 0}
    return {
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "max_chars": max(lengths),
    }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


#: 兜底分词用：中文连续段 / 英文单词
_KEYWORD_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9']+")

#: 关键词去噪：URL / @提及 / [B站表情]
_REAL_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'）】]+", re.IGNORECASE)
_REAL_MENTION_RE = re.compile(r"@[^\s@]+")
_REAL_EMOTICON_RE = re.compile(r"\[[^\[\]]{1,20}\]")

#: URL 片段 / 域名 / 视频号等无信息 token（去 URL 后仍可能出现的残余）
_URL_FRAGMENT_RE = re.compile(
    r"(?:https?|www|com|cn|net|org|html?|php|mp4|bilibili|share|music|video|m)"
    r"|bv[0-9a-z]{10}",
    re.IGNORECASE,
)

#: 关键词停用词（分词器已过滤资源词表，这里再挡掉高频无信息词）
_KEYWORD_STOPWORDS = frozenset(
    {
        "这个", "那个", "就是", "还是", "真的", "感觉", "其实", "然后", "现在", "已经",
        "可以", "没有", "什么", "怎么", "因为", "所以", "但是", "不过", "而且", "如果",
        "一个", "一种", "我们", "你们", "他们", "自己", "时候", "东西", "这样", "那样",
        "视频", "内容", "the", "and", "for", "with", "that", "this", "you", "are",
    }
)
