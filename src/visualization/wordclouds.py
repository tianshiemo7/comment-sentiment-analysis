"""词云骨架：正面 / 负面评论词云各一张。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

FIGURES_DIR = Path("outputs") / "figures"


def _require_wordcloud():
    """导入 ``wordcloud.WordCloud``；缺失时抛出带安装提示的 ImportError。"""
    try:
        from wordcloud import WordCloud  # noqa: PLC0415 - 函数内导入
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "词云需要 wordcloud，请执行：python -m pip install wordcloud"
        ) from exc
    return WordCloud


def word_frequencies(tokenized_texts: Iterable[Sequence[str]], top_n: int = 200) -> dict[str, int]:
    """统计词频，返回 ``{word: count}``（取前 ``top_n`` 个）。

    TODO(实现): ``Counter`` 累加全部词，按频次降序取前 ``top_n``。
    """
    raise NotImplementedError("TODO: 尚未实现词频统计")


def plot_wordcloud(
    frequencies: Mapping[str, int],
    out_path: str | Path,
    *,
    title: str = "词云",
    font_path: str | None = None,
    background_color: str = "white",
) -> Path:
    """按词频绘制词云并保存 PNG，返回保存路径。

    Args:
        frequencies: ``{词: 频次}`` 字典。
        out_path: 输出路径，如 ``outputs/figures/wordcloud_positive.png``。
        title: 图标题。
        font_path: 中文字体文件路径（Windows 常用
            ``C:/Windows/Fonts/msyh.ttc``）；``None`` 时尝试自动探测，
            找不到会画出方块，需在报告中说明。
        background_color: 背景色。

    TODO(实现):
        1. :func:`_require_wordcloud`，``WordCloud(font_path=..., width=1600, height=900,
           background_color=...)`` + ``generate_from_frequencies``；
        2. 用 matplotlib 展示并叠加标题（同样需要中文字体），保存后关闭。
    """
    raise NotImplementedError("TODO: 尚未实现词云绘制")


def plot_wordclouds_by_label(
    records: Sequence,
    out_dir: str | Path = FIGURES_DIR,
    *,
    top_n: int = 200,
) -> dict[str, Path]:
    """按标签分别生成正负词云。

    Args:
        records: :class:`~src.preprocessing.dataset.Record` 列表（需含 ``label`` 与 ``tokens``）。
        out_dir: 输出目录，默认 ``outputs/figures/``。
        top_n: 每张词云取前多少高频词。

    Returns:
        ``{"positive": Path, "negative": Path}``

    TODO(实现): 按 ``label`` 分组 → :func:`word_frequencies` → :func:`plot_wordcloud`，
    文件名固定 ``wordcloud_positive.png`` / ``wordcloud_negative.png``。
    """
    raise NotImplementedError("TODO: 尚未实现正负词云对比")
