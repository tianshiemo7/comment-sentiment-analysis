# API 契约（B 站视频评论分析）

> 本文件是**前后端唯一契约来源**。字段名一旦锁定，任何一侧都不得单方面改名。
> 改动流程：先改本文件 → 双方确认 → 再改代码。

服务基址：`http://localhost:8000`，前端开发服务器 `http://localhost:3000` 通过 Vite 代理
转发 `/api`、`/health` 到后端。所有路径前端的写法都是**同源相对路径**（如 `/api/videos`）。

---

## 一、状态语义

任务按流水线推进，`status` 为固定枚举：

| status | 左侧栏显示 | 右侧详情区 |
| --- | --- | --- |
| `queued` | 排队中（灰） | 「排队中…」 |
| `crawling` | 爬取中 + 进度条（蓝） | 「正在爬取评论… {progress}%」 |
| `processing` | 处理中 + 进度条（橙） | 「正在清洗与情感分析… {progress}%」 |
| `completed` | 已完成（绿） | 完整详情：图表 + 词云 + 评论 |
| `failed` | 失败（红，带原因） | `error` 字段内容 + 重试按钮 |

- `progress`：整数 0~100。
- `phases`：子步骤列表，用于在详情区显示细粒度进度；前端按数组渲染，**新增阶段不需要改前端**。

```json
"phases": [
  {"name": "采集评论", "status": "done"},
  {"name": "文本清洗", "status": "active"},
  {"name": "情感推理", "status": "pending"},
  {"name": "统计分析", "status": "pending"}
]
```

`phases[].status` 取值：`pending` | `active` | `done` | `failed`。

**未完成时详情怎么返回**：不报错，仍返回 HTTP 200，`analysis.status` 为
`crawling` / `processing`，并带当前 `progress`、`message`；其余数据字段留空，
具体形态固定为：

| 字段 | 未完成时 | 说明 |
| --- | --- | --- |
| `sentiment` | `null` | 尚未统计 |
| `trend` | `[]` | 空数组 |
| `keywords` | `{"positive": [], "negative": []}` | **结构保留**，两个数组为空 |
| `top_comments` | `{"positive": [], "negative": []}` | 同上 |
| `analysis.length_stats` | `null` | 尚未统计 |
| `model` | 对象 | 与具体视频无关的静态信息，始终返回 |
| `error` | `null` | 仅 `failed` 时填原因 |

前端据此渲染「处理中」。

---

## 二、接口清单

| # | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 1 | POST | `/api/videos` | 提交 BV 号，创建分析任务 |
| 2 | GET | `/api/videos` | 视频列表（左侧栏，轮询） |
| 3 | GET | `/api/videos/{bvid}/detail` | 视频详情（右侧详情区） |
| 4 | POST | `/api/videos/{bvid}/retry` | 失败任务重试 |
| 5 | DELETE | `/api/videos/{bvid}` | 删除任务（可选功能） |
| — | GET | `/health` | 健康检查（已存在） |

### 1. 提交 BV 号

```
POST /api/videos
Content-Type: application/json

{"bvid": "BV1xx411c7mD"}
```

成功 `200`：

```json
{"bvid": "BV1xx411c7mD", "status": "queued", "progress": 0,
 "created_at": "2026-10-16T14:30:00+08:00"}
```

错误：

| 状态码 | 场景 | 响应体 |
| --- | --- | --- |
| 400 | BV 号格式非法 | `{"detail": "BV 号格式不正确，应形如 BV1xx411c7mD"}` |
| 409 | 该 BV 号已在处理中 | `{"detail": "该视频已在分析队列中"}` |

BV 号校验规则（前后端一致）：正则 `^BV[0-9A-Za-z]{10}$`。

### 2. 视频列表

```
GET /api/videos
```

```json
{
  "total": 2,
  "videos": [
    {
      "bvid": "BV1xx411c7mD",
      "title": "某某视频标题",
      "owner": "UP主名",
      "cover_url": null,
      "status": "crawling",
      "progress": 43,
      "message": "正在爬取评论…",
      "phases": [{"name": "采集评论", "status": "active"}],
      "comment_count": 0,
      "created_at": "2026-10-16T14:30:00+08:00",
      "updated_at": "2026-10-16T14:30:12+08:00"
    }
  ]
}
```

字段说明：

- `title` / `owner` / `cover_url`：**尚未爬到时为 `null`**，前端需容忍（显示 BV 号占位）。
- `comment_count`：已入库评论数（任务进行中也可逐步增长）。
- 排序：建议按 `created_at` 倒序（最新在前）。

