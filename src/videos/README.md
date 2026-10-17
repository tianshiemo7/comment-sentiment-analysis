# src/videos —— B 站视频评论分析任务（`/api/videos` 系列）

> 对应契约：`docs/API契约.md`（**唯一契约来源**，字段名 / 状态枚举 / phases 语义以它为准）。
> 本包实现契约第二节的 5 个接口，HTTP 路由挂在 `src/inference/app.py`。
> 既有 5 个接口（`/health`、`/api/predict`、`/api/predict/batch`、
> `/api/predict/batch/download/{file_id}`、`/api/models`）**保持完全不变**。

## 接口一览（字段严格照契约）

| # | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 1 | POST | `/api/videos` | 提交 BV 号；非法 → 400，同 BV 在处理中 → 409 |
| 2 | GET | `/api/videos` | 列表（`total` + `videos`，按 `created_at` 倒序） |
| 3 | GET | `/api/videos/{bvid}/detail` | 详情（未完成也返回 200，不报错） |
| 4 | POST | `/api/videos/{bvid}/retry` | 重置为 `queued`；不存在 → 404 |
| 5 | DELETE | `/api/videos/{bvid}` | 删除任务；不存在 → 404 |

提交成功（200）：`{"bvid","status":"queued","progress":0,"created_at"}`；
删除成功（200）：`{"deleted": true}`。

## 状态机与进度

```
queued ──▶ crawling ──▶ processing ──▶ completed
  0         0→40          40→100         100
                                 └────▶ failed（带 error）
```

`phases` 四个子步骤（`采集评论` / `文本清洗` / `情感推理` / `统计分析`），
状态取 `pending | active | done | failed`：

| 任务状态 | phases |
| --- | --- |
| `queued` | 全 `pending` |
| `crawling` | 采集评论 `active`，其余 `pending` |
| `processing` | 采集评论 `done`，当前阶段 `active`，其余 `pending` |
| `completed` | 全 `done` |
| `failed` | 失败阶段 `failed`（之前的 `done`、之后仍 `pending`） |

- `progress` 为整数 0~100，**严格单调不减**；
- 采集阶段 `comment_count` 朝最终评论数逐步增长；
- 同一 BV 号重复提交（`queued` / `crawling` / `processing`）返回 **409**；
  已完成 / 已失败的任务重新提交会重跑（状态回到 `queued`）。

## 阶段时长可配置（硬要求）

