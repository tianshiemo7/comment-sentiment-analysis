#!/usr/bin/env bash
# 获取数据：下载公开数据集，或运行爬虫采集。
# 现状：骨架占位，实现后请把下面 TODO 替换成真实逻辑。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[get_data] 仓库根目录: $ROOT"
echo "[get_data] 目标目录:   data/raw/"

# TODO(阶段一): 二选一
#   1) 下载公开数据集（ChnSentiCorp / 外卖评论 / IMDB / Yelp）
#      - 建议写成 src/crawler/datasets.py 里的函数，此处调用
#   2) 运行爬虫（必须检查 robots.txt 并限速）
#      - python -m src.crawler.spider --site <站点> --limit 10000
#
# 采集完成后必须记录：站点、URL、采集时间、条数 → data/raw/来源说明.md

echo "[get_data] TODO: 尚未实现，请先完成 src/crawler/ 模块。"
