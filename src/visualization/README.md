# src/visualization —— ⑦ 可视化模块

> 对应需求：损失 / 准确率曲线（训练 vs 验证）、混淆矩阵热力图、正负词云各一张、
> （加分）Attention 权重可视化；图表统一输出到 `outputs/figures/`，报告第 6 章引用。
> 路线图阶段四；验收项 `docs/验收检查清单.md` 五、可视化。

## 产物清单（文件名固定，报告直接引用）

| 文件 | 内容 | 生成函数 |
| --- | --- | --- |
| `outputs/figures/loss_curve.png` | 训练 / 验证损失曲线 | `curves.plot_loss_curve` |
| `outputs/figures/accuracy_curve.png` | 训练 / 验证准确率曲线 | `curves.plot_accuracy_curve` |
| `outputs/figures/confusion_matrix_<model>.png` | 混淆矩阵热力图 | `matrices.plot_confusion_matrix` |
| `outputs/figures/wordcloud_positive.png` | 正面评论词云 | `wordclouds.plot_wordcloud` |
| `outputs/figures/wordcloud_negative.png` | 负面评论词云 | `wordclouds.plot_wordcloud` |
| `outputs/figures/attention_heatmap.png` | Attention 权重（加分项） | `matrices.plot_attention_heatmap` |

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 训练历史（每 epoch 的 train/val loss、acc） | `outputs/logs/train_*.json` |
| 输入 | 混淆矩阵与词频统计 | `src/evaluation`、`src/preprocessing` |
| 输出 | PNG 图表（建议 dpi=150，中文标题用 `SimHei`/`Microsoft YaHei`） | `outputs/figures/` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `curves.py` | 损失 / 准确率曲线 |
| `matrices.py` | 混淆矩阵热力图、Attention 热力图 |
| `wordclouds.py` | 正负词云 |

## 实现状态（已完成）

- [x] `curves.py:plot_loss_curve` / `plot_accuracy_curve` —— 训练 vs 验证双折线；缺少 `train_acc` 时只画验证集
- [x] `curves.py:load_history` —— 读取训练日志 JSON 的 `history`
- [x] `matrices.py:plot_confusion_matrix` —— seaborn heatmap，缺 seaborn 时退回 imshow + 手动标注；支持按行归一化
- [x] `matrices.py:plot_attention_heatmap` —— Attention 权重 Top-K 横向条形图（加分项）
- [x] `wordclouds.py:word_frequencies` / `plot_wordcloud` / `plot_wordclouds_by_label` —— 正负词云，自动探测中文字体

训练 CLI（默认 `--no-figures` 可关闭）会把曲线、混淆矩阵、正负词云写入 `outputs/figures/`：
曲线同时产出 `loss_curve.png`（最新一次）与 `loss_curve_<model>.png`（按模型保留）。

## 关联

- 路线图：`docs/开发路线图.md` 阶段四第 4 步
- 上游：`src/training`、`src/evaluation`
- 一键脚本：`scripts/train.sh`（训练后调用出图）