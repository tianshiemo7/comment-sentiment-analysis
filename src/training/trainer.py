"""训练主循环：早停、保存 best 检查点、写训练日志。

对应需求：训练过程可复现（种子、超参、日志齐全）；早停与 best 检查点。
本模块顶层不导入 torch（见 `src/models/base.py:require_torch`）。
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..preprocessing.processor import LABEL_MAP
from .config import TrainConfig


def set_seed(seed: int = 42) -> None:
    """固定所有随机源，保证实验可复现。"""
    random.seed(seed)
    try:
        import numpy as np  # noqa: PLC0415 - 可选依赖，缺失时只固定 Python 随机源

        np.random.seed(seed)
    except ImportError:  # pragma: no cover - 最小依赖环境
        pass
    try:
        import torch  # noqa: PLC0415 - 函数内导入，保证无 torch 也能 import 本模块
    except ImportError:
        return
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


@dataclass
class EarlyStopping:
    """早停与最优检查点记录（默认监控验证准确率，越大越好）。

    Attributes:
        patience: 连续多少轮验证指标未提升即停止。
        min_delta: 视为提升的最小变化量。
        best_score: 历史最优验证指标。
        counter: 未提升计数。
        best_epoch: 最优轮次。
        best_state: 最优轮次的模型权重（state_dict 深拷贝）。
        should_stop: 是否应停止训练。
    """

    patience: int = 3
    min_delta: float = 1e-4
    best_score: float = float("-inf")
    counter: int = 0
    best_epoch: int = 0
    best_state: Any = None
    should_stop: bool = False

    def step(self, score: float, epoch: int, model_state: Any = None) -> bool:
        """比较本轮 score 与历史最优，返回是否「有提升」。"""
        if score > self.best_score + self.min_delta:
            self.best_score = float(score)
            self.best_epoch = int(epoch)
            self.best_state = copy.deepcopy(model_state) if model_state is not None else None
            self.counter = 0
            return True
        self.counter += 1
        if self.counter >= self.patience:
            self.should_stop = True
        return False


def _require_torch():
    from ..models.base import require_torch  # noqa: PLC0415 - 延迟导入

    return require_torch()


def _model_module(model: Any):
    """取到底层 nn.Module：包装类先 build，已是 nn.Module 的直接返回。"""
    net = getattr(model, "net", None)
    if net is not None:
        return net
    build = getattr(model, "build", None)
    if callable(build):
        return build()
    return model


def _forward_logits(model: Any, input_ids: Any, attention_mask: Any = None):
    """统一两种模型的前向调用；BiLSTM-Attention 额外吃 attention_mask。"""
    if getattr(model, "model_type", "") == "bilstm_attention":
        output = model.forward(input_ids, attention_mask=attention_mask)
    else:
        output = model.forward(input_ids)
    if isinstance(output, tuple):
        output = output[0]
    return output


def _select_device(config: TrainConfig) -> Any:
    torch = _require_torch()
    if config.device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if config.device == "cuda" and not torch.cuda.is_available():
        raise ValueError("device=cuda 但当前环境没有可用 GPU，请改用 --device cpu")
    return torch.device(config.device)


def build_dataloader(records: list, config: TrainConfig, *, shuffle: bool, vocab=None):
    """把 Record 列表转成 ``DataLoader``（id 序列 + 掩码 + 有效长度 + 标签）。

    records 需已带分词结果（``record.tokens``，由预处理管线产出）。
    """
    if vocab is None:
        raise ValueError("build_dataloader 需要词表 vocab（data/processed/vocab.json）")
    if not records:
        raise ValueError("数据集为空，无法构建 DataLoader")
    torch = _require_torch()
    from ..preprocessing.tokenize import encode_ids  # noqa: PLC0415 - 延迟导入

    input_ids, masks, lengths, labels = [], [], [], []
    for record in records:
        if record.label not in LABEL_MAP:
            raise ValueError(f"标签不合法：{record.label!r}，只支持 {tuple(LABEL_MAP)}")
        tokens = record.tokens.split() if isinstance(record.tokens, str) else list(record.tokens or [])
        if not tokens:
            raise ValueError(f"记录缺少分词结果：{str(record.text)[:30]!r}，请先运行预处理管线")
        ids = encode_ids(tokens, vocab, config.max_len)
        length = min(len(tokens), config.max_len)
        input_ids.append(ids)
        masks.append([1] * length + [0] * (config.max_len - length))
        lengths.append(length)
        labels.append(LABEL_MAP[record.label])

    dataset = torch.utils.data.TensorDataset(
        torch.tensor(input_ids, dtype=torch.long),
        torch.tensor(masks, dtype=torch.long),
        torch.tensor(lengths, dtype=torch.long),
        torch.tensor(labels, dtype=torch.long),
    )
    return torch.utils.data.DataLoader(
        dataset,
        batch_size=config.batch_size,
        shuffle=shuffle,
        num_workers=config.num_workers,
    )


def _device_of(module: Any) -> Any:
    torch = _require_torch()
    try:
        return next(module.parameters()).device
    except StopIteration:  # pragma: no cover - 空模型不应出现
        return torch.device("cpu")


def _unpack_batch(batch):
    input_ids, attention_mask, lengths, labels = batch
    return input_ids, attention_mask, lengths, labels


def train_one_epoch(model: Any, loader: Any, optimizer: Any, criterion: Any, config: TrainConfig) -> float:
    """训练一轮，返回按样本数加权的平均训练损失。"""
    torch = _require_torch()
    module = _model_module(model)
    module.train()
    device = _device_of(module)
    total_loss, total = 0.0, 0
    for step, batch in enumerate(loader, 1):
        input_ids, attention_mask, _lengths, labels = _unpack_batch(batch)
        input_ids = input_ids.to(device)
        attention_mask = attention_mask.to(device)
        labels = labels.to(device)
        optimizer.zero_grad()
        logits = _forward_logits(model, input_ids, attention_mask)
        loss = criterion(logits, labels)
        loss.backward()
        if config.grad_clip and config.grad_clip > 0:
            torch.nn.utils.clip_grad_norm_(module.parameters(), config.grad_clip)
        optimizer.step()
        total_loss += float(loss.item()) * input_ids.size(0)
        total += input_ids.size(0)
        if config.log_every and step % config.log_every == 0:
            print(f"[train] step {step} loss={loss.item():.4f}")
    if not total:
        raise ValueError("DataLoader 为空，无法训练")
    return total_loss / total


def evaluate(model: Any, loader: Any, criterion: Any, config: TrainConfig) -> dict[str, float]:
    """验证 / 测试评估，返回 ``{"loss": float, "accuracy": float}``。"""
    result = predict_loader(model, loader, criterion=criterion)
    return {"loss": result["loss"], "accuracy": result["accuracy"]}


def predict_loader(model: Any, loader: Any, *, criterion: Any = None) -> dict[str, Any]:
    """在给定 DataLoader 上做一次完整前向，返回评估所需的全部原始结果。

    Returns:
        ``{"loss", "accuracy", "y_true", "y_pred", "probabilities", "attention"}``：
        ``probabilities`` 是正类概率列表，``attention`` 为 BiLSTM 的注意力列表（没有则为 None）。
    """
    torch = _require_torch()
    module = _model_module(model)
    module.eval()
    device = _device_of(module)
    total_loss, total, correct = 0.0, 0, 0
    y_true: list[int] = []
    y_pred: list[int] = []
    probabilities: list[float] = []
    attentions: list[list[float]] = []
    with torch.no_grad():
        for batch in loader:
            input_ids, attention_mask, _lengths, labels = _unpack_batch(batch)
            input_ids = input_ids.to(device)
            attention_mask = attention_mask.to(device)
            labels = labels.to(device)
            output = (
                model.forward(input_ids, attention_mask=attention_mask)
                if getattr(model, "model_type", "") == "bilstm_attention"
                else model.forward(input_ids)
            )
            if isinstance(output, tuple):
                logits, attention = output[0], output[1]
                attentions.extend(attention.detach().cpu().tolist())
            else:
                logits = output
            if criterion is not None:
                total_loss += float(criterion(logits, labels).item()) * input_ids.size(0)
            probs = torch.softmax(logits, dim=1)
            predicted = probs.argmax(dim=1)
            total += input_ids.size(0)
            correct += int((predicted == labels).sum().item())
            y_true.extend(labels.detach().cpu().tolist())
            y_pred.extend(predicted.detach().cpu().tolist())
            probabilities.extend(probs[:, 1].detach().cpu().tolist())
    if not total:
        raise ValueError("DataLoader 为空，无法评估")
    return {
        "loss": (total_loss / total) if criterion is not None else 0.0,
        "accuracy": correct / total,
        "y_true": y_true,
        "y_pred": y_pred,
        "probabilities": probabilities,
        "attention": attentions or None,
    }


def train_model(model: Any, train_loader: Any, val_loader: Any, config: TrainConfig, vocab=None) -> dict:
    """完整训练流程（早停 + best 检查点 + 训练日志）。

    Returns:
        ``{"history", "best_epoch", "best_score", "log_path", "checkpoint_path"}``。
    """
    from ..models.base import save_checkpoint  # noqa: PLC0415 - 延迟导入

    config.validate()
    set_seed(config.seed)
    torch = _require_torch()
    device = _select_device(config)
    module = _model_module(model)
    module.to(device)
    optimizer = torch.optim.Adam(
        module.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    criterion = torch.nn.CrossEntropyLoss()
    stopper = EarlyStopping(patience=config.patience, min_delta=config.min_delta)
    history: list[dict] = []
    checkpoint_path: Path | None = None
    log_path = log_path_for(config.model_name, config.log_dir)

    for epoch in range(1, config.epochs + 1):
        train_loss = train_one_epoch(model, train_loader, optimizer, criterion, config)
        validation = evaluate(model, val_loader, criterion, config)
        learning_rate = float(optimizer.param_groups[0]["lr"])
        history.append(
            {
                "epoch": epoch,
                "train_loss": round(train_loss, 6),
                "val_loss": round(validation["loss"], 6),
                "val_acc": round(validation["accuracy"], 6),
                "lr": learning_rate,
            }
        )
        print(
            f"[train] epoch {epoch}/{config.epochs} "
            f"train_loss={train_loss:.4f} val_loss={validation['loss']:.4f} "
            f"val_acc={validation['accuracy']:.4f}"
        )
        improved = stopper.step(validation["accuracy"], epoch, module.state_dict())
        if improved:
            state = {
                "model_state": module.state_dict(),
                "config": config.to_dict(),
                "vocab": vocab.to_dict() if hasattr(vocab, "to_dict") else None,
                "epoch": epoch,
                "metrics": {
                    "val_loss": validation["loss"],
                    "val_acc": validation["accuracy"],
                },
                "model_name": config.model_name,
                "model_type": getattr(model, "model_type", config.model_name),
                "hyperparams": getattr(model, "hyperparams", {}),
            }
            checkpoint_path = save_checkpoint(
                state, Path(config.checkpoint_dir) / config.model_name / "best.pt"
            )
        if stopper.should_stop:
            print(f"[train] 早停：验证集连续 {config.patience} 轮未提升，停止训练")
            break

    if stopper.best_state is not None:
        module.load_state_dict(stopper.best_state)

    payload = {
        "model": config.model_name,
        "config": config.to_dict(),
        "history": history,
        "best_epoch": stopper.best_epoch,
        "best_score": stopper.best_score,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "labels": ["negative", "positive"],
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "history": history,
        "best_epoch": stopper.best_epoch,
        "best_score": stopper.best_score,
        "log_path": str(log_path),
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
    }


def log_path_for(model_name: str, log_dir: str | Path = "outputs/logs") -> Path:
    """生成训练日志路径 ``train_<model>_<时间戳>.json``。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(log_dir) / f"train_{model_name}_{stamp}.json"
