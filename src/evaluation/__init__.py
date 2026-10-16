"""⑥ 评估模块：Accuracy / Precision / Recall / F1 / 混淆矩阵与错误分析。

入口：`metrics.py`（指标计算）、`report.py`（对比表落盘）。
sklearn 只在函数内部导入，缺失时给出安装提示。
"""

from .metrics import compute_metrics, confusion_matrix, error_analysis
from .report import compare_models, save_metrics_json, save_metrics_table

__all__ = [
    "compute_metrics",
    "confusion_matrix",
    "error_analysis",
    "compare_models",
    "save_metrics_table",
    "save_metrics_json",
]
