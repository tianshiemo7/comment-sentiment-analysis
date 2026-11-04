# 数据导出 + 词云词量 60 + Python 词云图片 设计（2026-11-04）

> 本文件是实现前的**接口约定**，后端、前端、文档子任务都以本文件为准。
> 实现过程中如需偏离，必须先通知 Lead 并同步修改本文件。

## 1. 目标

1. **导出文件（浏览器下载）**
   - 数据项目（artifacts）：导出「爬虫上传的原始文件」（`initial` 阶段 CSV）与
     「数据清理过的文件」（`clean` 阶段 CSV），另附「排除记录」（`excluded` 阶段 CSV）。
   - 视频任务（videos）：导出「爬虫采集的原始文件」（`crawl.json` 转 CSV）与
     「数据清理过的文件」（`clean.json` 转 CSV）。
   - 现状只有「打开 CSV（服务器本机）」「复制文件地址」，没有浏览器下载，本设计补上。
2. **词云词量增加**：视频任务每侧关键词从 **8** 提到 **60**；数据项目从 **50** 统一为 **60**。
   前端词云显示全部返回词，字号随词数自适应，关键词排行仍取前 15。
3. **词云可切换为 Python 渲染的图片**：视频任务详情与数据项目详情的正面/负面词云卡片，
   支持在「前端渲染（echarts-wordcloud）」与「Python 渲染（wordcloud 库生成 PNG）」之间切换。

非目标：不改变现有情感预测链路；不新增 npm 依赖；不改动实验可视化页（其训练语料词云本来就是
Python 生成的 PNG）；不做 PDF/Excel 导出；不把导出文件打包成 ZIP。

## 2. 方案对比与选择

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **A（采用）** | 导出走 REST 下载端点，后端从落盘 CSV/JSON 直接返回；Python 词云由按需端点用真实词频渲染并缓存到任务目录 | 改动集中、无新依赖、缓存命中后秒开、不受表格分页限制 |
| B | 处理流水线里预生成 PNG 并把 URL 写进 detail 契约 | 每次分析都白算图；历史任务与改词量后图片过期；还要扩契约字段 |
| C | 前端把三张表数据拼 Blob 下载 | 表格分页每页仅 100 行，导出不全；视频任务无表可拼 |

## 3. 后端接口新增

所有新接口沿用现有安全约定：只接受服务端生成的 `project_id` / `bvid` 与固定枚举参数，
**对外绝不接受任何路径**；下载响应统一 `Cache-Control: no-store`。

### 3.1 数据项目导出

`GET /api/artifacts/{project_id}/download?stage=initial|clean|excluded`

- 200：`text/csv; charset=utf-8`，`Content-Disposition: attachment`，
  文件名形如 `<项目名或 id>_<阶段名>.csv`（非 ASCII 用 RFC 5987 `filename*`）。
  内容为该阶段落盘 CSV 原样字节（UTF-8 BOM，Excel 可直接打开）。
- 400：`stage` 非枚举值；项目 id 形态非法。
- 404：项目不存在；该阶段文件尚未生成。

实现复用 `ArtifactStore.resolve_open_path`（stage 白名单 + `resolve()` 后必须位于
`data/uploads/` 或 `data/processed/` 内），不新增路径拼接逻辑。

### 3.2 视频任务导出

`GET /api/videos/{bvid}/download?stage=crawl|clean`

- 由 `outputs/videos/<bvid>/crawl.json` / `clean.json` 的 `records[]` **实时转 CSV**
  （UTF-8 BOM），不改变落盘 JSON。
- crawl 列（与爬虫 CSV 标准列一致）：

  | 列 | 说明 |
  | --- | --- |
  | `bvid, aid, video_title, rpid, level, root_rpid, parent_rpid` | 采集标识 |
  | `text` | 原始评论文本 |
  | `likes, reply_count` | 互动数 |
  | `ctime_utc, collected_at_utc` | 评论时间 / 采集时间（ISO8601） |

- clean 列 = crawl 全部列 + `raw_text`（清洗前原文；`text` 为清洗后文本）。
- 200：`text/csv; charset=utf-8`，`Content-Disposition: attachment`，
  文件名 `collect_<bvid>.csv` / `clean_<bvid>.csv`。