### 3. 视频详情

```
GET /api/videos/{bvid}/detail
```

```json
{
  "video": {
    "bvid": "BV1xx411c7mD",
    "title": "某某视频标题",
    "owner": "UP主名",
    "cover_url": null,
    "pubdate": "2026-09-01T12:00:00+08:00",
    "crawled_at": "2026-10-16T14:31:00+08:00",
    "comment_total": 8421
  },
  "analysis": {
    "status": "completed",
    "progress": 100,
    "message": "分析完成",
    "length_stats": {"avg_chars": 38.2, "max_chars": 200}
  },
  "sentiment": {
    "total": 8421,
    "positive": 5210, "negative": 2511, "neutral": 700,
    "positive_ratio": 0.619, "negative_ratio": 0.298, "neutral_ratio": 0.083
  },
  "trend": [
    {"bucket": "2026-09-01", "positive": 120, "negative": 45, "neutral": 10}
  ],
  "keywords": {
    "positive": [{"word": "好用", "count": 320, "weight": 1.0}],
    "negative": [{"word": "卡顿", "count": 210, "weight": 1.0}]
  },
  "model": {
    "name": "bilstm_attention",
    "accuracy": 0.912, "precision": 0.908, "recall": 0.915, "f1": 0.911
  },
  "top_comments": {
    "positive": [{"text": "很好用", "like": 520, "confidence": 0.97, "label": "positive"}],
    "negative": [{"text": "太卡了", "like": 310, "confidence": 0.94, "label": "negative"}]
  },
  "error": null,
  "placeholder": true
}
```

约定：

- `placeholder`：`true` 表示当前结果由**占位逻辑**生成（真实爬虫与模型尚未接入）；
  接入真实数据后置为 `false`，前端可据此显示「占位数据」提示。
- `sentiment.*_ratio` 为 0~1 小数，三者之和约等于 1。
- `trend[].bucket` 默认按天 `YYYY-MM-DD`，也可按小时 `YYYY-MM-DD HH:00`；前端按字符串直接作为横轴。
- `keywords.*[].weight` 为 0~1 归一化权重，**词云直接用 `count` 或 `weight` 渲染，后端不生成图片**。
- `model` 为当前生效模型的测试集指标（可复用 `outputs/stats/metrics_<model>.json`）。
- `top_comments` 每种情感取点赞数最高的若干条（建议 ≤ 10）。
- `analysis.status` 为 `failed` 时，`error` 给出失败原因，其余数据字段可为空。
- 未知 `bvid` 返回 `404` + `{"detail": "..."}`。

### 4. 重试失败任务

```
POST /api/videos/{bvid}/retry
```

成功 `200`：返回与「接口 1」相同结构的对象（`status` 重置为 `queued`）。
不存在返回 `404`。

### 5. 删除任务

```
DELETE /api/videos/{bvid}
```

成功 `200`：`{"deleted": true}`；不存在返回 `404`。

---

## 三、前端轮询策略（已定）

- 列表每 **3 秒**轮询一次 `GET /api/videos`。
- 当所有任务都处于 `completed` / `failed` 时**停止轮询**；提交新 BV 或点「重试」后恢复。
- 页面切到后台（`document.visibilitychange` 隐藏）时暂停轮询，回到前台立即拉一次。
- 详情区：选中任务后拉一次详情；若其 `analysis.status` 不是 `completed`/`failed`，
  则随列表轮询一起刷新（即详情也用 3 秒节奏），完成后停止。

---

## 四、mock 约定（后端实现前）

在 `frontend/` 内用 Vite 开发服务器中间件按本契约返回数据，位置 `frontend/mock/`：

- 提交 BV 后约 30 秒内走完 `queued → crawling → processing → completed`，进度真实递增。
- 预置 2 条演示数据（1 条已完成、1 条进行中），方便直接查看界面效果。
- 词频、趋势、代表性评论由 mock 生成，**不得写进业务组件**；
  真实后端就绪后只需移除 mock 插件（`vite.config.js` 一处），前端业务代码不改。

---

## 四点五、数据项目与单独评论（左栏三段中的前两段）

左栏共三段：**数据项目**（`projects`）、**单独评论**（`solo`）、**视频任务**（`videos`）。
视频任务见上文第二点；本节点定义前两段。

### 4.5.1 数据项目

**来源**：界面右上角「上传爬取数据」按钮 → 上传爬虫产出的 CSV/XLSX → 后端**落盘**
→ 自动按 `clean` 模式跑一遍 `src/preprocessing` 管线 → 产出可查看的表格与情感分析占位结果。

