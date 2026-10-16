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

## 五、与既有接口的关系

原「单条预测 / 批量预测」接口（`POST /api/predict`、`POST /api/predict/batch`、
`GET /api/models`、批量下载）**保持不变**，用于课程要求的单条与批量预测演示；
本次新增的 `/api/videos*` 系列是 B 站视频评论分析的主流程。两者互不影响。