- 400：`stage` 非枚举值 / bvid 形态非法；404：任务不存在；404：该阶段产物尚未生成
  （detail 中文提示，如「该任务尚未完成采集」）。

实现放在 `src/videos/service.py`（读阶段 JSON + 转 CSV），路由在
`src/inference/app.py`；CSV 序列化不触碰 `detail` 契约字段。

### 3.3 Python 渲染词云图片

两个只读端点，参数 `tone=positive|negative`：

- `GET /api/videos/{bvid}/wordcloud-image?tone=positive`
- `GET /api/artifacts/{project_id}/wordcloud-image?tone=positive`

响应：`image/png`（inline），可选 `&download=true` → `attachment`。

- 数据来源：视频用详情聚合后的 `keywords[tone]`（先过 `clean_keyword_groups`），
  数据项目用 `analysis.json.keywords[tone]`。
- 渲染：在 `src/visualization/wordclouds.py` 新增
  `render_frequencies_to_png(frequencies, out_path, *, tone, size=(960, 600))`：
  `wordcloud.WordCloud` + `detect_cjk_font()`（字体缺失时文字为方块，接口照常返回并在
  日志 warning）；正面绿色系、负面红色系；白底、无标题；先写 `.tmp` 再 `os.replace` 原子替换。
- 缓存：视频写入 `outputs/videos/<bvid>/wordcloud_<tone>.png`；
  数据项目写入 `data/uploads/<project_id>/wordcloud_<tone>.png`。
  缓存失效规则：PNG 不存在，或源文件（`<bvid>.json` / `analysis.json`）
  `mtime` 晚于 PNG 时重新渲染；删除任务/项目时随目录一起清理。
- 错误：任务/项目不存在 → 404；该情感没有可用词（< 2 个词）→ 404 中文提示；
  `wordcloud` 未安装 → **503**（detail 给出 `pip install wordcloud` 指引）；
  单个词渲染失败 → 500 并记录日志。前端收到错误后降级回 ECharts 并展示原因。

### 3.4 词量调整

| 常量 | 现值 | 新值 |
| --- | --- | --- |
| `src/videos/placeholder.py: KEYWORDS_PER_SIDE` | 8 | **60** |
| `src/artifacts/pipeline.py: KEYWORD_LIMIT` | 50 | **60** |

契约变更：`detail.keywords.*` 与 `analysis.keywords.*` 每侧最多 60 项（不足时返回实际数量），
`weight`/`count` 语义不变，仍可直接被前端渲染。

## 4. 前端设计

### 4.1 导出按钮

- **数据项目详情**（`ProjectDetail.vue` 底部操作区）：
  - 主按钮「导出清洗结果 CSV」→ `stage=clean`；
  - 次按钮「导出原始爬虫 CSV」→ `initial`、「导出排除记录 CSV」→ `excluded`；
  - 阶段文件不存在（`project.stages[].rows === 0` 或项目未完成）时对应按钮禁用并提示原因；
  - 复用统一下载方法（创建隐藏 `<a href download>` 触发，不新开窗口）。
- **视频任务详情**（`VideoDetail.vue`）：
  - 完成态底部操作条：「导出采集数据 CSV」（crawl）与「导出清洗数据 CSV」（clean）；
  - 等待态 `crawled` 显示采集导出、`cleaned` 显示两个导出，与「开始清洗/开始分析」并排；
  - 未采集（无产物）时不显示。

### 4.2 词云组件（`WordCloud.vue`）

- 新增可选 prop：`imageUrl`（Python 图地址，含 `?tone=&download=false`）。
  有值时卡片头显示分段切换「前端渲染 | Python 图」；无值（如旧数据/mock）时保持现状。
- 默认仍为前端渲染，符合既有行为；切到 Python 时懒加载 `<img>`，
  显示加载态；`@error` 时展示中文错误并自动切回前端渲染。
- Python 模式提供「下载图片」链接（`&download=true`）。
- 图片版本参数 `v`：由 `keywords` 内容确定性生成（词数 + 各词频次之和等，纯函数便于单测），
  避免重新分析后浏览器命中旧图。
