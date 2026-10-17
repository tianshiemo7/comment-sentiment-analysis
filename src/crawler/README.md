# 评论采集框架

`spider.py` 统一配置、站点分派、robots 检查、CSV 保存和来源登记；`bilibili.py` 提供 B站 API、WBI 签名、一级评论和楼中楼分页；`sources.py` 追加来源说明。

## 命令行

在项目根目录执行（默认 B站）：

```powershell
python -m src.crawler.spider --bvid BV1AJ411g7Fr --include-replies --max-pages 5 --max-sub-pages 3
```

原命令继续可用：

```powershell
python src/crawler/bilibili.py --bvid BV1AJ411g7Fr --include-replies
```

省略 `--bvid` 时提示输入 BV 号或视频链接，请在支持输入的终端运行。多个 BV 号或链接用空格分隔。

```powershell
python -m src.crawler.spider --bvid BV1AJ411g7Fr --max-items 500 --delay 5 --timeout 15 --out data/raw/my_comments.csv
```

| 参数 | 含义 |
| --- | --- |
| `--bvid` | 一个或多个 BV 号 / B站视频链接 |
| `--include-replies` | 启用楼中楼；默认只采一级评论 |
| `--max-pages` | 每个视频本次最多处理的一级评论页数，默认 5，范围 1–50 |
| `--max-sub-pages` | 每条一级评论最多处理的楼中楼页数，默认 3，范围 1–20 |
| `--max-items` / `--limit` | 所有视频、两级评论合计的本次新增条数上限，默认 2000；历史记录不计入 |
| `--mode time/hot` | 按时间 / 热度，默认 time |
| `--delay` | 请求间隔，默认至少 2.5 秒；B站最小 2 秒 |
| `--timeout` | 请求超时设置，默认 15 秒 |
| `--out` | 输出路径；默认项目根目录下 `data/raw/bilibili.csv` |
| `--reset-state` | 从头扫描，保留 CSV 并按评论 ID 去重 |
| `--skip-robots` | 跳过本地 robots.txt 检查，日志和来源说明会记录此选择 |

环境变量 `CRAWL_USER_AGENT`、`CRAWL_DELAY_SECONDS` 可设置项目身份与间隔。`BILI_SESSDATA` 可提供本人有效登录凭据，不要写进代码或提交到 Git。当前代码只读取进程环境变量，不自动加载 `.env`。

## 输出与断点

统一 CSV 前五列：`text,label,source,url,crawled_at`。自采 `label` 为空，表示未标注，不能视作负面情感。B站附加 `bvid,aid,video_title,rpid,level,root_rpid,parent_rpid,likes,reply_count,ctime_utc,collected_at_utc`。`level=2` 是楼中楼，`root_rpid` 指向一级评论。

CSV 使用 UTF-8 BOM，新数据按批次追加；已有评论按 `(source,bvid,rpid)` 去重。来源说明写在输出文件同目录的 `来源说明.md`，每次运行追加时间、视频 URL、新增条数和运行状态。

断点位于输出目录 `.checkpoints/<输出路径摘要>/<BV号>.json`。只有当前页及配置范围内的楼中楼全部处理完才推进游标。发生风控、网络中断或条数上限时，已保存数据保留，未完成页面下次重扫并去重。改变排序、是否包含楼中楼、楼中楼页数时需要 `--reset-state`。不要同时运行多个任务写入同一 CSV。

迁移后默认使用新的合并文件；原有 `<BV号>_comments.csv` 及旧断点均保留，不自动合并或覆盖。新输出会从第一页扫描。指定已有但字段不匹配的 CSV 会报错，需选择新文件。删除输出 CSV 后，下次运行会重新扫描而不跳过旧断点。

## 风控与访问限制

默认采集前检查 API 域名的 robots.txt；404 按允许处理，明确拒绝或无法确认时停止。使用 `--skip-robots` 可跳过这项本地检查：

```powershell
python -m src.crawler.spider --bvid BV1AJ411g7Fr --include-replies --max-pages 5 --max-sub-pages 3 --skip-robots
```

Python 调用可设置 `CrawlConfig(..., respect_robots=False)`。原 `bilibili.py` 命令入口也支持同一参数。切换 robots 选项不需要重置断点。

HTTP 401/403/412/429、接口 `-352` 等不会自动重复请求。跳过本地 robots 检查不会消除服务器端的登录、权限或风控要求；`-352` 会明确报告并保留已经保存的数据。

`--max-pages` 和 `--max-sub-pages` 限定采集范围，运行成功不代表取得全部评论。失败返回非零退出码；中断返回 130。

## Python 调用

```python
from src.crawler import CrawlConfig, crawl

items = crawl(CrawlConfig(
    start_urls=['BV1AJ411g7Fr'],
    source='bilibili',
    max_items=500,
    extra={'include_replies': True, 'max_pages': 5, 'max_sub_pages': 3},
))
# items 只包含本次新保存的记录；异常时先前已写入的 CSV 不会被撤销。
```

静态 HTML 站点须指定评论正文 CSS 选择器（不会自动处理 JavaScript 渲染或发现下一页）：

```powershell
python -m src.crawler.spider --site html --urls https://example.com/comments --selector ".comment-text" --out data/raw/html.csv
```

`save_raw(items, path)` 是显式覆盖导出标准五列的工具；采集主流程使用增量存储。依赖见项目根目录 `requirements.txt`（requests、beautifulsoup4）。

## 测试

```powershell
python -m pytest tests/test_crawler.py -q
```

测试替换 HTTP 边界，验证真实的分页、去重、保存、来源登记、断点和错误处理，不访问 B站。
