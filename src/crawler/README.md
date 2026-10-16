# src/crawler —— ① 爬虫模块

> 对应需求：**数据来源**（公开数据集或爬虫，遵守 robots 协议，注明来源与采集时间）；路线图阶段一。
> 对应验收项：`docs/验收检查清单.md` 二、数据 ——「数据来源与采集时间已注明」。

## 职责

1. 获取原始评论：优先使用公开数据集（ChnSentiCorp / 外卖评论等），需要自采时再启用爬虫；
2. 采集前检查目标站点 `robots.txt`，遵守 `Crawl-Area` 限制；
3. 控制请求频率（默认 `CRAWL_DELAY_SECONDS=1.5`）与并发，避免对站点造成压力；
4. 把「站点 / URL / 采集时间 / 条数」写入 `data/raw/来源说明.md`。

## 输入 / 输出

| 方向 | 内容 | 位置 |
| --- | --- | --- |
| 输入 | 起始 URL、限速、UA、最大条数 | `CrawlConfig` |
| 输出 | 原始评论 `[{text, label?, source, url, crawled_at}]` | `data/raw/` |
| 输出 | 数据来源说明（Markdown 表格） | `data/raw/来源说明.md` |

## 文件

| 文件 | 说明 |
| --- | --- |
| `spider.py` | 爬虫主流程骨架：robots 检查 → 取页 → 解析 → 去重 → 落盘 |
| `sources.py` | 数据来源登记与 `来源说明.md` 生成 |

## 待实现清单（TODO）

- [ ] `spider.py:is_allowed_by_robots` —— 用 `urllib.robotparser` 判断 URL 是否允许抓取
- [ ] `spider.py:fetch_html` —— `requests.get` + 超时 + 重试 + `time.sleep(delay)` 限速
- [ ] `spider.py:parse_comments` —— BeautifulSoup 解析评论节点，返回纯文本列表
- [ ] `spider.py:crawl` —— 串联上面的步骤，按 `max_items` 截断并去重
- [ ] `spider.py:save_raw` —— 写出 `data/raw/<source>.csv`（字段 `text,label,source,url,crawled_at`）
- [ ] `sources.py:write_source_note` —— 追加一行来源记录到 `data/raw/来源说明.md`

## 关联

- 路线图：`docs/开发路线图.md` 阶段一第 1 步
- 下游：`src/preprocessing`（清洗与分词）
- 一键脚本：`scripts/get_data.sh`
