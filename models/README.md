# models/ — 模型权重存放目录

本目录用于存放**训练产物**。权重文件默认被 `.gitignore` 排除（`*.pt` / `*.bin` 等），
2026-10-30 随提交 `1eb77a7` 以「一次性快照」方式强制入库（总体约定见 [`../data/README.md`](../data/README.md)）；
本轮交付又把**真实训练权重**回填到同一快照路径（`.gitignore` 规则未改）。

## 当前入库权重（2026-10-30，本机 CPU 复核）

| 路径 | 内容 | 大小 |
| --- | --- | ---: |
| `checkpoints/textcnn/best.pt` | TextCNN 最佳权重（best epoch=2，val_acc 0.8808） | 6,074,780 B |
| `checkpoints/bilstm_attention/best.pt` | BiLSTM-Attention 最佳权重（best epoch=3，val_acc 0.8798） | 6,736,317 B |
| `word2vec/word2vec.model` | gensim Word2Vec 词向量（Skip-gram、128 维、13,797 句 / 483,303 词元） | 10,277,760 B |
| `word2vec/embedding_matrix.npy` | 对齐训练词表的 float32 `[9753,128]` 嵌入矩阵 | 4,993,664 B |
| `word2vec/word2vec_params.json` | 词向量超参（window=5、min_count=3、sg=1、negative=5、epochs=10、seed=42） | 267 B |

- 数据版本 `753bc2014c69bdc1`（pipeline 1.1.0，train / val / test = 13,797 / 1,971 / 3,943，词表 9,753）；
- 测试集真实指标：TextCNN Acc 0.8960 / P 0.9060 / R 0.8700 / F1 0.8876 / MacroF1 0.8954；
  BiLSTM-Attention Acc 0.9008 / P 0.9181 / R 0.8673 / F1 0.8920 / MacroF1 0.9002；
- 权重 SHA256：TextCNN `2b379bedb47a4e6ff6d04e3ad3645c13f79b914e56d388c9defc2e7a9983b17b`，
  BiLSTM-Attention `715a604ae5e029326846e1c32558a7939f4deac40a85e8efa56dbbe61a588b63`
  （完整实验记录见 `outputs/stats/training_experiment_20261030.json` 与各模型 `experiment.json`）；
- 每个模型目录含 `config.json`（结构超参）、`metadata.json`（最佳轮次与验证指标）、
  `experiment.json`（数据 / 环境 / 源码 / 权重 / 预处理快照哈希）与 `preprocessing/`
  （`vocab.json` / `preprocessor.json` / `label_map.json` / `manifest.json` 与训练时一致）。
- GPU（RTX 4060）历史实验：TextCNN 测试 Acc 89.09%、BiLSTM 89.25%（best epoch=4），
  作为**历史对照**记录在训练文档中；仓库当前入库权重/指标是 **CPU 复核**那一份。

## 推理侧约定

- `ACTIVE_MODEL` 默认 `bilstm_attention`，`INFERENCE_DEVICE` 默认 `cpu`（可 `auto` / `cuda`），
  见 [`.env.example`](../.env.example) 与 [`src/inference/README.md`](../src/inference/README.md)；
- 单条 `/api/predict`、批量 `/api/predict/batch`、`src/videos/` 视频情感阶段、`src/artifacts/`
  数据项目情感阶段**四个入口**都走 `src/inference/service.py` 的同一个 `Predictor`
  （`get_predictor()` = `load_model_cached(ACTIVE_MODEL)`）；
- `GET /api/models` 的指标来源为 `outputs/stats/metrics_<模型>.json`；`ready=true` 表示检查点存在，
  是否真正加载成功由 `src/inference/service.py:load_model` 的返回值决定；
- 检查点缺失 / torch 未安装 / 权重损坏时降级 `predict_rule_based`，响应 `method=rule_placeholder`。

## 复现

```bash
# ① 数据（固定公开语料，数据版本 753bc2014c69bdc1）
python -m scripts.prepare_training_data

# ② 词向量（仅用训练集，128 维）
python -m src.embeddings --train-file data/splits/train.csv --vocab data/processed/vocab.json \
  --out models/word2vec --embed-dim 128 --window 5 --min-count 3 --epochs 10 --workers 1 --seed 42

# ③ 两个模型（CPU 复核；GPU 环境把 --device 换成 cuda）
python -m src.training.cli --model textcnn --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
python -m src.training.cli --model bilstm_attention --embeddings models/word2vec/embedding_matrix.npy \
  --epochs 20 --batch-size 64 --lr 0.001 --max-len 200 --seed 42 --device cpu --patience 3
```

训练命令会更新同名 `best.pt`、`outputs/stats/metrics_<模型>.json` 与出图；每轮实验应配套记录
数据版本、随机种子（`SEED`）、超参和权重文件名，避免混用。`bert/` 目前只有目录占位、没有权重
（BERT 微调不在短期范围内）。具体实验记录见
[模型训练与评估](../docs/模型训练与评估-2026-10-30.md)。
