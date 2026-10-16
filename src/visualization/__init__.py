"""⑦ 可视化模块：曲线 / 混淆矩阵热力图 / 词云 / Attention 热力图。

所有绘图库（matplotlib / seaborn / wordcloud）均在函数内部导入，
因此未安装绘图依赖时 `import src.visualization` 仍然可用。
"""

from .curves import plot_accuracy_curve, plot_loss_curve
from .matrices import plot_attention_heatmap, plot_confusion_matrix
from .wordclouds import plot_wordcloud, plot_wordclouds_by_label

__all__ = [
    "plot_loss_curve",
    "plot_accuracy_curve",
    "plot_confusion_matrix",
    "plot_attention_heatmap",
    "plot_wordcloud",
    "plot_wordclouds_by_label",
]
