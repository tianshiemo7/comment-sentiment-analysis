# frontend/ — 前端展示（Vue 3 + Vite）

B 站视频评论情感分析系统的 Web 界面：
**主界面**（顶部 BV 输入 + 可收起左栏**三段**：数据项目 / 单独评论 / 视频任务 + 右侧三类详情视图），
以及课程要求的单条预测、批量预测、可视化、模型对比四个页面。

B 站主流程的接口契约见 [`../docs/API契约.md`](../docs/API契约.md)（唯一契约来源，第四点五节为数据项目与单独评论）。
**默认直连真实 FastAPI 后端（:8000）**；没有后端、只想演示界面时，用 `VITE_USE_MOCK=1 npm run dev`
显式启用 `frontend/mock/`（按契约返回演示数据，默认不加载）。

---

## 一、环境与命令

| 项目 | 值 |
| --- | --- |
| Node.js | ≥ 18（开发环境 v24 已验证） |
| 包管理器 | **npm**（不要使用 pnpm / yarn） |
| 依赖 | `vue`、`vite`、`@vitejs/plugin-vue`、`echarts`、`echarts-wordcloud`（不含 UI 框架） |
| 美术资源 | **未新增任何依赖**：48 个 Remix Icon 线稿图标以 path 数据内置在 `src/assets/icons/`，不引入图标库 npm 包、也不加载图标字体 |
| 开发端口 | **3000**（固定，端口被占用时直接报错而非顺延） |

```bash
cd frontend
npm install                    # 安装依赖
npm run dev                    # 开发模式 -> http://localhost:3000（默认走 proxy 连真实后端 :8000）
VITE_USE_MOCK=1 npm run dev    # 演示模式：启用 mock、无需后端（仅 dev 生效；PowerShell 用 $env:VITE_USE_MOCK=1; npm run dev）
npm run build                  # 生产构建 -> frontend/dist/
npm run preview                # 预览 dist/ -> http://localhost:3000（始终走 proxy 连真实后端，mock 不生效）
```

前端通过 Vite 代理把 `/api`、`/health` 转发到 `http://localhost:8000`，所以默认需要先启动后端：

```bash
# 仓库根目录
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload
```

**默认行为（不设 `VITE_USE_MOCK`）**：不启用 mock，所有 `/api/*` 与 `/health` 都走代理到真实 FastAPI；
后端未启动时页面显示「待接入后端」/请求失败提示（不展示模拟结果）。

演示模式 `VITE_USE_MOCK=1 npm run dev` 下，以下路径由 **mock 插件**接管：
`/api/videos*`、`/api/artifacts*`、`/api/solo-comments*`、`/api/predict`、`/api/settings*`、
`/api/crawler/session/check` 与 `/health`，无需后端即可完整演示 B 站主界面（含左栏三段、
数据项目三张表与聚合图表）、单条预测与「保存到左栏」。
批量预测、模型对比、批量导出下载在 mock 模式下仍走代理到 FastAPI，未启动后端时显示「待接入后端」提示（不展示模拟结果）。

---

## 二、目录结构

