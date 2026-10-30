"""Read-only experiment figures; public IDs map to a fixed set of PNG files."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIGURES_DIR = REPO_ROOT / "outputs/figures"
STATS_DIR = REPO_ROOT / "outputs/stats"
CHECKPOINT_DIR = REPO_ROOT / "models/checkpoints"
MODEL_LABELS = {"textcnn": "TextCNN", "bilstm_attention": "BiLSTM-Attention"}


@dataclass(frozen=True)
class FigureSpec:
    id: str
    filename: str
    title: str
    category: str
    description: str
    scope: str = "model"


def _model_specs(model: str) -> list[FigureSpec]:
    return [
        FigureSpec(f"{model}-loss", f"loss_curve_{model}.png", "损失曲线", "training",
                   "训练集与验证集的损失随轮次变化。"),
        FigureSpec(f"{model}-accuracy", f"accuracy_curve_{model}.png", "准确率曲线", "training",
                   "比较训练和验证准确率，观察模型收敛与过拟合。"),
        FigureSpec(f"{model}-confusion", f"confusion_matrix_{model}.png", "混淆矩阵", "evaluation",
                   "行表示真实类别，列表示预测类别，顺序为负面、正面。"),
    ]


SHARED_SPECS = [
    FigureSpec("wordcloud-positive", "wordcloud_positive.png", "正面评论词云", "wordcloud",
               "训练语料中正面评论的高频词分布，两种模型共用。", "shared"),
    FigureSpec("wordcloud-negative", "wordcloud_negative.png", "负面评论词云", "wordcloud",
               "训练语料中负面评论的高频词分布，两种模型共用。", "shared"),
]
ATTENTION_SPEC = FigureSpec("bilstm_attention-attention", "attention_heatmap.png", "注意力权重", "attention",
                            "可选实验：查看词语对 BiLSTM-Attention 预测的贡献。")
FIGURE_SPECS = {spec.id: spec for model in MODEL_LABELS for spec in _model_specs(model)}
FIGURE_SPECS.update({spec.id: spec for spec in [*SHARED_SPECS, ATTENTION_SPEC]})


def resolve_figure_path(figure_id: str) -> Path:
    """Reject arbitrary names, links, directories and absent artifacts alike."""
    spec = FIGURE_SPECS.get(figure_id)
    if spec is not None:
        candidate = FIGURES_DIR / spec.filename
        if (not candidate.is_symlink() and candidate.is_file()
                and candidate.resolve().parent == FIGURES_DIR.resolve()):
            return candidate.resolve()
    raise FileNotFoundError("图表不存在或尚未生成")


def _read_object(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def get_visualizations(model: str = "textcnn") -> dict:
    if model not in MODEL_LABELS:
        raise ValueError("不支持的模型，请选择 textcnn 或 bilstm_attention")
    payload = _read_object(STATS_DIR / f"metrics_{model}.json")
    stored_metrics = payload.get("metrics", {})
    if not isinstance(stored_metrics, dict):
        stored_metrics = {}
    metrics = {}
    for key in ("accuracy", "precision", "recall", "f1"):
        value = stored_metrics.get(key)
        metrics[key] = (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
                        and 0 <= value <= 1 and math.isfinite(value) else None)
    metadata = _read_object(CHECKPOINT_DIR / model / "metadata.json")
    epoch = metadata.get("epoch")
    manifest = _read_object(CHECKPOINT_DIR / model / "preprocessing/manifest.json")
    version = manifest.get("dataset_version")
    figures = []
    specs = [*_model_specs(model), *SHARED_SPECS]
    if model == "bilstm_attention":
        specs.append(ATTENTION_SPEC)
    for spec in specs:
        item = {"id": spec.id, "title": spec.title, "category": spec.category, "scope": spec.scope,
                "description": spec.description, "available": False, "image_url": None,
                "download_url": None, "updated_at": None}
        try:
            stat = resolve_figure_path(spec.id).stat()
        except OSError:
            pass
        else:
            url = f"/api/visualizations/figures/{spec.id}"
            revision = f"{stat.st_mtime_ns}-{stat.st_size}"
            item.update(available=True, image_url=f"{url}?v={revision}",
                        download_url=f"{url}?download=true&v={revision}",
                        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat())
        figures.append(item)
    return {"model": model, "model_label": MODEL_LABELS[model], "metrics": metrics,
            "best_epoch": epoch if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch > 0 else None,
            "dataset_version": version if isinstance(version, str) and version else None, "figures": figures}
