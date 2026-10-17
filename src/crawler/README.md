# src/crawler —— ① 爬虫模块

> 对应需求：**数据来源**（公开数据集或爬虫，遵守 robots 协议，注明来源与采集时间）；路线图阶段一。
> 对应验收项：`docs/验收检查清单.md` 二、数据 ——「数据来源与采集时间已注明」。
> 实测效率与改进前后对比见根目录 [README](../../README.md) 的「爬虫模块改进与实测效率」一节。

## 职责

1. 获取原始评论：B 站视频评论（接口版）与通用 HTML 页面评论（骨架版）两条链路；
2. 采集前检查目标站点 `robots.txt`，并把判定结果写进采集报告；
3. 控制请求频率（默认 2.5 秒/次 + 30% 抖动，风控时自适应降速）；
4. 把「站点 / URL / 采集时间 / 条数」写入 `data/raw/来源说明.md` 与 `data/raw/sources.json`；
5. 输出标准化、可去重、可断点续爬的 CSV，直接对接 `src/preprocessing`。

## 目录

| 文件 | 说明 |
| --- | --- |
| `bilibili.py` | B 站接口爬虫：WBI 签名、分页、楼中楼、断点续爬、风控退避、CLI、适配器 |
| `spider.py` | 通用 HTML 爬虫：robots → 取页 → 解析 → 去重 → 落盘（`CrawlConfig` / `HtmlCrawler`） |
| `transport.py` | HTTP 传输层：连接池复用、统一超时、重试、429 `Retry-After`、请求指标 |
| `rate_limit.py` | 最小间隔 + 抖动 + 自适应惩罚限速器；指数退避函数 |
| `robots.py` | robots.txt 判定（按主机缓存、404 允许、异常 fail-closed） |
| `normalize.py` | 文本与记录标准化（HTML 实体、NFKC、零宽/控制字符、字段类型） |
| `datasets.py` | 公开带标签语料注册表：下载 + 可选 SHA256 + 来源登记 + `import_labeled_csv` 统一格式 |
| `labeling.py` | 无标签评论 → 分层确定性标注候选；标注合并校验 + manifest |
| `sources.py` | 来源登记：JSON 事实源 + Markdown 表格渲染/合并 |
| `errors.py` | 异常层次（可恢复停止 vs 配置错误 vs 风控/业务错误） |
| `__main__.py` | 统一 CLI：`python -m src.crawler <bilibili\|html>` |

## 反爬 / 风控稳健性策略

> 定位：**不是绕过站点限制**，而是在「匿名、低速、小规模」的合规前提下，让采集在站点
> 正常的风控与网络波动下可恢复、可解释、可停止。

| 策略 | 实现位置 | 说明 |
| --- | --- | --- |
| WBI 签名 | `bilibili.py:sign_wbi` | 2023 年后 B 站评论主接口要求 `wts` + `w_rid`；密钥带 TTL 缓存，风控时强制刷新 |
| 限速 + 抖动 | `rate_limit.py` | 以「上次请求开始时间」为基准，间隔在 `[d, 1.3d]` 随机；避免固定节奏 |
| 自适应降速 | `RateLimiter.penalize/relax` | 风控后间隔 ×1.5（上限 60s），连续成功缓慢回落 |
| 重试 + 指数退避 | `transport.py` | 超时 / 连接错误 / 5xx / 429 最多 3 次，退避 1s→2s→4s 带抖动 |
| 尊重 `Retry-After` | `transport.py` | 429 时取响应头与退避的较大值 |
| 风控识别 | `bilibili.py:_api` | HTTP 412 / 403 / 业务码 -352 / -509 / -799 → 冷却 + 刷新 WBI + 重试 |
| 会话复用 | `transport.py:build_session` | 连接池（8）复用 TLS，减少握手开销 |
| 设备指纹（可选） | `BiliClient(init_buvid=True)` | 公开接口 `/x/frontend/finger/spi` 获取 buvid3/4。**默认关闭**：实测带上 buvid3 后匿名账号只能看到 3 条评论（见根 README 实测） |
| robots 校验 | `robots.py` | 视频页按 www.bilibili.com 规则（允许）；API 主机单独判定并如实告警 |
| 安全停止 | `errors.py` | 重试耗尽 / 风控持续时抛 `CrawlStopped`，已落盘 CSV 与断点保留 |
| 长跑断点 | `bilibili.py:sub_done` | 已处理根评论的楼中楼不重复请求；旧断点兼容；上限 5000 条防膨胀 |
| 时间预算 | `bilibili.py:max_seconds` | `--max-seconds` 到时先落 CSV 再存断点，`timed_out` 写入报告；默认 0 不限 |

## 标准化（正规化）规则

文本经 `normalize.normalize_text`：

1. HTML 实体还原（`&amp;` / `&#39;` / `&nbsp;`）；
2. 逐字符 NFKC：全角字母数字转半角，**中文标点保留**（避免 `！` 变 `!` 丢失语气）；
3. `\r\n` / `\r` 统一为 `\n`；
4. 删除零宽字符（`U+200B`、`U+FEFF` 等）与除换行/制表外的控制字符；
5. 合并连续空白、去掉行尾空格、最多保留一个空行；
6. 纯空白 / 纯标点 / 空文本过滤；emoji 与 `[笑哭]` 等 B 站表情保留（情感信号）。