```
frontend/
├── index.html              # 入口 HTML（挂载 #app，内联 SVG favicon）
├── vite.config.js          # 端口 3000 + /api、/health 代理；mock 仅 VITE_USE_MOCK=1 时接入
├── package.json            # scripts: dev / build / preview
├── .gitignore              # 忽略 node_modules、dist
├── mock/                   # ★ 仅演示模式（VITE_USE_MOCK=1）使用的 mock 层，默认不加载
│   ├── index.js            #   Vite 插件 + connect 中间件（路由分发、multipart/JSON 解析、/__mock/reset）
│   ├── store.js            #   视频任务内存仓库（提交/列表/详情/重试/删除）
│   ├── pipeline.js         #   状态机纯函数（queued→crawling→processing→completed，progress/phases）
│   ├── fixtures.js         #   视频演示数据（预置 2 条 + 词云/趋势/评论/模型指标生成）
│   ├── artifacts.js        #   ★ 数据项目 + 单独评论仓库（上传/三张表/逐条情感/聚合/path/open/删除）
│   ├── artifactFixtures.js #   ★ 数据项目演示数据（三张表、逐条情感、关键词、长度分布）
│   └── predict.js          #   ★ POST /api/predict 的规则占位实现（响应含 method）
└── src/
    ├── main.js             # createApp 挂载入口
    ├── App.vue             # 顶部导航 + 动态组件视图切换（未引入 vue-router）
    ├── api/client.js       # 既有接口封装（predict / batch / models / health）
    ├── api/videos.js       # ★ B 站分析接口封装（/api/videos*，契约见 docs/API契约.md）
    ├── api/artifacts.js    # ★ 数据项目/单独评论封装（/api/artifacts*、/api/solo-comments*）
    ├── utils/format.js     # 既有展示层格式化
    ├── utils/videoFormat.js# ★ 状态文案/色调、BV 校验、时间与数值格式化
    ├── utils/sentimentLabels.js # ★ 情感标签文案/配色（含 neutral）
    ├── composables/
    │   ├── usePolling.js       # ★ 3 秒轮询器（全部终态停止、页面隐藏暂停、回前台立即刷新）
    │   ├── useVideoAnalysis.js # ★ 视频列表 + 详情 + 提交/重试/删除（列表 ref 支持外部注入）
    │   ├── useArtifacts.js     # ★ 左栏三段唯一数据源（一次 GET /api/artifacts）+ 上传/删除/清空
    │   └── useArtifactDetail.js# ★ 数据项目详情（三张表分页、逐条评论分页筛选、聚合、path/open）
    ├── components/
    │   ├── Icon.vue             # ★ 统一图标组件（内联 SVG，零依赖，currentColor 继承颜色）
    │   ├── BvInput.vue          # 顶部 BV 输入框（即时校验 ^BV[0-9A-Za-z]{10}$）
    │   ├── VideoSidebar.vue     # ★ 左栏三段（各段可收起、整栏可收起为窄条，状态存 localStorage）
    │   ├── SidebarSection.vue   # ★ 可收起的一段（标题/计数/进行中数量/操作插槽）
    │   ├── ProjectListItem.vue  # ★ 数据项目条目（名称/初始行→结果行/状态徽章/进度条/删除）
    │   ├── SoloCommentItem.vue  # ★ 单独评论条目（截断原文/极性徽章/置信度/删除）
    │   ├── ProjectDetail.vue    # ★ 数据项目详情（三张表 + 逐条评论 + 图表 + 底部三按钮）
    │   ├── SoloCommentDetail.vue# ★ 单独评论详情（只有文字与概率条，不渲染任何图表）
    │   ├── UploadArtifactsDialog.vue # ★ 上传爬取数据弹窗（多选文件 + 可选项目名）
    │   ├── MethodBadge.vue      # ★ method 标识（rule_placeholder → 醒目「规则占位」）
    │   ├── SentimentBadge.vue   # ★ 正/负/中性极性徽章
    │   ├── ProbBars.vue         # ★ 概率条（按后端 probs 的键动态渲染）
    │   ├── VideoListItem.vue    # 单个视频任务条目（徽章/进度条/评论数/失败重试/删除）
    │   ├── VideoStatusBadge.vue # 状态徽章（灰/蓝/橙/绿/红）
    │   ├── VideoProgress.vue    # 未完成时的「处理中」大状态块（环形进度 + phases 三态）
    │   ├── VideoHeader.vue      # 详情信息头（标题/UP/BV/评论数/发布时间/爬取时间）
    │   ├── VideoDetail.vue      # 详情区状态分派（未完成 / 失败 / 已完成 / 未选中空态）
    │   ├── CommentList.vue      # 代表性评论列表
    │   ├── ModelMetrics.vue     # 模型指标卡片
    │   └── charts/
    │       ├── EChart.vue        # ECharts 基类（init / setOption / resize / onBeforeUnmount dispose）
    │       ├── SentimentPie.vue  # 情感占比饼图
    │       ├── TrendLine.vue     # 情感趋势折线图
    │       ├── WordCloud.vue     # 词云（echarts-wordcloud，字号由 count 控制）
    │       └── KeywordBar.vue    # 关键词排行 Top 15 横向条形图
    ├── assets/main.css     # 既有基础样式（新增 .icon / .icon-btn / .icon--spin / .hint--icon 工具类）
    ├── assets/video.css    # ★ B 站页面布局与图表卡片样式
    ├── assets/icons/       # ★ 美术资源：icons.js（48 个图标 path 数据）+ NOTICE.txt（许可全文）
    └── views/
        ├── BilibiliView.vue          # ★ 主界面：顶部入口 + 左栏三段 + 三类详情视图
        ├── HomeView.vue              # 旧骨架首页（已被 BilibiliView 取代，文件保留但不再挂载）
        ├── SinglePredictView.vue     # 单条预测（含「保存到左栏」开关，默认开启）
        ├── BatchPredictView.vue     # 批量预测（保留）
        ├── VisualizationView.vue     # 可视化四个占位卡片（保留）
        └── ModelCompareView.vue      # 模型对比表格（保留）
```

