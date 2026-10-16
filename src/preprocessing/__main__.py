"""python -m src.preprocessing --input data/raw/comments.csv"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .clean import RESOURCES_DIR
from .dataset import load_records
from .pipeline import detect_processing_mode, prepare_comments, prepare_dataset
from .processor import TextPreprocessor
from .tokenize import DEFAULT_MAX_LEN, DEFAULT_STOPWORDS_PATH, DEFAULT_USERDICT_PATH


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="接收爬虫 CSV，清洗待预测评论；或制备已标注训练集")
    parser.add_argument("--input", required=True, help="爬虫输出或已标注的 CSV/TSV/XLSX 文件")
    parser.add_argument("--mode", choices=["auto", "clean", "train"], default="auto",
                        help="默认自动识别：无标签时清洗待预测评论，有标签时制备训练数据")
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--text-column")
    parser.add_argument("--sheet-name")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh")
    parser.add_argument("--max-len", type=int, default=DEFAULT_MAX_LEN)
    parser.add_argument("--min-freq", type=int, default=3)
    parser.add_argument("--max-vocab-size", type=int, default=30000, help="普通词数量上限，不含 PAD/UNK")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ratios", type=float, nargs=3, default=(.7, .1, .2))
    parser.add_argument("--group-by", default="auto", help="auto 自动使用 video_id；none 明确采用非分组划分")
    parser.add_argument("--stopwords", default=str(DEFAULT_STOPWORDS_PATH))
    parser.add_argument("--userdict", default=str(DEFAULT_USERDICT_PATH))
    parser.add_argument("--emoji-map", default=str(RESOURCES_DIR / "emoji_map.json"))
    parser.add_argument("--allow-small-dataset", action="store_true", help="调试模式，记录但不阻断课程规模及分布检查")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        processor = TextPreprocessor.from_resources(lang=args.lang, max_len=args.max_len,
                    stopwords_path=args.stopwords, userdict_path=args.userdict, emoji_map_path=args.emoji_map)
        mode = args.mode
        if mode == "auto":
            records = load_records(args.input, text_column=args.text_column, sheet_name=args.sheet_name, keep_empty=True)
            mode = detect_processing_mode(records)
        common = dict(output_root=args.output_root, processor=processor, text_column=args.text_column,
                      sheet_name=args.sheet_name, overwrite=args.overwrite)
        if mode == "clean":
            result = prepare_comments(args.input, **common)
        else:
            result = prepare_dataset(args.input, **common, seed=args.seed, ratios=tuple(args.ratios),
                        group_key=args.group_by, min_freq=args.min_freq, max_vocab_size=args.max_vocab_size,
                        allow_small_dataset=args.allow_small_dataset)
    except (ValueError, OSError, ImportError, json.JSONDecodeError) as exc:
        print(f"数据处理失败：{exc}", file=sys.stderr)
        return 1
    stats = result["stats"]
    summary = {"mode": mode, "output_root": result["output_root"],
               "dataset_version": result["manifest"]["dataset_version"],
               "accepted": stats["total"], "excluded": stats["excluded_rows"]}
    if mode == "clean":
        summary.update(next_step="sentiment_prediction", output_csv="data/processed/comments_cleaned.csv",
                       vocab_fitted=False)
    else:
        summary.update(vocab_size=stats["vocab_size"],
                       splits={name: value["total"] for name, value in stats["splits"].items()},
                       course_requirements_met=stats["course_requirements_met"],
                       issues=stats["course_check_issues"], warnings=stats["warnings"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
