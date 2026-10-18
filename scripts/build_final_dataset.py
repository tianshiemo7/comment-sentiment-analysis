"""把爬取结果整理为「一份总表 + 训练/测试划分」，不做任何文本清洗。

输入:
    data/raw/sentiment_crawl/all_comments_raw.csv   爬虫合并的原始评论
    data/raw/sentiment_crawl/crawl_plan.csv         采集目录（bvid -> 分类）

输出:
    data/raw/sentiment_crawl/comments_raw.csv       原始评论 + category/rid/split（唯一总表）
    data/raw/sentiment_crawl/train.csv              训练集（按 bvid 分组划分，80%）
    data/raw/sentiment_crawl/test.csv               测试集（按 bvid 分组划分，20%）
    data/raw/sentiment_crawl/videos_meta.csv        视频级清单与评论条数
    data/raw/sentiment_crawl/dataset_stats.json     统计摘要

说明:
    - text 字段保持爬虫产出的原文（仅爬虫自带标准化，无去重/分词/去噪等清洗）；
    - 划分以视频为最小单位（同一视频的评论只出现在 train 或 test），避免同视频泄漏；
    - 分层依据：分区 category。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_COLUMNS = [
    "bvid",
    "aid",
    "video_title",
    "rpid",
    "level",
    "root_rpid",
    "parent_rpid",
    "text",
    "likes",
    "reply_count",
    "ctime_utc",
    "collected_at_utc",
]
EXTRA_COLUMNS = ["category", "rid", "split"]


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default="data/raw/sentiment_crawl")
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    base = Path(args.base_dir)
    if not base.is_absolute():
        base = ROOT / base
    raw_path = base / "all_comments_raw.csv"
    plan_path = base / "crawl_plan.csv"
    if not raw_path.exists():
        raise SystemExit(f"[error] 缺少 {raw_path}")
    if not plan_path.exists():
        raise SystemExit(f"[error] 缺少 {plan_path}")

    plan: dict[str, dict] = {}
    with plan_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            plan[row["bvid"]] = row

    rows: list[dict] = []
    with raw_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        raw_header = list(reader.fieldnames or [])
        missing = [c for c in BASE_COLUMNS if c not in raw_header]
        if missing:
            raise SystemExit(f"[error] 原始 CSV 缺列：{missing}")
        for row in reader:
            bvid = row.get("bvid") or ""
            meta = plan.get(bvid, {})
            row["category"] = meta.get("category", "")
            row["rid"] = meta.get("rid", "")
            rows.append(row)

    # 视频级分组划分（分层：category），同一 bvid 只进入一侧
    videos: dict[str, str] = {}
    for row in rows:
        videos.setdefault(row["bvid"], row["category"])
    by_cat: dict[str, list[str]] = defaultdict(list)
    for bvid, cat in videos.items():
        by_cat[cat].append(bvid)
    rng = random.Random(args.seed)
    test_videos: set[str] = set()
    for cat in sorted(by_cat):
        bvids = sorted(by_cat[cat])
        rng.shuffle(bvids)
        n_test = max(1, round(len(bvids) * args.test_ratio))
        test_videos.update(bvids[:n_test])
    for row in rows:
        row["split"] = "test" if row["bvid"] in test_videos else "train"

    columns = BASE_COLUMNS + EXTRA_COLUMNS
    out_path = base / "comments_raw.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[dataset] 总表 {len(rows)} 行 -> {out_path}")

    for split in ("train", "test"):
        subset = [row for row in rows if row["split"] == split]
        path = base / f"{split}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(subset)
        print(f"[dataset] {split}: {len(subset)} 行 / {len({r['bvid'] for r in subset})} 视频 -> {path}")

    # 视频级清单
    per_video: dict[str, dict] = {}
    for row in rows:
        item = per_video.setdefault(
            row["bvid"],
            {
                "bvid": row["bvid"],
                "aid": row["aid"],
                "video_title": row["video_title"],
                "category": row["category"],
                "rid": row["rid"],
                "split": row["split"],
                "comments": 0,
                "avg_likes": 0.0,
            },
        )
        item["comments"] += 1
        try:
            item["avg_likes"] += int(float(row["likes"] or 0))
        except ValueError:
            pass
    for item in per_video.values():
        if item["comments"]:
            item["avg_likes"] = round(item["avg_likes"] / item["comments"], 2)
    meta_path = base / "videos_meta.csv"
    with meta_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(next(iter(per_video.values())).keys()))
        writer.writeheader()
        writer.writerows(sorted(per_video.values(), key=lambda x: x["bvid"]))
    print(f"[dataset] 视频清单 {len(per_video)} 个 -> {meta_path}")

    # 统计摘要
    lengths = [len(row["text"] or "") for row in rows]
    cat_counter = Counter(row["category"] for row in rows)
    split_counter = Counter(row["split"] for row in rows)
    duplicate_rpids = len(rows) - len({row["rpid"] for row in rows})
    stats = {
        "generated_at": now(),
        "raw_source": str(raw_path.relative_to(ROOT)),
        "total_comments": len(rows),
        "total_videos": len(per_video),
        "categories": dict(sorted(cat_counter.items())),
        "split": {
            "train_comments": split_counter.get("train", 0),
            "test_comments": split_counter.get("test", 0),
            "train_videos": sum(1 for v in per_video.values() if v["split"] == "train"),
            "test_videos": sum(1 for v in per_video.values() if v["split"] == "test"),
        },
        "duplicate_rpid_count": duplicate_rpids,
        "empty_text_count": sum(1 for row in rows if not (row["text"] or "").strip()),
        "text_length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "avg": round(sum(lengths) / len(lengths), 2) if lengths else 0,
        },
        "comments_per_video": {
            "min": min(v["comments"] for v in per_video.values()) if per_video else 0,
            "max": max(v["comments"] for v in per_video.values()) if per_video else 0,
            "avg": round(len(rows) / len(per_video), 2) if per_video else 0,
        },
        "notes": "未做文本清洗；split 按 bvid 分组、按 category 分层，避免同视频泄漏",
    }
    stats_path = base / "dataset_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dataset] 统计 -> {stats_path}")
    print(json.dumps({k: stats[k] for k in ("total_comments", "total_videos", "split")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