> 说明：接口白名单不含 `vue-router`，因此 `App.vue` 用「顶部导航 + `<component :is>`」实现多视图切换，
> 功能上等价于路由，后续需要 URL 可分享时可再引入 router。
> 导航顺序：**B站分析（首页）** / 单条预测 / 批量预测 / 实验可视化 / 模型对比。

---

## 三、B 站分析接口契约（唯一来源：`docs/API契约.md`）

前端封装见 [`src/api/videos.js`](src/api/videos.js)；字段名与 `docs/API契约.md` 完全一致，任一侧都不得改名。

| # | 方法 | 路径 | 前端函数 |
| --- | --- | --- | --- |
| 1 | POST | `/api/videos` | `submitVideo(bvid)` |
| 2 | GET | `/api/videos` | `listVideos()` |
| 3 | GET | `/api/videos/{bvid}/detail` | `getVideoDetail(bvid)` |
| 4 | POST | `/api/videos/{bvid}/retry` | `retryVideo(bvid)` |
| 5 | DELETE | `/api/videos/{bvid}` | `deleteVideo(bvid)` |

状态语义（左栏 / 详情区文案）与轮询策略（3 秒、全部终态停止、页面隐藏暂停）见契约第一、三节。

### 轮询约定（已实现）

- 左栏三段的**列表**每 **3 秒** `GET /api/artifacts`（一次请求提供 projects/solo/videos）；`completed` / `failed`
  之外存在任务才继续轮询（终态判断统一用 `utils/videoFormat.js` 的 `isTerminal`）。
- 视频详情仅在被选中且未完成时随轮询刷新（静默刷新，不闪 loading 骨架）；
  `useVideoAnalysis` 的列表 ref 由 `useArtifacts` 注入，因此不会重复请求 `/api/videos` 列表。
- 提交新 BV / 重试 / 上传新项目后恢复轮询；`document.visibilitychange` 隐藏时暂停，回到前台立即拉一次。

### 三·五、数据项目与单独评论接口（`docs/API契约.md` 第四点五节）

前端封装见 [`src/api/artifacts.js`](src/api/artifacts.js)。

