#!/usr/bin/env bash
# 获取数据：公开带标签数据集下载 + B 站视频评论采集。
#
# 用法：
#   bash scripts/get_data.sh --list-datasets          # 列出内置公开数据集
#   bash scripts/get_data.sh --dataset chnsenticorp   # 下载公开数据集（SHA256 校验 + 来源登记）
#   bash scripts/get_data.sh BV1GJ411x7h7
#   BV=BV1GJ411x7h7 PAGES=2 INCLUDE_REPLIES=1 bash scripts/get_data.sh
#   bash scripts/get_data.sh --html https://example.com/post --source 示例站点
#
# 公开数据集下载：
#   DATASET_DIR=data/raw/datasets 指定下载目录；FORCE=1 强制重新下载；
#   下载后用 `python -m src.crawler.datasets --import-csv <文件>` 统一成 text,label。
#
# 合规约定（脚本层面强制）：
#   - 默认请求间隔 2.5 秒（--delay 不得小于 2）；
#   - 采集前校验 robots.txt；
#   - 自动登记 data/raw/来源说明.md（站点 / URL / 采集时间 / 条数）。
set -euo pipefail

# 统一 Python 输出编码：Windows Git Bash / VS Code 终端为 UTF-8，
# 而 Python 默认按系统代码页（如 cp936）输出，会导致中文日志乱码。
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[get_data] 仓库根目录: $ROOT"
echo "[get_data] 目标目录:   data/raw/"
echo "[get_data] 采集时间:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 公开数据集分支：列出内置数据集
if [[ "${1:-}" == "--list-datasets" ]]; then
  exec python -m src.crawler.datasets --list
fi

# 公开数据集分支：下载指定数据集（下载 + SHA256 校验 + 来源登记）
if [[ "${1:-}" == "--dataset" ]]; then
  shift
  DATASET_NAME="${1:?用法: bash scripts/get_data.sh --dataset <数据集名称>（先用 --list-datasets 查看）}"
  shift
  DATASET_DIR="${DATASET_DIR:-data/raw/datasets}"
  dataset_args=(--download "$DATASET_NAME" --target-dir "$DATASET_DIR")
  if [[ "${FORCE:-0}" == "1" ]]; then
    dataset_args+=(--force)
  fi
  echo "[get_data] 运行: python -m src.crawler.datasets ${dataset_args[*]}"
  exec python -m src.crawler.datasets "${dataset_args[@]}"
fi

# 通用 HTML 采集分支
if [[ "${1:-}" == "--html" ]]; then
  shift
  URL="${1:?用法: bash scripts/get_data.sh --html <URL> --source <来源名>}"
  shift
  SOURCE="${SOURCE:-通用网页评论}"
  SELECTOR="${SELECTOR:-}"
  MAX_ITEMS="${MAX_ITEMS:-200}"
  args=(html --url "$URL" --source "$SOURCE" --max-items "$MAX_ITEMS" --delay "${DELAY:-1.5}")
  if [[ -n "$SELECTOR" ]]; then
    args+=(--selector "$SELECTOR")
  fi
  echo "[get_data] 运行: python -m src.crawler ${args[*]}"
  exec python -m src.crawler "${args[@]}"
fi

# B 站视频评论分支
BV="${1:-${BV:-}}"
if [[ -z "$BV" ]]; then
  echo "用法: bash scripts/get_data.sh <BV号或视频链接>" >&2
  echo "      bash scripts/get_data.sh --html <URL> --source <来源名>" >&2
  echo "      bash scripts/get_data.sh --list-datasets" >&2
  echo "      bash scripts/get_data.sh --dataset <数据集名称>" >&2
  exit 2
fi

PAGES="${PAGES:-2}"
MAX_SUB_PAGES="${MAX_SUB_PAGES:-2}"
MAX_ITEMS="${MAX_ITEMS:-200}"
DELAY="${DELAY:-2.5}"
MODE="${MODE:-time}"

args=(bilibili --bvid "$BV" --max-pages "$PAGES" --max-sub-pages "$MAX_SUB_PAGES"
      --max-items "$MAX_ITEMS" --delay "$DELAY" --mode "$MODE")
if [[ "${INCLUDE_REPLIES:-0}" == "1" ]]; then
  args+=(--include-replies)
fi

echo "[get_data] 运行: python -m src.crawler ${args[*]}"
python -m src.crawler "${args[@]}"

echo
echo "[get_data] 完成。CSV 位于 data/raw/<BV>_comments.csv；"
echo "[get_data] 来源登记见 data/raw/来源说明.md 与 data/raw/sources.json。"
echo "[get_data] 下游清洗示例："
echo "  python -m src.preprocessing --input data/raw/<BV>_comments.csv --mode clean"
echo "[get_data] 说明：本批 B 站评论无情感标签，训练前需先标注："
echo "  python -m src.crawler.labeling export --input data/raw/<BV>_comments.csv --output data/labeling/sample.csv --sample-size 500"
echo "[get_data] 公开带标签数据集：bash scripts/get_data.sh --list-datasets"
