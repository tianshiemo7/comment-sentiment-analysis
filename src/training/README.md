# src/training —— ⑤ 训练模块

> 对应需求：两种深度模型训练与对比；训练过程可复现（种子、超参、日志齐全）。
> 路线图阶段三；验收项 `docs/验收检查清单.md` 四、模型与实验。

## 职责

1. 提供统一的训练 / 验证循环，TextCNN 与 BiLSTM-Attention 共用；
2. 早停（`EarlyStopping`）、保存最优检查点 `models/checkpoints/<model>/best.pt`；
3. 训练日志写入 `outputs/logs/train_<model>_<时间戳>.json`（每 epoch 的
   `train_loss / val_loss / val_acc / lr`），供 `src/visualization` 画曲线；
4. 固定随机种子（`SEED=42`），并记录完整超参，保证实验可复现。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 训练/验证集（id 序列 + 标签） | `data/splits/train.csv`、`val.csv` |
| 输入 | 词表 | `data/processed/vocab.json` |
| 输入 | 超参 | `TrainConfig` / `.env.example` |
| 输出 | 训练曲线数据（每 epoch 指标） | `outputs/logs/train_*.json` |
| 输出 | 最优检查点 | `models/checkpoints/<model>/best.pt` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `config.py` | `TrainConfig` 超参容器（batch_size / epochs / lr / patience 等） |
| `trainer.py` | `train_model` 训练主循环、`EarlyStopping`、`set_seed` |
| `cli.py` | `python -m src.training.cli --model textcnn` 命令行入口 |

## 待实现清单（TODO）

- [ ] `trainer.py:set_seed` —— 同时固定 `random` / `numpy` / `torch`（含 cuDNN 确定性）
- [ ] `trainer.py:build_dataloader` —— 把 `Record` 转成 `TensorDataset` + `DataLoader`
- [ ] `trainer.py:train_one_epoch` —— 单轮训练，返回平均 loss
- [ ] `trainer.py:evaluate` —— 验证集 loss 与 accuracy
- [ ] `trainer.py:train_model` —— 主循环：早停、保存 best、写日志 JSON
- [ ] `cli.py:main` —— argparse 解析数据集/模型/超参并调用 `train_model`

## 关联

- 路线图：`docs/开发路线图.md` 阶段三
- 上游：`src/preprocessing`、`src/embeddings`、`src/models`
- 下游：`src/evaluation`、`src/visualization`
- 一键脚本：`scripts/train.sh`