| # | 方法 | 路径 | 前端函数 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | GET | `/api/artifacts` | `listArtifacts()` | 左栏三段唯一数据源（groups ×3） |
| 2 | POST | `/api/artifacts/upload` | `uploadArtifacts(files, name)` | multipart：`files`（多选）+ 可选 `name` |
| 3 | GET | `/api/artifacts/{id}` | `getArtifact(id)` | 项目对象 |
| 4 | POST | `/api/artifacts/{id}/analyze` | `analyzeArtifact(id)` | 「重新处理」 |
| 5 | GET | `/api/artifacts/{id}/stages` | `getArtifactStages(id)` | 阶段清单 |
| 6 | GET | `/api/artifacts/{id}/table?stage&offset&limit` | `getArtifactTable(id, q)` | 三张表分页（列名动态渲染） |
| 7 | GET | `/api/artifacts/{id}/comments?offset&limit&label` | `getArtifactComments(id, q)` | 逐条评论情感 + label 筛选 |
| 8 | GET | `/api/artifacts/{id}/analysis` | `getArtifactAnalysis(id)` | 占比 / 词频 / 长度分布（`method` 标识） |
| 9 | POST | `/api/artifacts/{id}/open` | `openArtifact(id, stage)` | 「打开 CSV」：**会调用系统 opener** |
| 10 | GET | `/api/artifacts/{id}/path?stage=clean` | `getArtifactPath(id, stage)` | 「复制文件地址」：**只读取路径，不调用 opener、不弹 Excel**；`exists:false` 时页面提示「该阶段文件不存在」 |
| 11 | DELETE | `/api/artifacts/{id}` | `deleteArtifact(id)` | 删除项目 |
| 12 | POST | `/api/solo-comments` | `saveSoloComment(payload)` | 保存单条评论（`payload.method` 取自 `/api/predict` 响应） |
| 13 | GET | `/api/solo-comments?offset&limit` | `listSoloComments(q)` | 单独评论列表（倒序） |
| 14 | DELETE | `/api/solo-comments/{id}` | `deleteSoloComment(id)` | 单条删除 |
| 15 | DELETE | `/api/solo-comments?ids=a,b` | `deleteSoloComments(ids)` | 「清空全部」 |

### mock 层

- 位置 `frontend/mock/`，仅在 `VITE_USE_MOCK=1 npm run dev` 时通过 `vite.config.js` 中的
  `mockApiPlugin()` 接入（插件 `apply: 'serve'`，`npm run preview` 与生产构建均不生效），覆盖
  `/api/videos*`、`/api/artifacts*`（含 `path`）、`/api/solo-comments*`、`POST /api/predict` 与 `/health`；
  其它 `/api/*`（batch、models、批量下载）仍走代理到 FastAPI，互不影响。
- 预置数据：视频 1 条 `completed` + 1 条长期 `crawling`；数据项目 1 条 `completed`（三张表 312/256/41 + 聚合分析）+ 1 条长期 `processing`；
  单独评论 2 条。
- 新提交的 BV：约 30 秒走完 `queued(5s) → crawling(10s) → processing(15s) → completed`；提交 `BV1m0ckfai11` 走失败分支。
- 上传的数据项目：`queued(1.5s) → processing(13s) → completed`，完成后三张表与聚合分析可用；
  `GET /{id}/path` 在未完成时返回 `exists:false`，演示「该阶段文件不存在」提示。
- `POST /api/predict` 在 mock 下由 `mock/predict.js` 的**规则占位**实现返回
  `{text,label,confidence,probs,method}`，其中 `method` 当前为 `rule_placeholder`
  （真实后端已同样返回该字段；未来为模型名），供「保存到左栏」原样透传。
- 调试接口 `POST /__mock/reset` 可恢复全部预置数据。
- **默认联调真实后端**：不设 `VITE_USE_MOCK` 就完全不加载 mock，`/api/*`、`/health` 全部走 proxy 到 :8000；
  如需彻底移除 mock，删除 `vite.config.js` 中的 `mockApiPlugin()` 引用与 `mock/` 目录即可，业务代码无需改动。

---

## 四、接口契约（既有 4 个调用，已锁定，不得改名）

契约与仓库根 `README.md`「前后端接口契约」一节一致，前端封装见 [`src/api/client.js`](src/api/client.js)。

