# notebooks —— 实验记录与探索性分析

> 对应需求：报告第 3 章「数据清洗与探索性分析」（文本长度分布、标签分布、高频词）。
> 路线图阶段一。

## 约定

1. Notebook 只做**探索与出图**，可复用的逻辑必须回写到 `src/` 对应模块（避免「只在
   notebook 里跑过」的不可复现代码）；
2. 每个 notebook 顶部注明：目的、输入数据路径、运行前置命令、产出图表路径；
3. 执行后可提交（体积小的 `.ipynb`），但**不要提交大数据集**（见 `.gitignore`）；
4. 图表统一保存到 `outputs/figures/`，命名与 `src/visualization/README.md` 一致。

## 计划清单

| 文件 | 目的 | 输入 | 产出 |
| --- | --- | --- | --- |
| `01_eda.ipynb` | 数据概况与探索性分析 | `data/sample/*.csv`、`data/splits/train.csv` | 长度分布图、标签分布、高频词表 |
| `02_训练过程分析.ipynb`（待建） | 读取训练日志画曲线 | `outputs/logs/train_*.json` | 复用 `src/visualization` 出图 |
| `03_错误分析.ipynb`（待建） | 典型错误样例剖析（反讽 / 转折 / 否定） | `outputs/predictions/error_cases_*.csv` | 报告第 6 章素材 |

## 关联

- 路线图：`docs/开发路线图.md` 阶段一
- 上游：`src/preprocessing`；出图：`src/visualization`
