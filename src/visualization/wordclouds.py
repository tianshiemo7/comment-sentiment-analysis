"""词云：正面 / 负面评论词云各一张。"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

FIGURES_DIR = Path("outputs") / "figures"
SPECIAL_TOKENS = {"<pad>", "<unk>"}

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
        counter.update(token for token in tokens if token and token not in SPECIAL_TOKENS)
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
    frequencies = {str(word): int(count) for word, count in frequencies.items() if word and int(count) > 0}
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
