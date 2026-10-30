# API 契约（B 站视频评论分析）

## 实验可视化扩展（2026-10-30）

新增只读接口，不改变原有预测、视频、数据项目契约。

- `GET /api/visualizations?model=textcnn`：默认模型 textcnn；另支持 bilstm_attention。
  不支持的模型返回 400。即使没有生成图表也返回 200，并逐项标记 available=false。
- `GET /api/visualizations/figures/{figure_id}`：返回指定 PNG（inline）；
  加 `?download=true` 返回附件。未知 ID、缺失文件、目录或符号链接返回 404。
  只允许固定实验图表，不接受文件路径，不挂载整个 outputs 目录。

```json
{
  "model": "textcnn",
  "model_label": "TextCNN",
  "dataset_version": "753bc2014c69bdc1",
  "best_epoch": 2,
  "metrics": {"accuracy": 0.8960, "precision": 0.9060, "recall": 0.8700, "f1": 0.8876},
  "figures": [
    {
      "id": "textcnn-loss",
      "title": "损失曲线",
      "category": "training",
      "scope": "model",
      "description": "训练集与验证集的损失随轮次变化。",
      "available": true,
      "image_url": "/api/visualizations/figures/textcnn-loss?v=版本标识",
      "download_url": "/api/visualizations/figures/textcnn-loss?download=true&v=版本标识",
      "updated_at": "2026-10-30T06:52:00+00:00"
    }
  ]
}
```

示例 figures 仅列一项；实际固定返回所选模型的 loss / accuracy / confusion，以及训练语料共享的
wordcloud-positive / wordcloud-negative；BiLSTM-Attention 额外包含可选的 bilstm_attention-attention。
category 为 training / evaluation / wordcloud / attention，scope 为 model / shared。
没有图片时 image_url、download_url、updated_at 为 null；元信息缺失时 dataset_version、best_epoch
为 null，metrics 中缺失/无效的指标为 null，不生成模拟值。metrics 来自对应模型已有评估文件，
dataset_version 来自该模型的配套资源 manifest。image_url 的 v 来自文件修改时间与大小，刷新后更新。
图表清单不缓存；图片响应要求重新验证缓存，以便训练重跑后刷新页面读取新产物。
当前公开实验为酒店/外卖语料，指标和语料词云不代表 B 站评论领域的评估结果。

> 本文件是**前后端唯一契约来源**。字段名一旦锁定，任何一侧都不得单方面改名。
> 改动流程：先改本文件 → 双方确认 → 再改代码。

服务基址：`http://localhost:8000`，前端开发服务器 `http://localhost:3000` 通过 Vite 代理
转发 `/api`、`/health` 到后端。所有路径前端的写法都是**同源相对路径**（如 `/api/videos`）。

情感类别口径：监督训练固定为正负二分类（`negative=0、positive=1、num_classes=2`）。
下文响应示例及筛选条件中的 `neutral` 保留给当前规则推理未命中正负词时的占位结果
（真实模型推理只输出 positive/negative，不会产生 neutral），
不是已训练模型的第三类，也不是低置信度预测的默认归类。训练入口排除 neutral/uncertain/irrelevant，
不把模型预测结果回灌为人工标签。真实二分类模型已接入推理服务（加载失败时回退规则占位，
`method` 如实标注）；低置信度需另行定义复核状态。

---

## 一、状态语义

任务按流水线推进，`status` 为固定枚举：

| status | 左侧栏显示 | 右侧详情区 |
| --- | --- | --- |
| `queued` | 排队中（灰） | 「排队中…」 |
| `crawling` | 爬取中 + 进度条（蓝） | 「正在爬取评论… {progress}%」 |
| `processing` | 处理中 + 进度条（橙） | 「正在清洗与情感分析… {progress}%」 |
| **`crawled`** | **待清洗**（琥珀，等待操作） | 「采集完成，等待清洗」+「开始清洗」按钮 |
| **`cleaned`** | **待分析**（紫，等待操作） | 「清洗完成，等待情感分析」+「开始分析」按钮 |
| `completed` | 已完成（绿） | 完整详情：图表 + 词云 + 评论 |
| `failed` | 失败（红，带原因） | `error` 字段内容 + 重试按钮 |