| # | 方法 | 路径 | 请求 | 响应 | 前端函数 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/health` | — | `{"status":"ok","service":str,"version":str}` | `getHealth()` |
| 2 | POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float},"method":str}` | `predict(text)` |
| 3 | POST | `/api/predict/batch` | multipart 文件（`.csv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` | `predictBatch(file)` |
| 4 | GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` | `getModels()` |
| 5 | GET | `/api/predict/batch/download/{file_id}` | — | `text/csv` 附件（批量结果导出，Excel 可直接打开，带 UTF-8 BOM；非法文件名 400、不存在 404） | 浏览器直接下载（`<a href download>`），无需封装函数 |

请求约定：

- 前端一律使用**同源相对路径**（如 `/api/predict`），由 Vite 代理转发，不在代码里写死 `localhost:8000`。
- `POST /api/predict` 的响应新增 **`method`** 字段（结果来源）：当前为 `rule_placeholder`，真实模型接入后为模型名。
  「单条预测」页的「保存到左栏」开关会把它**原样透传**给 `POST /api/solo-comments`；
  仅当老后端确实不返回该字段时才回退为 `rule_placeholder`。
- 批量上传的 multipart 字段名默认为 `file`（`predictBatch(file, fieldName = 'file')` 可覆盖），
  **后端需按 `file` 读取**；如后端命名不同请同步修改此默认值。
- 上传时不要手动设置 `Content-Type`，浏览器自动补 multipart boundary。
- 非 2xx 响应会抛出中文 `Error`（含状态码与后端 `detail`），页面据此显示错误/待接入提示。
- 批量接口的 `export_url` 由后端**总是返回非 null** 字符串（结果文件落盘到 `outputs/predictions/`）；
  前端直接把该 URL 绑到 `<a :href download>` 下载，无需再拼路径。下载路由为
  `GET /api/predict/batch/download/{file_id}`（返回 `text/csv` 附件，Excel 可直接打开，带 UTF-8 BOM；
  非法文件名 400、不存在 404）。

---

## 五、页面与后端对接状态

| 页面 | 交互 | 调用的接口 | 状态 |
| --- | --- | --- | --- |
| **B站分析（首页）** | 顶部 BV 输入（即时校验）+ 右上角「上传爬取数据」+ 左栏**三段**（数据项目 / 单独评论 / 视频任务，各段可收起、状态存 localStorage）+ 右侧按选中类型渲染三类详情 | `/api/artifacts`（三段唯一列表源）、`/api/videos*`、`/api/artifacts*`、`/api/solo-comments*` | 前端已完成，默认接真实后端；演示模式（`VITE_USE_MOCK=1`）可完整演示 |
| ↳ 视频任务详情 | 未完成只显示进度块；完成显示饼图/折线/双词云/关键词排行/评论/模型指标 | `/api/videos/{bvid}/detail`、retry、delete | 原行为保留（已回归验证） |
| ↳ 数据项目详情 | 头部（名称/初始行→结果行/来源文件/method 标识）+ 三张表切换与分页（列名动态）+ 可展开逐条评论（分页 + label 筛选）+ 占比饼图/词云/长度分布 + **页面最底部**三按钮 | `/api/artifacts/{id}/table|comments|analysis|path|open|analyze` | 前端已完成，默认接真实后端（method 按后端返回值动态显示） |
| ↳ 单独评论详情 | **只渲染**原文（大字号）+ 极性徽章 + 置信度 + 概率条 + 时间 + method 标识，**不渲染任何图表** | 数据来自 `GET /api/artifacts` 的 `groups.solo` | 前端已完成 |
| 单条预测 | 文本域 + 开始预测 + 清空 + **「保存到左栏」开关（默认开启）**；结果区展示极性、置信度、正负概率条 | POST `/api/predict`、POST `/api/solo-comments`（开关开启时） | 已封装，默认接真实后端（`method` 原样透传） |
| 批量预测 | 文件选择（前端校验扩展名）+ 提交；结果表格 + 导出按钮（直接使用 `export_url` 下载） | POST `/api/predict/batch`、GET `/api/predict/batch/download/{file_id}` | 已可用（需后端运行） |
| 实验可视化 | 四个占位卡片 + 产物文件对照表 | 无（契约中没有图表接口） | 待后端产出 PNG，需决策接入方式 |
| 模型对比 | 指标表格（模型名/类型/Accuracy/P/R/F1/状态）+ 刷新 | GET `/api/models` | 已封装，待后端 |