默认四段合计 **30 秒**（与前端 mock 节奏一致），全部可用环境变量或
`PipelineConfig(...)` 覆盖，**测试用毫秒级配置即可跑完整个状态机**：

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_CRAWL_SECONDS` | `12` | 采集评论阶段耗时 |
| `VIDEO_CLEAN_SECONDS` | `6` | 文本清洗阶段耗时 |
| `VIDEO_SENTIMENT_SECONDS` | `8` | 情感推理阶段耗时 |
| `VIDEO_STATS_SECONDS` | `4` | 统计分析阶段耗时 |
| `VIDEO_STEPS_PER_PHASE` | `4` | 每阶段分几步推进进度 |
| `VIDEO_SIMULATE_FAILURE` | `0` | 置 `1` 时在情感推理阶段失败（验证 `failed` + retry） |
| `VIDEOS_DIR` | `outputs/videos` | 任务落盘根目录 |

```bash
# 真机联调时把 30 秒压到 1 秒左右
VIDEO_CRAWL_SECONDS=0.4 VIDEO_CLEAN_SECONDS=0.2 VIDEO_SENTIMENT_SECONDS=0.2 \
VIDEO_STATS_SECONDS=0.2 python -m uvicorn src.inference.app:app --port 8123
```

## 落盘与恢复

```
outputs/videos/
├── index.json          # 任务索引（含 status / progress / phases / 标题 / 时间）
└── <bvid>.json         # 该视频的完整 detail
```

- 写入为**原子替换**（先 `.tmp` 再 `os.replace`），进程被杀不会留下半截 JSON；
- 启动时读取 `index.json`，把残留的 `queued` / `crawling` / `processing` 任务
  **统一重置为 `queued` 并重新入队**，不会出现永远卡在 `crawling` 的僵尸任务；
- lifespan 关停时置位 `stop_event`，流水线在阶段内立刻退出并把任务恢复成 `queued`。

## 真实爬取（opt-in，默认关闭）

默认 `real_crawl=False`：不发起任何网络请求、不加载模型权重，detail 由
`placeholder.py` 用「`sha256(bvid)` 派生的确定性伪随机」生成，响应顶层
`placeholder: true`，标题/UP 主带「占位」字样。

设置 `VIDEO_REAL_CRAWL=1`（或测试里 `PipelineConfig(real_crawl=True)`）后，
「采集评论」阶段会调用 `crawler_adapter.collect_video_comments`
（底层 `src.crawler.fetch_video_comments`，已含 robots 校验、限速、风控退避、文本标准化）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `VIDEO_REAL_CRAWL` | `0` | `1` 时启用真实采集 |
| `VIDEO_CRAWL_MAX_PAGES` | `1` | 一级评论最多页数（1~50） |
| `VIDEO_CRAWL_MAX_ITEMS` | `200` | 本次最多新增评论数（0=不限） |
| `VIDEO_CRAWL_DELAY` | `2.5` | 请求最小间隔（秒，不得为负） |
| `VIDEO_CRAWL_INCLUDE_REPLIES` | `0` | 是否采集楼中楼 |
| `VIDEO_CRAWL_TIMEOUT` | `15` | 单次请求超时（秒） |

真实采集完成后：

- `comment_count` / `video.comment_total` = 真实条数；
- `top_comments`、`keywords`（已过滤 URL/@提及/[表情] 噪声）、`trend`（按 `ctime_utc` 分天）、
  `length_stats` 来自真实评论；
- 顶层 `placeholder=false` **只表示评论数据真实**；情感极性仍是
  `predict_rule_based`（`method=rule_placeholder`），真实模型接入后才会变化；
- 采集失败或空结果：任务落 `failed`（`error` 含可读原因），不会卡在 `crawling`；
- 真实采集已消耗真实网络时间，因此不再额外 sleep「采集评论」的模拟时长。

**诚实说明**：真实采集只改变「评论数据」来源，文本清洗/情感推理/统计分析仍是占位或规则实现。
完整真实链路需要接入 `src/preprocessing` 与训练出的模型检查点。

## 与契约的两点明确说明（新增而非改名）

1. 详情响应多一个**顶层** `placeholder` 字段，用于诚实标注数据来源
   （占位数据 `true`；真实爬取评论 `false`，但情感方法仍可能是规则占位）；
2. 详情未完成时：`sentiment` 为 `null`，`trend` / `top_comments` 为 `[]`，
   `keywords` 保持 `{"positive": [], "negative": []}` 的结构，`analysis.length_stats` 为 `null`；
   `model` 是「当前生效模型」的静态指标（与视频无关），因此始终返回对象。

`model` 字段：优先读 `GET /api/models` 背后的 `outputs/stats/metrics_<model>.json`
**真实指标**；未训练（指标全 0）时退回契约示例里的占位指标，保证前端图表可渲染。

## 分层

| 文件 | 职责 |
| --- | --- |
| `schemas.py` | 契约代码化：Pydantic 模型、状态枚举、BV 号正则 |
| `config.py` | 阶段时长 / 步数 / 模拟失败 + 真实采集参数等配置 |
| `crawler_adapter.py` | 真实采集适配层（opt-in）：env 解析、调用 `src.crawler.fetch_video_comments`、异常包装 |
| `errors.py` | 400 / 409 / 404 异常与契约锁定的错误文案 |
| `messages.py` | 各状态 `message` 文案 |
| `store.py` | 线程安全 + 原子写的落盘存储 |
| `pipeline.py` | 单任务流水线（可中断、可失败、进度单调） |
| `placeholder.py` | 占位 detail 生成（诚实标注） |
| `service.py` | 业务逻辑 + 队列 / 单工作线程 / 启动恢复 / 优雅退出 |

## 自测

```bash
python -m pytest tests/test_videos_api.py tests/test_videos_pipeline.py -q
python -m pytest tests/test_videos_real_crawl.py -q    # 真实采集的离线用例（monkeypatch，不联网）
python -m pytest tests/ -q                           # 全量（当前 451 项）
```
