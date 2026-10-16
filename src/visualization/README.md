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

## 待实现清单（TODO）

- [ ] `curves.py:plot_loss_curve` / `plot_accuracy_curve` —— 双折线 + 图例 + 训练/验证对比
- [ ] `matrices.py:plot_confusion_matrix` —— seaborn heatmap，标注数值与坐标轴标签
- [ ] `matrices.py:plot_attention_heatmap` —— 词 × 权重条形/热力图（加分项）
- [ ] `wordclouds.py:plot_wordcloud` —— 词频字典 → 词云图，中文字体需显式指定
- [ ] `wordclouds.py:plot_wordclouds_by_label` —— 按正负标签分别统计词频并各出一张

> 中文乱码排查：matplotlib 未指定中文字体时会出现方块，需
> ``plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]`` 且 ``axes.unicode_minus=False``。

## 关联

- 路线图：`docs/开发路线图.md` 阶段四第 4 步
- 上游：`src/training`、`src/evaluation`
- 一键脚本：`scripts/train.sh`（训练后调用出图）