- `progress`：整数 0~100。
- `phases`：子步骤列表，用于在详情区显示细粒度进度；前端按数组渲染，**新增阶段不需要改前端**。
- **`crawled` / `cleaned` 为 2026-10-17 新增的等待态**：不自动轮询，需用户点「开始清洗 / 开始分析」
  （`POST /api/videos/{bvid}/continue`）继续，或在设置页开启 `auto_clean` / `auto_analyze` 自动跨越。
  其余状态语义不变；旧客户端可忽略这两个新枚举。
- 处于 `crawled` 时 `phases` 为「采集评论=done，其余 pending」；`cleaned` 时
  「采集评论=done、文本清洗=done，情感推理/统计分析=pending」。

```json
"phases": [
  {"name": "采集评论", "status": "done"},
  {"name": "文本清洗", "status": "active"},
  {"name": "情感推理", "status": "pending"},
  {"name": "统计分析", "status": "pending"}
]
```

`phases[].status` 取值：`pending` | `active` | `done` | `failed`。

**未完成时详情怎么返回**：不报错，仍返回 HTTP 200，`analysis.status` 为当前状态
（`queued` / `crawling` / `processing` / `crawled` / `cleaned`），并带当前 `progress`、`message`；
其余数据字段留空，具体形态固定为：

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
| 1 | POST | `/api/videos` | 提交 BV 号，创建分析任务（可带 `settings` 启用分步流水线） |
| 2 | GET | `/api/videos` | 视频列表（左侧栏，轮询） |
| 3 | GET | `/api/videos/{bvid}/detail` | 视频详情（右侧详情区，含 `pipeline` 字段） |
| 4 | POST | `/api/videos/{bvid}/retry` | 失败任务重试 |
| 5 | DELETE | `/api/videos/{bvid}` | 删除任务（可选功能） |
| 6 | GET | `/api/settings` | 读取系统设置（B 站 Cookie 脱敏） |
| 7 | PUT | `/api/settings` | 保存系统设置（部分更新） |
| 8 | POST | `/api/crawler/session/check` | 检查 B 站登录态（手动 Cookie 兜底时校验） |
| 9 | POST | `/api/videos/{bvid}/continue` | 等待态任务继续下一阶段（`crawled`→清洗、`cleaned`→分析） |
| 10 | POST | `/api/account/login/start` | 打开人工登录窗口并启动 Cookie 捕获（幂等） |
| 11 | GET | `/api/account/login/status` | 轮询人工登录窗口状态（前端 2 秒一次） |
| 12 | POST | `/api/account/login/cancel` | 取消本次登录并关闭窗口 |
| — | GET | `/health` | 健康检查（已存在） |

> 第 6~12 项为 2026-10-17 新增，详见本文件「二点五、设置与分步流水线」；第 1 项请求体向后兼容。

### 1. 提交 BV 号

```
POST /api/videos
Content-Type: application/json

{"bvid": "BV1xx411c7mD"}
```

可选带 `settings` 启用分步流水线（省略 = 旧行为，见下方兼容说明）：

```jsonc
{
  "bvid": "BV1xx411c7mD",
  "settings": {
    "account": {"mode": "anonymous"},          // 只传模式；Cookie 永远取服务端设置
    "crawl": {"real_crawl": true, "max_pages": 2, "include_replies": true},
    "pipeline": {"auto_clean": false, "auto_analyze": false}
  }
}
```

`settings` 与已保存设置**部分合并**后作为本任务快照（不含 Cookie）写入任务记录，之后修改设置
不影响已提交任务。响应结构不变：