**标题**：上传时未指定名称时显示为「未自定义数据」。

存储布局（`PROJECTS_ROOT` 默认 `data/uploads/`）：

```
data/uploads/<project_id>/
├── upload.json                 上传元数据（原始文件名、大小、sha256、上传时间、显示名）
├── project.json                项目状态与阶段清单（对外主状态文件）
├── initial.csv                 初始数据（上传原件解析后的统一 CSV，UTF-8 BOM）
├── clean.csv                   筛选后-保留（清洗 + 分词后的评论）
├── excluded.csv                筛选后-排除（含排除原因列）
├── sentiment.csv               逐条情感（占位实现，含 label/confidence/probs）
└── analysis.json               聚合情感分析（占比、词频、长度分布）
```

#### 列表：`GET /api/artifacts`

```json
{
  "groups": [
    {"key": "projects", "label": "数据项目", "items": [ /* 项目对象 */ ]},
    {"key": "solo", "label": "单独评论", "items": [ /* 单条评论对象 */ ]},
    {"key": "videos", "label": "视频任务", "items": [ /* 视频对象，即 GET /api/videos 的 videos[] */ ]}
  ]
}
```

**项目对象**：

```json
{
  "id": "p_20261016_1530_a1b2c3",
  "name": "未自定义数据",
  "created_at": "2026-10-16T15:30:00+08:00",
  "status": "completed",
  "progress": 100,
  "message": "处理完成",
  "sources": [
    {"filename": "BV1UB4y1W7Xu_comments.csv", "rows": 2394, "size": 812345}
  ],
  "stages": [
    {"key": "initial",  "label": "初始数据",   "rows": 2394, "columns": 12},
    {"key": "clean",    "label": "筛选后结果", "rows": 1850, "columns": 14},
    {"key": "excluded", "label": "排除记录",   "rows": 49,   "columns": 4}
  ],
  "sentiment": {"total": 1850, "positive": 1024, "negative": 751, "neutral": 75},
  "method": "rule_placeholder",
  "error": null
}
```

`status` 取值：`queued` | `processing` | `completed` | `failed`。
`method` 取值：`rule_placeholder`（当前占位）或具体模型名（如 `bilstm_attention`）。
`stages[].columns` 为该表列数；`rows` 为数据行数（不含表头）。

#### 上传：`POST /api/artifacts/upload`

