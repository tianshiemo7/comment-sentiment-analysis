# src/evaluation —— ⑥ 评估模块

> 对应需求：测试集上给出 Accuracy / Precision / Recall / F1 对比表，混淆矩阵 + 错误分析。
> 路线图阶段四；报告第 6 章。

## 职责

1. 计算二分类指标：Accuracy / Precision / Recall / F1（`precision_recall_fscore_support`）；
2. 输出混淆矩阵 ``[[TN, FP], [FN, TP]]``（顺序与标签 ``[negative, positive]`` 对齐）；
3. 汇总多模型对比表 → `outputs/stats/model_comparison.csv` 与 `.json`；
4. 错误分析：把预测错误的样本（原文 / 真实标签 / 预测标签 / 置信度）导出，便于报告中举例。

## 指标定义（报告需给出）

.. math::

   \\text{Accuracy} = \\frac{TP + TN}{TP + TN + FP + FN}, \\quad
   \\text{Precision} = \\frac{TP}{TP + FP}, \\quad
   \\text{Recall} = \\frac{TP}{TP + FN}, \\quad
   F_1 = \\frac{2PR}{P + R}

其中正类取 ``positive``；由于数据基本平衡，宏平均与正类指标接近，两者都记录。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 真实标签、预测标签（可选预测概率） | 训练/评估流程 |
| 输出 | 指标 JSON `metrics_<model>.json` | `outputs/stats/` |
| 输出 | 模型对比表 `model_comparison.csv` | `outputs/stats/` |
| 输出 | 错误分析明细 `error_cases_<model>.csv` | `outputs/predictions/` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `metrics.py` | 指标、混淆矩阵、错误分析 |
| `report.py` | 对比表与 JSON 落盘 |

## 待实现清单（TODO）

- [ ] `metrics.py:compute_metrics` —— accuracy / precision / recall / f1（宏平均 + 正类）
- [ ] `metrics.py:confusion_matrix` —— 2×2 混淆矩阵与 TN/FP/FN/TP 明细
- [ ] `metrics.py:error_analysis` —— 错误样本导出（含置信度）
- [ ] `report.py:compare_models` —— 多模型指标汇总成对比表
- [ ] `report.py:save_metrics_table` / `save_metrics_json` —— 落盘

## 关联

- 路线图：`docs/开发路线图.md` 阶段四
- 上游：`src/training`；下游：`src/visualization`（混淆矩阵热力图）、`src/inference`（`/api/models` 指标）
