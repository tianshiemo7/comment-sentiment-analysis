# data/uploads/ — 界面上传的「数据项目」

本目录存放界面右上角「上传爬取数据」按钮传入的数据及其处理产物，**运行期自动生成，不入库**
（见根目录忽略规则）。单独评论集中存放在 `solo_comments.json`。

## 目录结构

```
data/uploads/
├── solo_comments.json          单独评论（左栏②段）集中存储
└── <project_id>/               每个数据项目一个目录
    ├── upload.json             上传元数据：原始文件名、大小、sha256、上传时间、显示名
    ├── project.json            项目状态与阶段清单（对外主状态文件）
    ├── initial.csv             ① 初始数据：上传原件解析后的统一 CSV
    ├── clean.csv               ② 筛选后结果：清洗 + 分词后的评论（保留 text_raw 原文）
    ├── excluded.csv            ③ 排除记录：含排除原因列
    ├── sentiment.csv           逐条情感（原文 + label + confidence + probs）
    └── analysis.json           聚合情感：占比、词频、长度分布
```

## 处理链路

上传 → 落盘为 `initial.csv` → 复用 `src/preprocessing` 的 **clean 模式**管线
（清洗 / 分词 / 去重 / 排除原因）→ 产出 `clean.csv` 与 `excluded.csv`
→ 复用 `src/inference/service.py:predict_rule_based` 逐条预测 → `sentiment.csv` 与 `analysis.json`。

> ⚠️ **情感结果是占位**：`method` 字段为 `rule_placeholder`，来源是 140 词规则/词典预测器，
> **不是训练出来的深度学习模型**。界面据此显示「规则占位」标识；接入真实模型后自动切换为模型名。

## 相关接口

见 `docs/API契约.md` 第四点五节：`/api/artifacts`（列表）、`/api/artifacts/upload`、
`/api/artifacts/{id}/stages|table|comments|analysis|open|analyze`、`DELETE /api/artifacts/{id}`，
以及 `/api/solo-comments`。

## 安全约定

对这些接口只传服务端生成的 `id` 与固定 `stage` 枚举，**不接受任何文件路径**；
`open` 仅允许 `.csv` 且限定在本目录与 `data/processed/` 之内。