记录字段统一为 `COLUMNS`：`bvid, aid, video_title, rpid, level, root_rpid, parent_rpid,
text, likes, reply_count, ctime_utc, collected_at_utc`，与旧版 CSV **列名完全兼容**，
`src/preprocessing/dataset.py` 的列名映射（`bvid→video_id`、`rpid→comment_id`、
`ctime_utc→posted_at`）无需改动。

## 公开数据集与标注

```bash
# 列表 / 下载（下载后自动登记来源；校验失败不留半成品）
bash scripts/get_data.sh --list-datasets
bash scripts/get_data.sh --dataset chnsenticorp
python -m src.crawler.datasets --import-csv data/raw/datasets/ChnSentiCorp_htl_all.csv \
    --output data/raw/chnsenticorp_unified.csv

# 多份语料合并 + 类别均衡（按较小类别下采样；实测 18,644 条 / 正负各 9,322）
python -m src.crawler.datasets --merge-balance \
    data/raw/waimai_10k_unified.csv data/raw/chnsenticorp_unified.csv \
    --output data/raw/comments_labeled_balanced.csv --seed 42

# 无标签评论 → 标注候选（按 video_id 分层、固定 seed）
python -m src.crawler.labeling export --input data/raw/BV1GJ411x7h7_comments.csv \
    --output data/labeling/sample.csv --sample-size 500 --seed 42
# 人工标注后合并校验（标签取值 / 重复 id / 冲突 / 缺失）
python -m src.crawler.labeling merge --sample data/labeling/sample.csv \
    --labels data/labeling/labels.csv --output data/raw/comments_labeled.csv
```

安全约定：下载 URL 仅允许 `http(s)`；`filename` 拒绝目录穿越；ZIP 只下载不解压；
内置数据集 URL/SHA256 需在联网环境首次复核（测试不联网）。

## 接口适配（给其它模块调用）

```python
from src.crawler import BiliClient, crawl_bilibili, crawl_video, fetch_video_comments

# 1) 完整采集（落盘 CSV + 断点 + 来源登记），返回带效率指标的报告
report = crawl_bilibili(["BV1GJ411x7h7"], max_pages=2, include_replies=True)
print(report.as_dict())

# 2) 单视频采集（可复用同一个 BiliClient 做批量）
client = BiliClient(delay=2.5)
result = crawl_video(client, "BV1GJ411x7h7", max_pages=1, include_replies=False)

# 3) 只要评论数据、不落盘（供 src/videos / src/artifacts 接入真实爬取）
rows = fetch_video_comments("BV1GJ411x7h7", max_pages=1)
```

`fetch_video_comments` 与 `crawl_video` 共用同一套客户端、限速、robots 与标准化逻辑；
`crawl_video` 的 `VideoCrawlResult` / `crawl_bilibili` 的 `CrawlReport` 都提供
`as_dict()`，可直接嵌进接口响应或写 JSON 报告。

通用 HTML 爬虫：

```python
from src.crawler import CrawlConfig, CrawlReport, crawl_with_report

config = CrawlConfig(
    start_urls=["https://example.com/post"],
    source="示例站点",
    extra={"selector": ".comment-content"},
    delay_seconds=1.5,
)
report = crawl_with_report(config)
```

## 合规说明

- **网页 robots**：B 站视频页 `https://www.bilibili.com/video/<BV>` 当前允许抓取，已校验；
- **API robots**：`https://api.bilibili.com/robots.txt` 当前为 `Disallow: /`。本项目把它
  视为「视频页数据通道」，默认 `warn` 模式：日志告警 + 来源说明如实记录 + 保持匿名
  低速小规模；设置 `BILI_API_ROBOTS_POLICY=enforce` 可改为直接拒绝（采集会停止）；
- **凭据**：源码不含 Cookie；如需登录态由使用者通过 `BILI_SESSDATA` 等环境变量显式提供；
- **频率**：CLI 强制 `--delay ≥ 2`；默认 2.5s；单进程串行，无并发爬取。

## 命令行

```bash
# B 站：采集 2 页一级评论 + 楼中楼
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 2 --include-replies

# B 站：机器可读报告（含请求数 / 延迟分位 / 风控次数）
python -m src.crawler bilibili --bvid BV1GJ411x7h7 --max-pages 1 --json

# 通用 HTML 页面
python -m src.crawler html --url https://example.com/post --source 示例站点 \
    --selector ".comment-content" --max-items 100

# 统一脚本
bash scripts/get_data.sh BV1GJ411x7h7
```

## 测试与效率基准

```bash
# 爬虫单元测试（离线；本地 HTTP 服务 + 假会话，不访问真实站点）
python -m pytest tests/test_crawler.py -q                       # 70 passed
python -m pytest tests/test_crawler_datasets.py tests/test_crawler_labeling.py -q  # 47 passed

# 离线效率基准：本地 mock API（含 WBI 校验 / 分页 / 楼中楼 / 风控恢复）
# 默认 --repeat 3 取中位数，并报告进程 RSS
python scripts/bench_crawler.py offline --pages 5 --per-page 20 --include-replies --repeat 3

# 真实联网轻量测试（保守频率，结果同时写入 outputs/benchmarks/*.json）
python scripts/bench_crawler.py live --bvid BV1GJ411x7h7 --max-pages 1
```

## 下游

- `src/preprocessing`：`python -m src.preprocessing --input data/raw/<BV>_comments.csv --mode clean`
- `src/artifacts`：上传爬虫 CSV → clean 管线；列名映射保持兼容；
- `src/videos`：`VIDEO_REAL_CRAWL=1` 时经 `src/videos/crawler_adapter.py` 调用
  `fetch_video_comments` 做真实采集；默认仍走占位数据。
