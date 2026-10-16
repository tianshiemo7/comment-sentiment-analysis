# frontend/ — 前端展示（Vue 3 + Vite）

B 站视频评论情感分析系统的 Web 界面：
**主界面**（顶部 BV 输入 + 可收起左栏任务列表 + 右侧详情图表/词云），
以及课程要求的单条预测、批量预测、可视化、模型对比四个页面。

B 站主流程的接口契约见 [`../docs/API契约.md`](../docs/API契约.md)（唯一契约来源）。
后端未就绪时，`frontend/mock/` 会按契约返回数据，界面可完整演示；真实后端就绪后删除 `vite.config.js` 里的一行即可。

---

## 一、环境与命令

| 项目 | 值 |
| --- | --- |
| Node.js | ≥ 18（开发环境 v24 已验证） |
| 包管理器 | **npm**（不要使用 pnpm / yarn） |
| 依赖 | `vue`、`vite`、`@vitejs/plugin-vue`、`echarts`、`echarts-wordcloud`（不含 UI 框架） |
| 开发端口 | **3000**（固定，端口被占用时直接报错而非顺延） |

```bash
cd frontend
npm install       # 安装依赖
npm run dev       # 开发模式 -> http://localhost:3000（自带 mock，无需后端）
npm run build     # 生产构建 -> frontend/dist/
npm run preview   # 预览构建产物 -> http://localhost:3000（注意：mock 插件只在 dev 生效）
```

前端通过 Vite 代理把 `/api`、`/health` 转发到 `http://localhost:8000`：

```bash
# 仓库根目录（可选：不启动也能用 mock 演示 B 站主界面）
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload
```

未启动后端时：

- `/api/videos*` 与 `/health` 由 **mock 插件**接管，B 站主界面可完整演示；
- 单条预测 / 批量预测 / 模型对比页面显示「待接入后端」提示（沿用骨架阶段行为，不展示模拟结果）。

---

## 二、目录结构