> 旧骨架首页 `views/HomeView.vue` 已被 `BilibiliView.vue` 取代，文件保留但不再挂载，可后续删除。

对应产物路径（命名约定见 `outputs/README.md`）：

| 图表面板 | 产物文件 |
| --- | --- |
| 损失 / 准确率曲线 | `outputs/figures/loss_curve.png`、`accuracy_curve.png` |
| 混淆矩阵热力图 | `outputs/figures/confusion_matrix_*.png` |
| 正负评论词云 | `outputs/figures/wordcloud_positive.png`、`wordcloud_negative.png` |
| Attention 权重 | `outputs/figures/attention_heatmap.png` |

---

## 六、后续要接什么（TODO）

- [x] **mock 默认关闭**（2026-10-30）：`npm run dev` 默认走 proxy 连真实后端（:8000）；
      mock 仅在显式 `VITE_USE_MOCK=1` 时启用（dev 内演示用）。
- [ ] **真实后端就绪后**：联调 `/api/videos*` 真实字段（重点核对 `phases[].status` 取值、`sentiment.*_ratio` 归一化、`trend[].bucket` 粒度）。
- [ ] 旧首页 `views/HomeView.vue`（已不挂载）确认无用后删除。
- [ ] mock 层的 `analysis.phases` 为契约外的附加字段（契约示例只在列表项给出 phases）——真实后端若不返回，
      详情区已回退使用列表项的 `phases`，需与后端确认是否保留。
- [ ] 后端就绪后联调既有接口（4 个调用 + 1 个批量下载路由），核对真实字段与类型（尤其 `label` 取值范围、概率字段是否归一化）。
- [x] 批量上传字段名已锁定为 `file`（与前端 `predictBatch` 默认值一致）。
- [ ] 可视化页接图表：确认是「FastAPI 静态挂载 `outputs/figures/`」还是「新增图表清单接口」。
- [ ] 单条预测：补充阳性/阴性概率条的 0~1 裁剪与异常值兜底（后端返回异常时页面不崩）。
- [ ] 批量预测：大文件（> 数万行）的分页显示或虚拟滚动，避免一次性渲染表格卡顿。
- [ ] 模型对比：接入 ECharts 指标条形图与混淆矩阵热力图（按课程安排自行接入）。
- [ ] 错误处理细化：区分「后端未启动」「请求超时」「后端返回校验失败」三类提示。
- [ ] 视觉/无障碍：导航键盘可达性、表格 `<caption>`、移动端断点微调。
- [ ] 可选：引入 `vue-router` 使视图可分享 URL（当前用动态组件切换）。
- [ ] 图标新增/替换时：改 `scripts/gen_icons.mjs` 的 `GROUPS` 后重跑
      `node scripts/gen_icons.mjs <remixicon 包目录>`，并跑一次 `--check` 校验（见第七节）。

---

## 六·五、美术资源与许可

