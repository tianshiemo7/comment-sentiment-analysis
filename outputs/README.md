# outputs/ — 产物输出目录

| 子目录 | 内容 | 对应课程要求 |
| --- | --- | --- |
| `figures/` | 图表：`loss_curve.png`、`accuracy_curve.png`、`confusion_matrix_cnn.png`、`wordcloud_positive.png`、`wordcloud_negative.png`、`attention_heatmap.png` | 结果可视化四项要求 |
| `logs/` | 训练日志（每个模型一份，含每轮 loss / acc） | 实验分析 |
| `predictions/` | 批量预测导出结果 `batch_prediction_YYYYMMDD_HHMMSS.csv` | 批量预测并导出 |
| `stats/` | 数据统计 `dataset_stats.json`：词表规模、平均文本长度、标签分布 | 预处理统计完整性 |

文件默认不入库（可由 `scripts/` 重新生成），但**报告中引用的最终图表建议一并提交**，
提交前请在此说明生成命令与对应模型。