```
frontend/
├── index.html              # 入口 HTML（挂载 #app，内联 SVG favicon）
├── vite.config.js          # 端口 3000 + /api、/health 代理 + mock 插件接入（删除一行即可关闭 mock）
├── package.json            # scripts: dev / build / preview
├── .gitignore              # 忽略 node_modules、dist
├── mock/                   # ★ 仅开发用的 mock 层（真实后端就绪后可整个删除）
│   ├── index.js            #   Vite 插件 + connect 中间件（路由分发、JSON 解析、/__mock/reset）
│   ├── store.js            #   内存任务仓库（提交/列表/详情/重试/删除）
│   ├── pipeline.js         #   状态机纯函数（queued→crawling→processing→completed，progress/phases）
│   └── fixtures.js         #   演示数据（预置 2 条 + 词云/趋势/评论/模型指标生成）
└── src/
    ├── main.js             # createApp 挂载入口
    ├── App.vue             # 顶部导航 + 动态组件视图切换（未引入 vue-router）
    ├── api/client.js       # 既有接口封装（predict / batch / models / health，未改动）
    ├── api/videos.js       # ★ B 站分析接口封装（/api/videos*，契约见 docs/API契约.md）
    ├── utils/format.js     # 既有展示层格式化（未改动）
    ├── utils/videoFormat.js# ★ 状态文案/色调、BV 校验、时间与数值格式化
    ├── composables/
    │   ├── usePolling.js      # ★ 3 秒轮询器（全部终态停止、页面隐藏暂停、回前台立即刷新）
    │   └── useVideoAnalysis.js# ★ 列表 + 详情 + 提交/重试/删除的状态编排
    ├── components/
    │   ├── BvInput.vue          # 顶部 BV 输入框（即时校验 ^BV[0-9A-Za-z]{10}$）
    │   ├── VideoSidebar.vue     # 左栏（可收起为窄条，收起状态存 localStorage）
    │   ├── VideoListItem.vue    # 单个任务条目（徽章/进度条/评论数/失败重试/删除）
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
    ├── assets/main.css     # 既有基础样式（未改动）
    ├── assets/video.css    # ★ B 站页面布局与图表卡片样式
    └── views/
        ├── BilibiliView.vue          # ★ 新首页：B 站视频评论分析主界面
        ├── HomeView.vue              # 旧骨架首页（已被 BilibiliView 取代，文件保留但不再挂载）
        ├── SinglePredictView.vue     # 单条预测（保留）
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

- 列表每 **3 秒** `GET /api/videos`；`completed` / `failed` 之外存在任务才继续轮询。
- 提交新 BV 或点「重试」后恢复轮询；`document.visibilitychange` 隐藏时暂停，回到前台立即拉一次。
- 详情仅在被选中且未完成时随轮询刷新（静默刷新，不闪 loading 骨架）。

### mock 层

- 位置 `frontend/mock/`，通过 `vite.config.js` 中 `mockApiPlugin()` 接入，只覆盖 `/api/videos*` 与 `/health`；
  其它 `/api/*`（predict / models）仍走代理到 FastAPI，互不影响。
- 预置 2 条演示数据：1 条 `completed`（含完整 sentiment/trend/keywords/top_comments/model）、1 条长期 `crawling`。
- 新提交的 BV：约 30 秒走完 `queued(5s) → crawling(10s) → processing(15s) → completed`。
- 提交 `BV1m0ckfai11` 会走失败分支，用于演示失败态与「重试」。
- 调试接口 `POST /__mock/reset` 可恢复预置数据。
- **真实后端就绪后：删除 `vite.config.js` 里的 `mockApiPlugin(),` 一行即可**（或直接删除 `mock/` 目录），业务代码无需改动。

---

## 四、接口契约（既有 4 个调用，已锁定，不得改名）

契约与仓库根 `README.md`「前后端接口契约」一节一致，前端封装见 [`src/api/client.js`](src/api/client.js)。

| # | 方法 | 路径 | 请求 | 响应 | 前端函数 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/health` | — | `{"status":"ok","service":str,"version":str}` | `getHealth()` |
| 2 | POST | `/api/predict` | `{"text": str}` | `{"text":str,"label":"positive"\|"negative","confidence":float,"probs":{"positive":float,"negative":float}}` | `predict(text)` |
| 3 | POST | `/api/predict/batch` | multipart 文件（`.csv` / `.xlsx`） | `{"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],"export_url":str}` | `predictBatch(file)` |
| 4 | GET | `/api/models` | — | `{"models":[{"name":str,"type":str,"metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},"ready":bool}]}` | `getModels()` |
| 5 | GET | `/api/predict/batch/download/{file_id}` | — | `text/csv` 附件（批量结果导出，Excel 可直接打开，带 UTF-8 BOM；非法文件名 400、不存在 404） | 浏览器直接下载（`<a href download>`），无需封装函数 |

请求约定：

- 前端一律使用**同源相对路径**（如 `/api/predict`），由 Vite 代理转发，不在代码里写死 `localhost:8000`。
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
| **B站分析（首页）** | 顶部 BV 输入（即时校验）+ 左栏任务列表（可收起、状态徽章、进度条、失败重试、删除）+ 右侧详情（未完成只显示进度块；完成显示饼图/折线/双词云/关键词排行/评论/模型指标） | `/api/videos*`（5 个） | 前端已完成，mock 下可完整演示；接真实后端后仅需删 mock 插件 |
| 单条预测 | 文本域 + 开始预测 + 清空；结果区展示极性、置信度、正负概率条 | POST `/api/predict` | 已封装，待后端 |
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

- [ ] **真实后端就绪后**：删除 `vite.config.js` 中 `mockApiPlugin(),` 一行（或删除 `mock/` 目录），
      并联调 `/api/videos*` 真实字段（重点核对 `phases[].status` 取值、`sentiment.*_ratio` 归一化、`trend[].bucket` 粒度）。
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

---

## 七、构建与验收

```bash
cd frontend
npm install
npm run build      # 成功后在 frontend/dist/ 生成 index.html + assets/
ls -la dist        # 确认 dist/index.html 存在
```

B 站主界面（mock 模式，无需后端）：

```bash
npm run dev        # http://localhost:3000
# 预置 2 条演示数据；提交任意合法 BV 号，约 30 秒走完 queued→crawling→processing→completed
# 提交非法 BV（如 BV123）会即时显示中文格式提示且不提交
# 提交 BV1m0ckfai11 可演示失败态与「重试」
```

已用 headless Edge（CDP 驱动）实跑验收：58 项断言全部通过，覆盖状态推进、进行中不渲染图表、
完成后 5 个 ECharts 实例、切换条目后实例 dispose 不泄漏、左栏收起与 localStorage、轮询暂停/恢复/终态停止、
4 个既有页面仍可打开。

构建产物已由 `.gitignore` 忽略（`frontend/dist/`、`node_modules/`），不入库。