- 字号自适应：`sizeRange` 随词数变化（≤15 词 13–40px；>15 词 10–30px），
  `gridSize` 5，卡片高度 260px → 320px，60 词不重叠。

### 4.3 API 客户端

`frontend/src/api/artifacts.js`、`videos.js` 新增 URL 构造函数
（`artifactDownloadUrl(id, stage)`、`videoDownloadUrl(bvid, stage)`、
`artifactWordcloudUrl(id, tone, {v, download})`、`videoWordcloudUrl(...)`），
返回字符串供 `<a>` / `<img>` 使用（不走 fetch、不需要 JSON 解析）。
纯函数放 `frontend/src/utils/exportFiles.js`，便于 Node 单测。

## 5. 错误处理与安全

- 所有下载/图片接口只读，不允许 `..`、绝对路径、任意文件名；`stage` / `tone` 白名单校验。
- `Content-Disposition` 文件名由后端拼接，不含用户上传的原始文件名（防头注入）。
- 词云渲染失败不能影响任务状态与分析结果展示；前端降级回 ECharts。
- 并发请求同一图片时允许重复渲染，但原子写盘保证不会读到半截文件。

## 6. 测试与验收

后端（pytest）：

1. 数据项目导出：三阶段 200/BOM/表头正确；非法 stage 400；不存在项目 404；
   未生成阶段 404；响应头为 attachment。
2. 视频导出：crawl/clean 列名与行数符合契约；BOM；非法 stage 400；未知 bvid 404；
   未采集任务 404。
3. 词量：构造足量文本，断言 `keywords[tone]` 长度为 60；数据项目同理；
   既有「词不拆分标记/URL」用例保持通过。
4. Python 词云图：200 且 `image/png`、PNG 魔数正确；第二次请求命中缓存（mtime 不变）；
   非法 tone 400；任务不存在 404；`wordcloud` 缺失时 503（monkeypatch 模拟）。

前端：

- `npm test`：新增 `exportFiles` 纯函数用例（URL 拼接、文件名、版本号）。
- `npm run build`：产物可构建。

端到端（真实后端 + Vite + 浏览器，Lead 验收）：

- 上传 `data/sample/bilibili_demo.csv` 生成数据项目 → 完成后点击三个导出按钮，
  下载文件行数/表头与页面一致；等待态项目按钮禁用。
- 打开已完成视频任务 `BV16BjHz9EUJ`：词云每侧 60 个词；切换 Python 图正常显示并可下载；
  导出采集/清洗 CSV 行数分别为 2864 / 2792（该任务真实产物）。
- 失败路径：停掉后端时导出/图片按钮给出错误提示，不白屏。

## 7. 影响文件

- 后端：`src/videos/placeholder.py`、`src/artifacts/pipeline.py`、
  `src/artifacts/service.py`、`src/videos/service.py`、
  `src/visualization/wordclouds.py`、`src/inference/app.py`、相关 `__init__.py` 导出。
- 前端：`frontend/src/components/WordCloud.vue`、`ProjectDetail.vue`、`VideoDetail.vue`、
  `frontend/src/api/artifacts.js`、`videos.js`、新增 `frontend/src/utils/exportFiles.js`、
  `frontend/tests/`、`frontend/mock/`（保证 mock 演示不因新按钮报错）。
- 文档：`docs/API契约.md`、`README.md`、`frontend/README.md`（必要时）、本文件。
- 测试：`tests/test_artifacts_api.py` / `tests/test_videos_api.py` 增补或新增
  `tests/test_export_download_stages.py`、`tests/test_wordcloud_image.py`。

## 8. 分工与里程碑

| 任务 | 负责 | 写范围 |
| --- | --- | --- |
| 后端：导出端点 + 词量 60 + Python 渲染端点 + pytest | 队友 backend | `src/`、`tests/` |
| 前端：导出按钮 + 词云切换 + 自适应字号 + 前端单测 | 队友 frontend | `frontend/src/`、`frontend/tests/`、`frontend/mock/` |
| 契约/README 更新、集成、端到端浏览器验收、最终提交 | Lead | `docs/`、`README.md` |

顺序：本设计批准 → 双方并行实现（接口以第 3、4 节为准）→ Lead 集成并跑全量测试 →
浏览器验收 → 更新文档并提交。
