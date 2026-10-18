"""并行分片运行 B 站评论爬虫（同账号、每进程独立限速，互不共享状态）。

用法:
    python scripts/crawl_shards.py --workers 3 --delay 3.0 --max-pages 2 --max-items 50

行为:
    - 读取 <catalog-dir>/bvids.txt，按 round-robin 切成 N 份（每份仍跨分类混合）；
    - 每个 worker 使用独立 --out-dir（shardN）与日志，可断点续爬（--skip-finished）；
    - 全部结束后把各 shard 的 CSV 合并为 <catalog-dir>/all_comments_raw.csv。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_cookie() -> str:
    cookie = os.environ.get("BILI_COOKIE", "").strip()
    if cookie:
        return cookie
    settings = ROOT / "outputs" / "settings.json"
    if settings.exists():
        data = json.loads(settings.read_text(encoding="utf-8"))
        return str((data.get("account") or {}).get("cookie") or "").strip()
    return ""


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", default="data/raw/sentiment_crawl")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--merge-output", default=None)
    args = parser.parse_args()

    catalog_dir = Path(args.catalog_dir)
    if not catalog_dir.is_absolute():
        catalog_dir = ROOT / catalog_dir
    bvids = [
        line.strip()
        for line in (catalog_dir / "bvids.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not bvids:
        print("[error] bvids.txt 为空", file=sys.stderr)
        return 2

    cookie = load_cookie()
    if not cookie:
        print("[warn] 未找到 Cookie，将以匿名方式爬取", file=sys.stderr)

    shards: list[list[str]] = [[] for _ in range(args.workers)]
    for i, bvid in enumerate(bvids):
        shards[i % args.workers].append(bvid)

    env = os.environ.copy()
    if cookie:
        env["BILI_COOKIE"] = cookie
    env["PYTHONIOENCODING"] = "utf-8"

    procs: list[tuple[int, subprocess.Popen]] = []
    for idx, shard in enumerate(shards, start=1):
        shard_dir = catalog_dir / f"shard{idx}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        shard_file = shard_dir / "bvids.txt"
        shard_file.write_text("\n".join(shard) + "\n", encoding="utf-8")
        log_path = catalog_dir / f"shard{idx}.log"
        cmd = [
            sys.executable,
            "-m",
            "src.crawler.bilibili",
            "--bvid-file",
            str(shard_file),
            "--max-pages",
            str(args.max_pages),
            "--max-sub-pages",
            "0",
            "--page-size",
            str(args.page_size),
            "--max-items",
            str(args.max_items),
            "--delay",
            str(args.delay),
            "--out-dir",
            str(shard_dir),
            "--log-file",
            str(log_path),
            "--no-source-note",
            "--quiet",
        ]
        log_fh = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        procs.append((idx, proc))
        print(f"[{now()}] worker{idx} 启动：{len(shard)} 个视频 -> {shard_dir}")

    codes: dict[int, int] = {}
    while procs:
        time.sleep(15)
        still = []
        for idx, proc in procs:
            code = proc.poll()
            if code is None:
                still.append((idx, proc))
            else:
                codes[idx] = code
                print(f"[{now()}] worker{idx} 结束 exit={code}")
        procs = still

    merged = Path(args.merge_output or (catalog_dir / "all_comments_raw.csv"))
    print(f"[{now()}] 全部分片结束：{codes}，开始合并 -> {merged}")
    import csv

    part_files: list[Path] = []
    for idx in range(1, args.workers + 1):
        shard_dir = catalog_dir / f"shard{idx}"
        part = catalog_dir / f"shard{idx}_merged.csv"
        merge_cmd = [
            sys.executable,
            "-m",
            "src.crawler.bilibili",
            "--out-dir",
            str(shard_dir),
            "--merge-output",
            str(part),
            "--no-source-note",
        ]
        rc = subprocess.run(merge_cmd, cwd=str(ROOT), env=env).returncode
        print(f"[{now()}] shard{idx} 合并 exit={rc} -> {part}")
        if rc == 0 and part.exists():
            part_files.append(part)

    header: list[str] | None = None
    total = 0
    with merged.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        for part in part_files:
            with part.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.reader(fh)
                head = next(reader, None)
                if head is None:
                    continue
                if header is None:
                    header = head
                    writer.writerow(header)
                elif head != header:
                    raise SystemExit(f"[error] 表头不一致：{part} {head}")
                for row in reader:
                    writer.writerow(row)
                    total += 1
    print(f"[{now()}] 合并完成：{merged}（{total} 行，{len(part_files)} 个分片）")
    ok = all(code == 0 for code in codes.values()) and bool(part_files)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
