"""按 B 站排行榜分区生成大规模评论采集目录（跨分类）。

用法:
    python scripts/build_crawl_catalog.py --per-category 12 --out-dir data/raw/sentiment_crawl

产物:
    <out-dir>/crawl_plan.csv    bvid, category, rid, title, owner, reply, view, pubdate
    <out-dir>/bvids.txt         每行一个 bvid（供爬虫 --bvid-file）
    <out-dir>/catalog.json      原始排行榜快照 + 采集计划摘要
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 可用的排行榜分区（rid -> 分区名）；番剧/国创等 PGC 分区接口返回 -400，已剔除。
CATEGORIES: list[tuple[int, str]] = [
    (1, "动画"),
    (3, "音乐"),
    (129, "舞蹈"),
    (4, "游戏"),
    (188, "科技"),
    (36, "知识"),
    (234, "运动"),
    (223, "汽车"),
    (160, "生活"),
    (211, "美食"),
    (217, "动物圈"),
    (155, "时尚"),
    (5, "娱乐"),
    (181, "影视"),
    (119, "鬼畜"),
    (177, "纪录片"),
    (23, "电影"),
    (11, "电视剧"),
]


def load_cookie() -> str:
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    if cookie:
        return cookie
    settings = ROOT / "outputs" / "settings.json"
    if settings.exists():
        data = json.loads(settings.read_text(encoding="utf-8"))
        return str((data.get("account") or {}).get("cookie") or "").strip()
    return ""


def fetch_ranking(rid: int, cookie: str, retries: int = 3) -> list[dict]:
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Cookie": cookie,
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                "https://api.bilibili.com/x/web-interface/ranking/v2",
                params={"rid": rid, "type": "all"},
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"ranking rid={rid} code={payload.get('code')} msg={payload.get('message')}")
            return list((payload.get("data") or {}).get("list") or [])
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"排行榜分区 {rid} 获取失败: {last_err}")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成跨分类 B 站评论采集目录")
    parser.add_argument("--per-category", type=int, default=12)
    parser.add_argument("--min-replies", type=int, default=120, help="候选视频最少评论数")
    parser.add_argument("--out-dir", default="data/raw/sentiment_crawl")
    parser.add_argument("--delay", type=float, default=0.8)
    args = parser.parse_args()

    cookie = load_cookie()
    if not cookie:
        print("[warn] 未找到 Cookie，将以匿名方式请求排行榜")

    out_dir = ROOT / args.out_dir if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plans: list[dict] = []
    seen: set[str] = set()
    snapshot: dict[str, list] = {}

    for rid, cname in CATEGORIES:
        items = fetch_ranking(rid, cookie)
        snapshot[f"{rid}:{cname}"] = items
        picked = 0
        for item in items:
            bvid = str(item.get("bvid") or "")
            if not bvid or bvid in seen:
                continue
            stat = item.get("stat") or {}
            reply = int(stat.get("reply") or 0)
            if reply < args.min_replies:
                continue
            seen.add(bvid)
            plans.append(
                {
                    "bvid": bvid,
                    "category": cname,
                    "rid": rid,
                    "title": str(item.get("title") or "").strip(),
                    "owner": str((item.get("owner") or {}).get("name") or "").strip(),
                    "reply": reply,
                    "view": int(stat.get("view") or 0),
                    "pubdate": datetime.fromtimestamp(
                        int(item.get("pubdate") or 0), tz=timezone.utc
                    ).isoformat(),
                }
            )
            picked += 1
            if picked >= args.per_category:
                break
        print(f"[catalog] rid={rid:4d} {cname:4s} 候选 {len(items):3d} -> 选中 {picked}")
        time.sleep(args.delay)

    # 按分区轮流输出，保证 bvids.txt 前段也覆盖多分类
    by_cat: dict[str, list[dict]] = {}
    for row in plans:
        by_cat.setdefault(row["category"], []).append(row)
    ordered: list[dict] = []
    idx = 0
    while True:
        added = False
        for cat in by_cat:
            if idx < len(by_cat[cat]):
                ordered.append(by_cat[cat][idx])
                added = True
        if not added:
            break
        idx += 1

    plan_csv = out_dir / "crawl_plan.csv"
    with plan_csv.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ordered[0].keys()))
        writer.writeheader()
        writer.writerows(ordered)

    (out_dir / "bvids.txt").write_text(
        "\n".join(row["bvid"] for row in ordered) + "\n", encoding="utf-8"
    )

    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    summary = {
        "generated_at": now,
        "source": "https://api.bilibili.com/x/web-interface/ranking/v2",
        "per_category": args.per_category,
        "min_replies": args.min_replies,
        "total_videos": len(ordered),
        "categories": {
            cat: len(rows) for cat, rows in by_cat.items()
        },
        "videos": ordered,
    }
    (out_dir / "catalog.json").write_text(
        json.dumps({"summary": summary, "ranking_snapshot": snapshot}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "ranking_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n[catalog] 视频总数 {len(ordered)}，分类数 {len(by_cat)}")
    for cat, rows in by_cat.items():
        replies = sum(r["reply"] for r in rows)
        print(f"  {cat:4s} {len(rows):3d} 个  评论总量约 {replies}")
    print(f"[catalog] 已写入 {plan_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