`multipart/form-data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `files` | File（可多个） | `.csv` / `.txt` / `.tsv` / `.xlsx`，单个 ≤ 20 MB，合计 ≤ 50 MB |
| `name` | str（可选） | 项目显示名；为空则用「未自定义数据」 |

成功 `200`：返回**项目对象**（此时通常 `status="queued"`，随后由处理线程推进）。

错误：`400` 不支持的格式 / 文件为空；`413` 超过大小上限。

#### 状态：`GET /api/artifacts/{project_id}`

返回**项目对象**（同列表项）；不存在返回 `404`。

#### 触发处理：`POST /api/artifacts/{project_id}/analyze`

请求体可省略；`{"mode": "auto"}` 为默认（自动判定，无标签 → `clean`）。

成功 `200`：返回项目对象（`status` 重置为 `queued`/`processing`）。
不存在的项目返回 `404`。

> 上传后后端**自动**触发一次处理；本接口用于「重新处理」。

#### 表格：`GET /api/artifacts/{project_id}/table?stage=clean&offset=0&limit=100`

- `stage` ∈ `initial` | `clean` | `excluded`（默认 `clean`）；
- `limit` 上限 500，默认 100。

```json
{
  "stage": "clean",
  "columns": ["text", "label", "tokens", "text_raw", "comment_id"],
  "rows": [["这个商品质量很好", "positive", "这个 商品 质量 很好", "这个商品质量很好！", "9527"]],
  "total_rows": 1850,
  "offset": 0,
  "limit": 100,
  "encoding": "utf-8-sig"
}
```

#### 逐条评论情感（可展开）：`GET /api/artifacts/{project_id}/comments?offset=0&limit=50&label=`

`label` 可选，取值 `positive` | `negative` | `neutral`（不传为全部）。

```json
{
  "total": 1850,
  "offset": 0,
  "limit": 50,
  "items": [
    {"index": 0, "text": "这个商品质量很好", "label": "positive",
     "confidence": 0.8084, "probs": {"positive": 0.8084, "negative": 0.1916}}
  ]
}
```

> `items[].text` 取**原文**（`text_raw`），与批量预测接口的语义一致（见第六节）。

#### 聚合情感分析：`GET /api/artifacts/{project_id}/analysis`

```json
{
  "method": "rule_placeholder",
  "total": 1850,
  "sentiment": {"positive": 1024, "negative": 751, "neutral": 75,
                "positive_ratio": 0.5535, "negative_ratio": 0.4059, "neutral_ratio": 0.0405},
  "keywords": {"positive": [{"word": "好用", "count": 320, "weight": 1.0}],
               "negative": [{"word": "卡顿", "count": 210, "weight": 1.0}]},
  "length": {"mean": 27.9, "median": 24, "p90": 61, "max": 209},
  "sample": [{"index": 0, "text": "这个商品质量很好", "label": "positive", "confidence": 0.8084}]
}
```

`method` 必须如实反映来源：当前为 `rule_placeholder`，前端据此显示「规则占位」标识。

#### 打开 CSV：`POST /api/artifacts/{project_id}/open`

**在服务器本机**用系统默认程序打开该项目的 CSV（Windows 用 `os.startfile`，
macOS `open`，Linux `xdg-open`）：

```json
// 请求（可省略，默认打开 clean 阶段）
{"stage": "clean"}
// 成功 200
{"opened": true, "path": "D:\\...\\data\\uploads\\<id>\\clean.csv"}
```

错误：`400` 该阶段文件不存在 / 平台不支持；`404` 项目不存在。

#### 删除：`DELETE /api/artifacts/{project_id}`

成功 `200`：`{"deleted": true}`（同时删除该项目目录）；不存在返回 `404`。

### 4.5.2 单独评论（solo comments）

粒度和视频任务相当，**集中存放所有单条评论**；因为每条评论都是"单独的"，所以
**详情区只渲染原文 + 极性徽章 + 置信度 + 概率条，不渲染任何图表**。

存储：`data/uploads/solo_comments.json`（数组）。

**单条评论对象**：

```json
{
  "id": "c_20261016_1531_9f8e7d",
  "text": "这个商品质量很好",
  "label": "positive",
  "confidence": 0.8084,
  "probs": {"positive": 0.8084, "negative": 0.1916},
  "method": "rule_placeholder",
  "created_at": "2026-10-16T15:31:00+08:00"
}
```

#### 保存：`POST /api/solo-comments`

```json
// 请求
{"text": "这个商品质量很好", "label": "positive", "confidence": 0.8084,
 "probs": {"positive": 0.8084, "negative": 0.1916}, "method": "rule_placeholder"}
// 成功 200：返回单条评论对象
```

- 由前端「单条预测」页的**「保存到左栏」开关**调用（默认开启）；
- `label` / `confidence` / `probs` 由前端把 `POST /api/predict` 的结果一并提交，
  因此**必须与 predict 的返回一致**；后端做基本校验（label 枚举、confidence ∈ [0,1]）；
- 文本为空或仅空白返回 `400`。

#### 列表：`GET /api/solo-comments?offset=0&limit=50`

```json
{"total": 12, "offset": 0, "limit": 50, "items": [ /* 单条评论对象，按 created_at 倒序 */ ]}
```

#### 删除：`DELETE /api/solo-comments/{id}` 与批量 `DELETE /api/solo-comments?ids=id1,id2`

成功 `200`：`{"deleted": true, "deleted_count": 2}`；不存在返回 `404`。

### 4.5.3 安全约定（必须遵守）

- `table` / `comments` / `analysis` / `open` **只接受服务端生成的 `id` 与固定枚举 `stage`**，
  对外**绝不接受任何文件路径**；
- `open` 额外限定：扩展名必须是 `.csv`，解析后的真实路径必须位于
  `data/uploads/` 或 `data/processed/` 之内（`resolve()` 后复核父目录），否则 `400`；
- 上传文件名只取 basename，禁止路径成分；解析后的内容按统一 CSV 落盘，不直接使用用户文件名做路径。

### 4.5.4 与既有接口的关系

`POST /api/predict`、`POST /api/predict/batch`、`/api/videos*` **保持不变**；
单条评论只是新增了一条"把预测结果存起来"的路径，批量预测接口语义不变
（详见第六节）。

---

## 五、与既有接口的关系

原「单条预测 / 批量预测」接口（`POST /api/predict`、`POST /api/predict/batch`、
`GET /api/models`、批量下载）**保持不变**，用于课程要求的单条与批量预测演示；
本次新增的 `/api/videos*` 系列是 B 站视频评论分析的主流程。两者互不影响。
