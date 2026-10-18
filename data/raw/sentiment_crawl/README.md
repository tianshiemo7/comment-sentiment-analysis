# 跨分区 B 站评论数据集（原始、未清洗）

> 采集时间：2026-10-18 12:12 ~ 12:26（UTC+8）
> 目标：≥200 个视频、约 10,000 条评论，覆盖尽可能多的分区；整理为一份总表，并提供训练/测试划分。
> 状态：**只采集、未清洗**（文本保留爬虫自带的最小标准化：HTML 实体还原、NFKC、去零宽/控制字符；
> 未去重、未分词、未过滤短文本、未标注情感）。

## 1. 采集规划

- **抽样来源**：B 站排行榜 `https://api.bilibili.com/x/web-interface/ranking/v2`，按 18 个分区各取榜单靠前、
  且评论数 ≥ 120 的 **12 个视频**，按 bvid 去重；
- **分区（18 个）**：动画、音乐、舞蹈、游戏、科技、知识、运动、汽车、生活、美食、动物圈、时尚、娱乐、影视、鬼畜、纪录片、电影、电视剧
  （番剧 / 国创 / 资讯的排行榜接口返回 -400，未纳入）；
- **评论量分配**：216 视频 × 50 条一级评论 = **10,800 条**；每视频 2 页（`ps=50`，实际单页 26~30 条，`--max-items 50` 封顶）；
- **排序**：按时间（`mode=time`），不采楼中楼，降低请求量、避免回复重复计入；
- **执行**：3 个进程分片串行采集（每进程独立限速 3.0s + 抖动），总请求约 670 次，风控 0 次，耗时约 13 分钟。

## 2. 文件

| 文件 | 说明 |
| --- | --- |
| `comments_raw.csv` | **总表（交付主文件）**：10,800 行原始评论 + `category` / `rid` / `split` |
| `train.csv` / `test.csv` | 从总表按 `split` 列拆分，训练 9,000 条 / 测试 1,800 条 |
| `all_comments_raw.csv` | 爬虫原样合并结果，仅 12 个爬虫字段（与项目旧 CSV 完全兼容） |
| `crawl_plan.csv` | 采集目录：视频 → 分区 / 标题 / UP 主 / 榜单评论数 |
| `videos_meta.csv` | 视频级清单：分区、split、实际评论条数、平均点赞 |
| `dataset_stats.json` | 统计摘要（总量、分类分布、长度、重复检查） |
| `bvids.txt`、`catalog.json`、`ranking_snapshot.json` | 可复现的 BV 清单与排行榜原始快照 |
| `shard{1,2,3}/`、`shard{n}_merged.csv` | 分片断点与分片合并结果（支持中断续爬） |

## 3. 总表字段

`bvid, aid, video_title, rpid, level, root_rpid, parent_rpid, text, likes, reply_count, ctime_utc, collected_at_utc, category, rid, split`

- `text`：评论文本原文（保留 emoji、`[doge]` 等 B 站表情、中文标点）；
- `ctime_utc`：评论发布时间；`collected_at_utc`：采集时间；
- `category` / `rid`：视频所属分区；
- `split`：`train` / `test`。

## 4. 训练/测试划分规则

- **按视频分组**：同一 `bvid` 的评论只出现在一侧，避免同视频重复模式造成泄漏；
- **按分区分层**：每个分区 12 个视频 → 2 个进 test（约 1/6），保证 18 个分区在训练/测试中均覆盖；
- 固定随机种子 `seed=42`，可复现；划分结果：train 180 视频 / 9,000 条，test 36 视频 / 1,800 条。

## 5. 质量核对

- 评论 10,800 条，`rpid` 全局唯一（0 重复），无空文本；
- 视频 216 个，全部命中采集目录；18 个分区各 600 条，分布完全均衡；
- 文本长度 1~929 字符，平均 18.2；
- `comments_raw.csv` 的 `text` 与 `all_comments_raw.csv` 逐条一致（0 处改动）；
- 数据来源已登记到 `data/raw/sources.json` 与 `data/raw/来源说明.md`。

## 6. 复现

```bash
cd comment-sentiment-analysis
# 1) 生成跨分区采集目录（216 视频 / 18 分区）
python scripts/build_crawl_catalog.py --per-category 12 --min-replies 120 --out-dir data/raw/sentiment_crawl
# 2) 分片并行采集 + 合并（断点续爬，已完成视频零请求跳过）
python scripts/crawl_shards.py --workers 3 --delay 3.0 --max-pages 2 --max-items 50
# 3) 生成总表与训练/测试划分（不清洗）
python scripts/build_final_dataset.py
```

## 7. 合规说明

- B 站视频页 robots 允许抓取；`api.bilibili.com/robots.txt` 为 `Disallow: /`，项目按 `warn` 策略
  仅在课程/研究用途下、带登录态、低速（≥3s/请求）小规模采集，并如实登记来源；
- 未采集登录后受限内容，未绕过付费/权限；评论为公开可见内容；
- 数据仅用于课程设计/研究，不再分发；使用时请遵守 B 站用户协议与相关法律法规。