```json
{"bvid": "BV1xx411c7mD", "status": "queued", "progress": 0,
 "created_at": "2026-10-16T14:30:00+08:00"}
```

错误：

| 状态码 | 场景 | 响应体 |
| --- | --- | --- |
| 400 | BV 号格式非法 | `{"detail": "BV 号格式不正确，应形如 BV1xx411c7mD"}` |
| 400 | `settings` 字段越界 / 枚举非法 | `{"detail": "crawl.max_pages 必须在 0~50 之间"}` 等，带具体字段与范围 |
| 409 | 该 BV 号已在处理中 | `{"detail": "该视频已在分析队列中"}` |

> **兼容性**：不带 `settings` 的请求（`{"bvid": "..."}`）与旧版行为完全一致 —— 一次跑完
> 四个阶段、不暂停；新行为只在 `settings` 存在时启用。

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
  "pipeline": {
    "account_mode": "anonymous",
    "auto_clean": false,
    "auto_analyze": false,
    "next_action": null,
    "crawl": {"real_crawl": true, "max_pages": 1, "max_sub_pages": 1,
              "page_size": 20, "include_replies": false, "sort_mode": "time",
              "max_items": 200, "delay_seconds": 2.5, "timeout_seconds": 15,
              "require_login": false, "init_buvid": false}
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

- `placeholder`：`true` 表示当前 detail 的**评论数据**由占位逻辑生成
  （默认 `crawl.real_crawl=false` 时的确定性伪随机数据；真实爬虫接入后置为 `false`，
  前端可据此显示「占位数据」提示）。**该字段只标注评论数据来源**：情感推理已接入真实模型
  （加载失败时回退规则占位），预测来源由 `method` 字段如实标注
  （`POST /api/predict` 与数据项目接口；视频任务见内部产物 `analyze.json` 的 `method`，
  不新增详情响应字段）。
- **顶层 `pipeline`**（2026-10-17 新增，additive，未完成时也返回）：`account_mode`
  （`anonymous` / `account`）、`auto_clean`、`auto_analyze`、`next_action`
  （`null` / `"clean"` / `"analyze"`）与本任务的 `crawl` 参数快照。
  `next_action="clean"` 时对应 `status="crawled"`，`"analyze"` 对应 `status="cleaned"`。
- `analysis.status` 使用新状态枚举；`analysis.message` 为「采集完成，等待清洗」/
  「清洗完成，等待情感分析」等中文提示。
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

## 二点五、设置与分步流水线（2026-10-17）

> 本节为 2026-10-17 新增接口与字段的**唯一契约**。后端实现见 `src/videos/settings.py`
> （设置存储/校验）、`src/videos/browser_login.py`（人工登录窗口）与 `pipeline.py` /
> `service.py`（分步状态机）；前端组件为 `components/SettingsDialog.vue`、
> `components/CrawlLaunchDialog.vue`。
> 原接口与不带 `settings` 的旧请求体**保持完全兼容**（旧行为零回归）。

### 2.5.1 设置模型与存储

- 落盘文件：`outputs/settings.json`（默认；已被 `.gitignore` 忽略，**切勿提交**），
  可用环境变量 `CSA_SETTINGS_PATH` 覆盖；原子写入，写盘失败不影响 HTTP 响应。
- 文件不存在 / 非法或损坏 → **按默认值返回**，不报错。
- Cookie 明文**只存在该文件**；任何接口响应与日志都不得含完整 Cookie。
- 完整结构（`GET` / `PUT` 响应用同一结构；带「只读」的字段不接受客户端修改）：

```jsonc
{
  "account": {
    "mode": "anonymous",        // anonymous | account
    "has_cookie": false,        // 只读：服务端是否已保存凭据
    "masked_cookie": "",        // 只读：形如 "SESSD…9f2a"
    "uname": "",                // 只读：最近一次检查登录态成功的昵称
    "mid": 0                    // 只读：最近一次检查登录态成功的 mid
  },
  "crawl": {
    "real_crawl": true,         // true=真实爬虫；false=占位数据（离线演示）
    "max_pages": 1,             // 0~50，0=不限
    "max_sub_pages": 1,         // 0~20，0=不限
    "page_size": 20,            // 1~50，一级评论每页条数
    "include_replies": false,   // 楼中楼
    "sort_mode": "time",        // time | hot
    "max_items": 200,           // ≥0，0=不限；本次任务最多新增评论数
    "delay_seconds": 2.5,       // 2.0~60，请求最小间隔（合规硬性要求）
    "timeout_seconds": 15,      // >0，单次请求超时
    "require_login": false,     // true 时未登录直接失败
    "init_buvid": false         // 是否初始化 buvid3/buvid4 设备指纹
  },
  "pipeline": {
    "auto_clean": false,        // 采集完成后自动清洗
    "auto_analyze": false       // 清洗完成后自动情感分析
  },
  "updated_at": "2026-10-17T18:00:00+08:00"
}
```

默认值即上例（匿名、真实采集、关闭自动连续）。

### 2.5.2 `GET /api/settings`

```
GET /api/settings
```

成功 `200`：返回上述结构（Cookie 脱敏）。不存在的字段以默认值补齐；非法/损坏文件回退默认值。

```json
{
  "account": {"mode": "account", "has_cookie": true, "masked_cookie": "SESSD…9f2a",
               "uname": "示例用户", "mid": 123456},
  "crawl": {"real_crawl": true, "max_pages": 1, "max_sub_pages": 1, "page_size": 20,
             "include_replies": false, "sort_mode": "time", "max_items": 200,
             "delay_seconds": 2.5, "timeout_seconds": 15,
             "require_login": false, "init_buvid": false},
  "pipeline": {"auto_clean": false, "auto_analyze": false},
  "updated_at": "2026-10-17T18:00:00+08:00"
}
```

### 2.5.3 `PUT /api/settings`

请求体为**部分更新**（缺省字段保持不变，只做浅层嵌套合并）：

```jsonc
{
  "account": {"mode": "account", "cookie": "SESSDATA=...; bili_jct=..."},
  "crawl": {"max_pages": 3, "include_replies": true},
  "pipeline": {"auto_clean": true, "auto_analyze": false}
}
```

规则：

| 情况 | 行为 |
| --- | --- |
| `account.cookie` 非空 | 保存凭据（明文仅落盘，不回传） |
| `account.cookie: ""` 或 `account.mode == "anonymous"` | 清除凭据 |
| `account.mode == "account"` 但没有凭据 | **400**，`detail` 为中文提示（去设置页填写 Cookie） |
| 数值越界、枚举非法 | **400**，`detail` 带具体字段与合法范围（不返回 422、不做宽松裁剪） |
| 成功 | `200`，返回与 `GET /api/settings` 相同的脱敏结构 |

示例错误体：

```json
{"detail": "crawl.delay_seconds 必须在 2~60 之间"}
```

> 实际 `detail` 由后端校验层生成，形如「`crawl.<字段> 必须在 <min>~<max> 之间`」或
> 「`crawl.<字段> 必须大于 0`」，可直接展示给用户。

### 2.5.4 `POST /api/crawler/session/check`

检查 B 站登录态（**手动 Cookie 兜底时的校验入口**；推荐的人工登录窗口见 2.5.6）。请求体可省略：

```jsonc
{ "cookie": "SESSDATA=...;" }   // 可选：用临时凭据检查（尚未保存时使用）
```

- 未提供时用已保存凭据；两者都没有 → `200` 返回匿名态（`is_login=false`），不报错；
- 调用爬虫真实登录态接口（`BiliClient.login_status`）；网络/接口异常 → **502** + 中文原因；
- 登录成功后把 `uname` / `mid` 缓存进设置文件（只读字段）。

成功 `200`：

```json
{
  "mode": "account",
  "has_credentials": true,
  "is_login": true,
  "uname": "示例用户",
  "mid": 123456,
  "message": "已登录：示例用户"
}
```

匿名无凭据时（文案以实现返回为准）：`{"mode":"anonymous","has_credentials":false,"is_login":false,"uname":"","mid":0,"message":"未配置登录凭据，当前为匿名模式"}`。

### 2.5.5 `POST /api/videos/{bvid}/continue`

继续等待态任务到下一阶段：

```
POST /api/videos/{bvid}/continue
```

- 仅当任务处于 `crawled`（继续清洗）或 `cleaned`（继续分析）时允许；
- 其他状态 → **409**，`detail` 形如「任务当前状态 queued，无需继续」；
- 未知 `bvid` → **404**；
- 成功 `200`：任务重置为 `queued`、`progress=0`，`phases` 保留已完成阶段，重新入队；
  响应结构与 `POST /api/videos` 相同（`status="queued"`）。

```json
{"bvid": "BV1xx411c7mD", "status": "queued", "progress": 0,
 "created_at": "2026-10-16T14:30:00+08:00"}
```

### 2.5.6 人工登录窗口（推荐方式）

> 背景：B 站密码登录会触发极验点选验证码，**不允许 / 不适合由后端自动破解**，因此「登录账号」
> 的推荐交互是：前端点按钮 → 后端在运行服务的那台机器上弹出一个**真实浏览器窗口** →
> 由**人**完成扫码 / 账号密码 + 验证码登录 → 后端只负责等待、捕获 Cookie 并写入设置。
> 手填 Cookie（2.5.3 的 `account.cookie` + 2.5.4 校验）保留为「高级」兜底方式。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/account/login/start` | 打开登录窗口并启动捕获；已有窗口在等待时直接返回当前状态（幂等） |
| `GET` | `/api/account/login/status` | 轮询登录窗口状态（前端 2 秒一次） |
| `POST` | `/api/account/login/cancel` | 取消本次登录并关闭窗口 |

状态对象（`start` / `status` / `cancel` 共用）：

```jsonc
{
  "state": "idle",        // idle | opening | waiting | success | failed | cancelled | timeout
  "message": "尚未开始登录",
  "uname": "",            // success 后为 B 站昵称
  "mid": 0,
  "has_cookie": false,    // 设置中是否已有可用 Cookie
  "started_at": null,     // ISO8601 或 null
  "updated_at": "2026-10-17T18:00:00+08:00"
}
```

`state` 语义：

| state | 含义 |
| --- | --- |
| `idle` | 尚未开始登录 |
| `opening` | 正在启动浏览器 / 打开登录页 |
| `waiting` | 窗口已打开，等待用户完成登录 |
| `success` | 登录成功，Cookie 已写入设置 |
| `failed` | 启动 / 捕获失败，`message` 带可读原因 |
| `cancelled` | 用户关闭窗口或主动取消 |
| `timeout` | 超过 **300 秒**未完成登录 |

语义：

- `start`：在后台线程启动 Playwright（优先系统 Edge `channel="msedge"`，其次 Chromium），
  打开 `https://passport.bilibili.com/login` 并置前；状态 `opening → waiting`；不阻塞事件循环；
- **浏览器依赖缺失时返回 `503`**，`detail` 说明安装命令：
  `pip install playwright` + `python -m playwright install chromium`（Windows 已装 Edge/Chrome
  可直接使用，无需另装 Chromium）；未安装依赖不影响应用其它功能；
- 捕获线程每 2 秒读一次 Cookie，出现 `SESSDATA` 后调用
  `https://api.bilibili.com/x/web-interface/nav` 取 `uname` / `mid`，把 Cookie 写入设置
  （`account.mode="account"`、`uname`、`mid`），状态 `success`，随后关闭浏览器；
- 用户关闭窗口 → `cancelled`；超过 300 秒 → `timeout`；异常 → `failed` + 可读原因；
- **同一时间只允许一个登录窗口**；等待期间重复 `start` 返回当前状态（幂等）；
- 窗口打开在**后端所在机器**的桌面（本地课程项目即用户本机）；远程部署时需到服务器桌面操作，
  或改用手动 Cookie；
- 账号密码 / 验证码由用户在 B 站官方登录页输入，**本项目不读取、不记录**；Cookie 永不通过接口
  回传明文，日志不得打印 Cookie；
- 安全与合规：登录态属**个人凭据**，只保存到服务端设置文件（`outputs/settings.json`，已被
  `.gitignore` 忽略，**切勿提交仓库**）；爬虫仍遵守限速与 robots 约定（见
  [`docs/大规模采集运行手册.md`](大规模采集运行手册.md)），不绕过风控、不并发抓取。

`waiting` 状态示例：

```json
{
  "state": "waiting",
  "message": "已打开登录窗口，请在运行后端的电脑上完成扫码 / 登录",
  "uname": "", "mid": 0, "has_cookie": false,
  "started_at": "2026-10-17T18:00:00+08:00",
  "updated_at": "2026-10-17T18:00:02+08:00"
}
```

`success` 状态示例：

```json
{
  "state": "success",
  "message": "已登录：示例用户",
  "uname": "示例用户", "mid": 123456, "has_cookie": true,
  "started_at": "2026-10-17T18:00:00+08:00",
  "updated_at": "2026-10-17T18:00:35+08:00"
}
```

> `requirements.txt` 中 `playwright` 为**可选依赖**（仅人工登录窗口需要）；未安装时其它功能完全
> 不受影响，`start` 返回 503 并给出安装指引。

### 2.5.7 分步状态机、轮询与中间产物

状态迁移：

```
queued → crawling → [采集完成]
                        ├─ auto_clean=true  → processing（清洗）→ [清洗完成]
                        └─ auto_clean=false → crawled（暂停）
crawled →（continue）→ queued → processing（清洗）→ [清洗完成]
                        ├─ auto_analyze=true  → processing（情感推理→统计分析）→ completed
                        └─ auto_analyze=false → cleaned（暂停）
cleaned →（continue）→ queued → processing（情感推理→统计分析）→ completed
任意阶段异常 → failed（error 带可读原因）；retry 清空中间产物、回到 queued。
```

- 流水线阶段名称不变：`采集评论 → 文本清洗 → 情感推理 → 统计分析`；
- `crawled` / `cleaned` 视为**已停止**，前端不再自动轮询；`continue` / `retry` / 重新提交后恢复轮询；
- 开启自动连续时不允许在中间态停留（除非该阶段失败）；
- 中间产物落盘 `outputs/videos/<bvid>/crawl.json`（原始评论或占位计数）、
  `clean.json`（清洗后评论）与 `analyze.json`（逐条情感预测结果 + 顶层 `method`
  来源标注：真实模式为模型名，加载失败回退时为 `rule_placeholder`；供审计，
  不属于 HTTP 响应字段）；
  详情文件仍为 `outputs/videos/<bvid>.json`；
- 服务重启：`queued` / `crawling` / `processing` 重置为 `queued` 重新跑；
  `crawled` / `cleaned` **保持等待态**，等待用户操作；
- 任务记录新增内部字段 `settings_snapshot`（不含 Cookie）、`resume_stage`
  （`crawl` / `clean` / `analyze`）、`stage_state`，**不对外暴露**。

### 2.5.8 兼容性与错误码汇总

- **旧请求体有效**：`POST /api/videos` 不带 `settings` 时仍走原有「一次跑完四个阶段」路径，
  不暂停、不受本小节影响；
- **响应仅新增字段**：详情新增顶层 `pipeline`，`sentiment` / `trend` / `keywords` /
  `top_comments` / `model` / `placeholder` 等不变；
- **Cookie 永不进任务**：任务 `settings` 只接受 `account.mode`，Cookie 始终取服务端设置；
  任务记录与响应均不含 Cookie 明文；
- **人工登录窗口为 additive 能力**：不改变既有 `/api/settings`、`/api/videos` 语义；
  未安装 Playwright 时应用正常启动，仅 `POST /api/account/login/start` 返回 503；
- 错误码：

| 状态码 | 场景 |
| --- | --- |
| `400` | `PUT /api/settings` 的数值越界 / 枚举非法；`mode=account` 但无凭据；`POST /api/videos` 的 `settings` 非法；BV 号格式非法 |
| `404` | 未知 `bvid`（detail / retry / continue / delete） |
| `409` | 提交时该 BV 号已在处理中；`continue` 时任务不在 `crawled` / `cleaned` |
| `502` | `POST /api/crawler/session/check` 调用 B 站登录态接口的网络/接口异常 |
| `503` | `POST /api/account/login/start` 缺少浏览器依赖（未安装 Playwright、无可用 Edge/Chromium）；`detail` 带安装指引 |

---

## 三、前端轮询策略（已定）

- 列表每 **3 秒**轮询一次 `GET /api/videos`。
- 当所有任务都处于 `completed` / `failed` /**`crawled` / `cleaned`（等待操作）** 时**停止轮询**；
  提交新 BV、点「重试」或点「开始清洗 / 开始分析」（continue）后恢复。
- 页面切到后台（`document.visibilitychange` 隐藏）时暂停轮询，回到前台立即拉一次。
- 详情区：选中任务后拉一次详情；若其 `analysis.status` 不是 `completed` / `failed` /
  `crawled` / `cleaned`，则随列表轮询一起刷新（即详情也用 3 秒节奏），到达终态或等待态后停止。

---

## 四、mock 约定（后端实现前）

在 `frontend/` 内用 Vite 开发服务器中间件按本契约返回数据，位置 `frontend/mock/`：

- 提交 BV 后约 30 秒内走完 `queued → crawling → processing → completed`，进度真实递增。
- 预置 2 条演示数据（1 条已完成、1 条进行中），方便直接查看界面效果。
- 词频、趋势、代表性评论由 mock 生成，**不得写进业务组件**；
  真实后端就绪后只需移除 mock 插件（`vite.config.js` 一处），前端业务代码不改。
- **2026-10-17 起 mock 同步支持**：`GET/PUT /api/settings`、`POST /api/crawler/session/check`、
  `POST /api/videos/{bvid}/continue`、`POST /api/account/login/{start,status,cancel}` 与新状态
  `crawled` / `cleaned`，保证 `npm run dev`（默认 mock）可完整演示「设置 → 参数确认 → 分步执行」；
  mock 的默认设置与后端一致，其中真实采集用占位数据模拟、**不触网**，人工登录窗口用时间推进的
  模拟状态机（不弹真实浏览器）。

---

## 四点五、数据项目与单独评论（左栏三段中的前两段）

左栏共三段：**数据项目**（`projects`）、**单独评论**（`solo`）、**视频任务**（`videos`）。
视频任务见上文第二点；本节点定义前两段。

### 4.5.1 数据项目

**来源**：界面右上角「上传爬取数据」按钮 → 上传爬虫产出的 CSV/XLSX → 后端**落盘**
→ 自动按 `clean` 模式跑一遍 `src/preprocessing` 管线 → 产出可查看的表格与情感分析结果
（真实模型优先，加载失败回退规则占位，`method` 如实标注）。

**标题**：上传时未指定名称时显示为「未自定义数据」。

存储布局（`PROJECTS_ROOT` 默认 `data/uploads/`）：

```
data/uploads/<project_id>/
├── upload.json                 上传元数据（原始文件名、大小、sha256、上传时间、显示名）
├── project.json                项目状态与阶段清单（对外主状态文件）
├── initial.csv                 初始数据（上传原件解析后的统一 CSV，UTF-8 BOM）
├── clean.csv                   筛选后-保留（清洗 + 分词后的评论）
├── excluded.csv                筛选后-排除（含排除原因列）
├── sentiment.csv               逐条情感（真实模型优先，加载失败回退规则占位；含 label/confidence/probs/method）
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
`method` 取值：真实模式为具体模型名（如 `textcnn` / `bilstm_attention`）；
模型加载失败回退规则占位时为 `rule_placeholder`。
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
- `limit` 上限 500，默认 100；`offset` 越界返回空 `rows` 且 `total_rows` 不变（不报错）；
- **列名由后端返回，前端必须按 `columns` 动态渲染**，不得写死列；
- 三个阶段的实际列名（实测，`data/sample/bilibili_demo.csv` 上传后）：

  | 阶段 | 行数 | 列名 |
  | --- | --- | --- |
  | `initial` | 27 | `text, label, comment_id, input_file, input_row, source, video_id` |
  | `clean` | 25 | `text, label, tokens, comment_id, input_file, input_row, provided_label, source, status, text_hash, text_raw, video_id` |
  | `excluded` | 2 | 同上 + `exclude_reason` |

- `clean` 是**在原始列基础上追加派生列**（`tokens` / `text_raw` / `provided_label` / `status` / `text_hash`），
  **不删列**，因此列数多于 `initial`；`clean.text_raw` 与 `initial.text` 按 `comment_id` 对齐后逐条一致（原文链路不断）；
- `initial` **不是原样 dump**：上传件经 `load_records` 解析后由 `save_records` 统一写出 UTF-8 BOM CSV，
  多文件上传会合并、列取并集。<br>clean 模式下 `label` 列保持为空，原始标注保留在 `provided_label`。

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

`method` 必须如实反映来源：真实模型可用时为模型名（如 `textcnn` / `bilstm_attention`），
模型加载失败回退规则占位时为 `rule_placeholder`，前端据此显示模型名 / 「规则占位（降级）」标识。

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

#### 只读取路径：`GET /api/artifacts/{project_id}/path?stage=clean`

**无副作用**：只返回路径，**绝不调用 opener、不打开任何程序**。
界面的「复制文件地址」按钮用它，避免为了拿路径而真的弹出 Excel。

```json
// 成功 200
{"stage": "clean", "path": "D:\\...\\data\\uploads\\<id>\\clean.csv", "exists": true}
```

`stage` 同样只接受 `initial` | `clean` | `excluded`（默认 `clean`）；
未知项目 `404`，非法 `stage` `400`。

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
- `label` / `confidence` / `probs` / `method` 由前端把 `POST /api/predict` 的结果一并提交，
  因此**必须与 predict 的返回一致**；后端做基本校验（label 枚举、confidence ∈ [0,1]）。
  其中 `method` 为 `POST /api/predict` 响应中**新增**的字段（取值 `rule_placeholder` 或模型名）；
  若请求未带 `method`，后端按 `rule_placeholder` 处理。
- 文本为空或仅空白返回 `400`。

#### 列表：`GET /api/solo-comments?offset=0&limit=50`

```json
{"total": 12, "offset": 0, "limit": 50, "items": [ /* 单条评论对象，按 created_at 倒序 */ ]}
```

#### 删除：`DELETE /api/solo-comments/{id}` 与批量 `DELETE /api/solo-comments?ids=id1,id2`

- 单条：成功 `200`：`{"deleted": true, "deleted_count": 1}`；不存在返回 `404`。
- 批量：**幂等**。`ids` 缺省或为空 → `200 {"deleted": true, "deleted_count": 0}`；
  其中部分 id 不存在 → 仍 `200`，`deleted_count` 只统计真实删除数。
  （不返回 4xx，避免前端「清空全部」在空列表时弹错误提示。）

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
