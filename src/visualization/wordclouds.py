"""词云：正面 / 负面评论词云各一张。"""

from __future__ import annotations

import logging
import os
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .keywords import SPECIAL_TOKENS, is_display_keyword

logger = logging.getLogger(__name__)

FIGURES_DIR = Path("outputs") / "figures"

#: 常见中文字体候选（Windows / Linux / macOS），按顺序探测
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _require_wordcloud():
    """导入 ``wordcloud.WordCloud``；缺失时抛出带安装提示的 ImportError。"""
    try:
        from wordcloud import WordCloud  # noqa: PLC0415 - 函数内导入
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "词云需要 wordcloud，请执行：python -m pip install wordcloud"
        ) from exc
    return WordCloud


def detect_cjk_font() -> str | None:
    """探测可用的中文字体路径；找不到返回 None（词云会显示方块，报告需说明）。"""
    env_font = os.getenv("WORDCLOUD_FONT")
    if env_font and Path(env_font).is_file():
        return env_font
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _setup_matplotlib():
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise ImportError(
            "绘图需要 matplotlib，请执行：python -m pip install matplotlib"
        ) from exc
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def word_frequencies(tokenized_texts: Iterable[Sequence[str]], top_n: int = 200) -> dict[str, int]:
    """统计词频，返回 ``{word: count}``（按频次降序取前 top_n 个）。"""
    counter: Counter = Counter()
    for text in tokenized_texts:
        tokens = text.split() if isinstance(text, str) else list(text)
        counter.update(token for token in tokens if is_display_keyword(token))
    if top_n is not None and top_n >= 0:
        return dict(counter.most_common(top_n))
    return dict(counter)


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
        frequencies: ``{词: 频次}`` 字典（一般来自 :func:`word_frequencies`）。
        out_path: 输出路径，如 ``outputs/figures/wordcloud_positive.png``。
        font_path: 中文字体文件路径；``None`` 时自动探测（见 :func:`detect_cjk_font`）。
    """
    frequencies = {str(word): int(count) for word, count in frequencies.items()
                   if is_display_keyword(str(word)) and int(count) > 0}
    if not frequencies:
        raise ValueError("没有可绘制词云的有效词频数据")
    WordCloud = _require_wordcloud()
    font = font_path or detect_cjk_font()
    cloud = WordCloud(
        font_path=font,
        width=1600,
        height=900,
        background_color=background_color,
        random_state=42,
        collocations=False,
    )
    cloud.generate_from_frequencies(frequencies)

    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(12, 7))
    ax.imshow(cloud, interpolation="bilinear")
    ax.axis("off")
    ax.set_title(title, fontsize=16)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _tokens_of(record) -> list[str]:
    tokens = getattr(record, "tokens", None)
    if isinstance(tokens, str):
        return tokens.split()
    return list(tokens or [])


def plot_wordclouds_by_label(
    records: Sequence,
    out_dir: str | Path = FIGURES_DIR,
    *,
    top_n: int = 200,
) -> dict[str, Path]:
    """按标签分别生成正负词云，返回 ``{"positive": Path, "negative": Path}``。

    records 为 :class:`~src.preprocessing.dataset.Record` 列表（需含 ``label`` 与 ``tokens``）。
    """
    grouped: dict[str, list[list[str]]] = {"positive": [], "negative": []}
    for record in records:
        label = getattr(record, "label", None)
        if label not in grouped:
            continue
        tokens = _tokens_of(record)
        if tokens:
            grouped[label].append(tokens)
    empty = [label for label, items in grouped.items() if not items]
    if empty:
        names = "、".join("正面" if label == "positive" else "负面" for label in empty)
        raise ValueError(f"缺少{names}评论的分词结果，无法生成词云")

    out_dir = Path(out_dir)
    result: dict[str, Path] = {}
    titles = {"positive": "正面评论词云", "negative": "负面评论词云"}
    for label, tokenized in grouped.items():
        frequencies = word_frequencies(tokenized, top_n=top_n)
        result[label] = plot_wordcloud(
            frequencies, out_dir / f"wordcloud_{label}.png", title=titles[label]
        )
    return result


# --------------------------------------------------------------------------- #
# 按需渲染（不依赖 matplotlib）：接口用法见 `src/inference/app.py` 的图片端点
# --------------------------------------------------------------------------- #
class WordcloudDependencyError(ImportError):
    """wordcloud 库缺失（HTTP 503，detail 带安装指引）。"""


#: 情感 → 配色（正面绿色系、负面红色系）
TONE_COLORMAPS: dict[str, str] = {"positive": "Greens", "negative": "Reds"}


def render_frequencies_to_png(
    frequencies: Mapping[str, int],
    out_path: str | Path,
    *,
    tone: str = "positive",
    size: tuple[int, int] = (960, 600),
) -> Path:
    """用 ``wordcloud`` 库把 ``{词: 频次}`` 渲染成白底 PNG（原子写盘，返回路径）。

    只做**图片渲染**（``WordCloud.to_file``），不引入 matplotlib；正面用绿色系、
    负面用红色系；CJK 字体缺失时仍能出图（文字为方块，这里记录 warning）。
    先写 ``<stem>.tmp.png`` 再 :func:`os.replace` 原子替换，避免并发读到半截文件。

    Args:
        frequencies: ``{词: 频次}``（一般来自词云数据 ``keywords[tone]``）。
        out_path: 输出 PNG 路径（父目录不存在时自动创建）。
        tone: ``positive`` / ``negative``（配色用；非法值按正面配色兜底）。
        size: 图片尺寸 ``(宽, 高)``。

    Raises:
        ValueError: 有效词（通过 :func:`is_display_keyword` 且频次 > 0）不足 2 个。
        WordcloudDependencyError: ``wordcloud`` 未安装（HTTP 503）。
    """
    cleaned = {
        str(word): int(count)
        for word, count in frequencies.items()
        if is_display_keyword(str(word)) and int(count) > 0
    }
    if len(cleaned) < 2:
        raise ValueError("可绘制的有效词不足 2 个")
    try:
        from wordcloud import WordCloud  # noqa: PLC0415 - 函数内导入，缺依赖时降级为 503
    except ImportError as exc:  # pragma: no cover - 取决于本地环境
        raise WordcloudDependencyError(
            "Python 词云渲染需要 wordcloud，请执行：python -m pip install wordcloud"
        ) from exc

    width, height = int(size[0]), int(size[1])
    font = detect_cjk_font()
    if font is None:
        logger.warning("未找到中文字体，Python 词云可能显示方块；可设置 WORDCLOUD_FONT")
    cloud = WordCloud(
        font_path=font,
        width=width,
        height=height,
        background_color="white",
        colormap=TONE_COLORMAPS.get(tone, "Greens"),
        random_state=42,
        collocations=False,
        max_words=200,
        prefer_horizontal=1.0,
        margin=4,
    )
    cloud.generate_from_frequencies(cleaned)

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    try:
        cloud.to_file(str(tmp))
        os.replace(tmp, path)
    finally:
        if tmp.exists():  # 渲染失败时清掉半截临时文件
            tmp.unlink(missing_ok=True)
    return path


__all__ = [
    "WordcloudDependencyError",
    "detect_cjk_font",
    "plot_wordcloud",
    "plot_wordclouds_by_label",
    "render_frequencies_to_png",
    "word_frequencies",
]
