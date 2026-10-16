"""`/api/artifacts`（数据项目）与 `/api/solo-comments`（单独评论）的实现包。

包内模块分工（与 `src/videos/` 保持同一套分层，便于对照阅读）：

| 模块 | 职责 |
| --- | --- |
| :mod:`~src.artifacts.schemas` | 契约的代码化：枚举、ID 规则、请求 / 响应模型 |
| :mod:`~src.artifacts.store` | 落盘存储：`data/uploads/<project_id>/` 与 `solo_comments.json`（线程安全 + 原子写） |
| :mod:`~src.artifacts.config` | 流水线阶段时长等可配置项（环境变量覆盖） |
| :mod:`~src.artifacts.errors` | 业务异常（HTTP 层映射为 400 / 404 / 413） |
| :mod:`~src.artifacts.messages` | 状态文案（中文，与前端展示一致） |
| :mod:`~src.artifacts.pipeline` | 后台流水线：`clean` 管线 → 占位情感 → 聚合统计 |
| :mod:`~src.artifacts.preview` | 只读解析 CSV（分页表格、逐条评论、阶段行列数） |
| :mod:`~src.artifacts.opener` | 本机打开 CSV（`os.startfile` / `open` / `xdg-open`，可注入假实现） |
| :mod:`~src.artifacts.service` | 业务服务：项目 CRUD、单线程队列、启动恢复、单独评论 CRUD |

**占位声明（诚实标注）**：本包的情感结果全部来自
`src.inference.service.predict_rule_based` —— 一个规则 / 词典**占位**预测器，
**不是训练模型**；对外 `method` 字段固定为 `"rule_placeholder"`。
"""

from __future__ import annotations

from .schemas import METHOD_RULE_PLACEHOLDER, STAGE_KEYS

__all__ = ["METHOD_RULE_PLACEHOLDER", "STAGE_KEYS"]
