"""爬虫命令行总入口：``python -m src.crawler <bilibili|html> [选项]``。

示例：

.. code-block:: bash

    # B 站视频评论（遵守 robots + 限速，默认 2.5s/次）
    python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2

    # B 站视频评论（机器可读输出）
    python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 1 --json

    # 采集完成后查看来源登记
    cat data/raw/来源说明.md
"""

from __future__ import annotations

import sys


def _print_help() -> None:
    print(
        "用法：python -m src.crawler <命令> [选项]\n"
        "\n"
        "命令：\n"
        "  bilibili   采集 B 站视频评论（WBI 签名 / 风控退避 / 断点续爬）\n"
        "  html       采集通用 HTML 页面评论（robots 校验 / 限速 / 重试）\n"
        "\n"
        "独立子命令（各自 --help）：\n"
        "  python -m src.crawler.datasets   公开带标签数据集下载 / 统一格式\n"
        "  python -m src.crawler.labeling   标注候选导出 / 标注合并校验\n"
        "\n"
        "查看子命令帮助：\n"
        "  python -m src.crawler bilibili --help\n"
        "  python -m src.crawler html --help\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return 0

    command, rest = args[0], args[1:]
    if command in ("bilibili", "bili"):
        from .bilibili import main as run

        return run(rest)
    if command in ("html", "spider"):
        from .spider import main as run

        return run(rest)
    print(f"未知命令：{command}", file=sys.stderr)
    _print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