界面图标取自 **[Remix Icon](https://remixicon.com)** v4.9.1，共 **48 个线稿图标**。

### 接入方式：只存 path 数据，零依赖

```
src/assets/icons/
├── icons.js      # 由 scripts/gen_icons.mjs 生成的 { '图标名': 'M12 …Z' } 映射（约 22 KB）
└── NOTICE.txt    # Remix Icon License v1.0 全文 + 出处 / 版本 / 抽取日期
src/components/Icon.vue   # <Icon name="refresh-line" :size="14" label="重试" />
```

Remix Icon 的 `*-line` 图标都是**单条 `<path>` 的纯填充路径**（无 `circle`/`rect`/`g`），
所以只保留 `d` 字符串即可，`Icon.vue` 用
`<svg viewBox="0 0 24 24" fill="currentColor"><path :d="…"/></svg>` 渲染：

- **不新增 npm 依赖** —— 依赖白名单与 CI 的 `npm ci` 均不受影响；
- **不加载图标字体** —— 没有 woff2 请求，也没有图标字体的首屏闪烁（FOUT）；
- **颜色自动正确** —— `fill="currentColor"` 让图标继承所在容器的文字颜色；
- 未知图标名回退为一个中性圆环占位，并在 DEV 下 `console.warn` 报出具体名字。

### 使用规则（新增界面时请沿用）

| 场景 | 做法 |
| --- | --- |
| 主操作按钮 | 保留中文文案，左侧加图标（`Icon` + 文本，`.btn` 已改为内联 flex，间距 6px） |
| 条目上的紧凑控件（删除 / 重试 / 复制路径 / 清空 / 关闭） | 用 `.icon-btn`（必要时 `.icon-btn--danger`）做**纯图标按钮**，**必须**同时给 `title` 与 `aria-label`，命中区 26×26 |
| 状态 / 极性 / 结果来源 | 图标 + 文字徽章，颜色由徽章的 tone 类决定，图标自身不上色 |
| 「处理中」类进行中状态 | `:spin="true"`，`prefers-reduced-motion: reduce` 下自动停转 |
| 装饰性图标 | 默认 `aria-hidden="true" focusable="false"`；传了 `label` 才输出 `role="img"` + `<title>` |

### 许可要点（Remix Icon License v1.0）

- 第 2.1 / 2.2 条：允许在 Web 界面中作功能与装饰元素使用，允许改颜色与尺寸 —— 本项目属此范围；
- 第 3.3 条：**禁止**将图标用作 logo / 商标 / 品牌标识 —— 因此本项目的品牌标记（「情」字方格）
  与 favicon 均为**原创**，未使用任何 Remix 图形，详见 `index.html` 中的注释；
- 第 4.1 条：品牌图标仅可用于指代对应品牌 —— 本项目只用 `bilibili-line` 一个 Logos 类图标，
  且仅出现在「B站分析」入口（代表 B 站内容），未用作项目标识，也未暗示背书或隶属关系。

### 增删图标

```bash
cd /tmp && npm pack remixicon@4.9.1 && tar xzf remixicon-4.9.1.tgz   # 取官方包（不装进 package.json）
cd <仓库根目录>
node scripts/gen_icons.mjs /tmp/package          # 重新生成 icons.js
node scripts/gen_icons.mjs /tmp/package --check  # 只校验：清单内的图标都在包里、代码用到的都在清单里
```

`--check` 会在「代码里用了一个不在清单里的图标名」时以非零码退出并打印出处文件 ——
这类拼写错误此前只会静默回退成占位圆环，加了这道校验后能在构建前拦住。

### 验收（2026-10-16，Playwright + headless Chromium）

在 mock 模式下实跑 **24/24 通过**，覆盖：5 个视图都渲染图标、页面内无空 path 的图标、
左栏整栏与三段收起/展开、①/②/③ 三类详情与上传弹窗、纯图标按钮 `aria-label` + `title` 齐全、
`prefers-reduced-motion` 下无旋转、无「未知图标名」告警、无未捕获异常。
脚本在仓库内可复现：`scripts/verify_frontend_icons.py`（用法见第七节末）。

其中两条已知**预期**现象，不是回归：

- `GET /api/models`（契约里不在 mock 覆盖范围内）在后端未启动时经 Vite 代理返回 **500**，
  页面按既有设计显示「待接入后端：请求 /api/models 失败（HTTP 500）」——本次改动未触碰
  `src/api/`、`mock/`、`vite.config.js`；
- ②段「单独评论」详情按契约**不渲染任何图表**，`canvas` 计数为 **0**；
  页面上只有标题左侧的界面图标（内联 `<svg class="icon">`，非图表）。



## 七、构建与验收

```bash
cd frontend
npm install
npm run build      # 成功后在 frontend/dist/ 生成 index.html + assets/
ls -la dist        # 确认 dist/index.html 存在
```

B 站主界面默认联调真实后端（先按第一节启动 FastAPI :8000）：

```bash
npm run dev        # http://localhost:3000，/api 与 /health 经 proxy 转发到 :8000
```

B 站主界面演示（mock 模式，无需后端）：

```bash
VITE_USE_MOCK=1 npm run dev    # PowerShell: $env:VITE_USE_MOCK=1; npm run dev
# 预置：视频 2 条（1 completed + 1 crawling）、数据项目 2 条（1 completed + 1 processing）、单独评论 2 条
# 提交任意合法 BV 号，约 30 秒走完 queued→crawling→processing→completed
# 提交非法 BV（如 BV123）会即时显示中文格式提示且不提交
# 提交 BV1m0ckfai11 可演示失败态与「重试」
# 右上角「上传爬取数据」上传 CSV/XLSX（可多选 + 可选项目名），约 15 秒后三张表与聚合图表可用
# 「单条预测」页「保存到左栏」开关默认开启，预测成功后左栏②段新增一条
```

已用 headless Edge（CDP 驱动）实跑验收（截图与逐条断言见交付报告）：

- 主验收 **37/37**：左栏三段渲染、①段三张表切换与翻页/跳页、列名动态渲染、逐条评论展开与筛选、
  饼图/词云 canvas、三个操作按钮位于页面最底部、②段详情**图表 canvas=0**（该视图按契约不渲染图表；
  标题左侧的界面图标是内联 `<svg class="icon">`，不计入 canvas）、开关保存后②段新增、
  上传后轮询到完成、三段收起/展开与 localStorage、无页面级 JS 错误。
- ③段回归 **6/6**：视频详情 5 个 ECharts 实例、提交新 BV 状态推进、非法 BV 拦截、失败态重试按钮；
  详情轮询快照 排队中 → 爬取中 9/21/33% → 处理中 45/56/67/77/88% → 已完成（补拉到位）。
- 增量验收：点「复制文件地址」只发 `GET /{id}/path`（`/open` 请求数为 0）、点「打开 CSV」才走 `POST /open`；
  「保存到左栏」的 `method` 与 `/api/predict` 响应一致（老后端缺字段时回退 `rule_placeholder`）。
- ECharts 实例在 `onBeforeUnmount` dispose，切换详情后存活实例数归零（`window.__CSA_ECHARTS_ALIVE__`，仅 DEV 登记）。

构建产物已由 `.gitignore` 忽略（`frontend/dist/`、`node_modules/`），不入库。

### 图标验收脚本（可复现）

上面「六·五」节的 24 项断言由仓库内脚本自动跑，不需要手工点：

```bash
cd frontend && VITE_USE_MOCK=1 npm run dev       # 先起前端（演示模式，无需后端）
pip install playwright && playwright install chromium   # 仅在首次需要
python ../scripts/verify_frontend_icons.py       # 默认打 http://localhost:3000
```

脚本会逐页截图、写 `report.txt`（UTF-8），全部通过时退出码为 0。
截图默认落在系统临时目录（避免污染仓库），可用 `--out ./shots` 改。

> 该脚本**不参与 CI**：CI 只跑后端 pytest 与前端 `npm run build`；
> playwright 也不是 `requirements.txt` 里的依赖，属本地/发版前的人工验收工具。
