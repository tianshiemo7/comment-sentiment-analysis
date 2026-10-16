"""网站用户评论情感分析系统 —— Python 主干代码包。

本包按 `docs/开发路线图.md` 的六个阶段划分为 8 个子包：

| 子包 | 职责 | 路线图阶段 |
| --- | --- | --- |
| `crawler` | ① 数据采集（公开数据集 / 爬虫） | 阶段一 |
| `preprocessing` | ② 清洗、分词、词表、统计、划分 | 阶段一 |
| `embeddings` | ③ 词向量（Word2Vec / GloVe） | 阶段二 |
| `models` | ④ 模型（TextCNN / BiLSTM-Attention） | 阶段三 |
| `training` | ⑤ 训练与验证循环 | 阶段三 |
| `evaluation` | ⑥ 指标评估（P / R / F1 / 混淆矩阵） | 阶段四 |
| `visualization` | ⑦ 结果可视化（曲线 / 热力图 / 词云 / Attention） | 阶段四 |
| `inference` | ⑧ FastAPI 推理服务（:8000） | 阶段五 |

当前为**骨架阶段**：函数签名、数据结构与文档契约已就绪，算法实现待按路线图逐步填充。
约定：所有需要 PyTorch 的导入一律放在函数内部或 `try/except` 中，
因此未安装 torch 的环境也能 `import src.*` 并跑通 `tests/`。
"""

__version__ = "0.1.0"
