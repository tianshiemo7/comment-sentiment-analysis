# 前端美术资源设计 — Remix Icon 内置子集（2026-10-16）

## 一、背景与目标

`frontend/` 的 Vue 3 界面功能已完整（B 站分析主界面 + 单条预测 / 批量预测 / 实验可视化 / 模型对比），
但视觉层**没有任何图形资源**：

- 折叠用的是 `«` `»` `▾` 字符；
- 按钮是纯文字（「重试」「删除」「清空全部」「打开 CSV」「复制文件地址」）；
- 状态与极性只靠彩色文字徽章；
- 左栏收起后的窄条用**条目首字**当头像；
- `index.html` 的 favicon 是一个内联 SVG 的「情」字。

本设计为全站补上一套统一的矢量图标语言，参考 **Remix Icon**（https://remixicon.com）的图形风格。

**目标**：在不新增任何 npm 依赖、不改变任何接口行为与轮询逻辑、不破坏 `frontend/README.md`
已记录的依赖白名单的前提下，把图标覆盖到全站，并给出可复现的验收方式。

## 二、许可证约束（关键前置发现）

Remix Icon 在 **2026 年 1 月**更换了许可证。npm 包 `remixicon@4.9.1` 的 `package.json`
仍写着 `"license": "Apache-2.0"`，但包内 `License` 文件已经是 **Remix Icon License v1.0**
（Version 1.0 – January 2026，Copyright (c) 2017–2026 Remix Design）。**以 `License` 文件为准。**

对本项目有决定影响的三条：

| 条款 | 内容 | 对本项目的结论 |
| --- | --- | --- |
| 2.1 / 2.2 | 允许在网站、Web 应用、UI 设计系统中使用；允许修改颜色、尺寸、描边等视觉属性 | 全站当作功能与装饰元素使用、改色改尺寸，**允许** |
| 3.3 | **禁止**将图标用作 logo、商标、品牌标识，或作为公司/产品/服务的主要视觉识别 | **品牌标识与 favicon 不得使用任何 Remix 图形**，必须保持原创 |
| 4.1 | 品牌图标（Logos 类）仅可用于指代或链接对应品牌本身 | `ri-bilibili-line` 仅可用于代表 B 站内容的入口，不得当作本项目标识 |

第 5 条要求：只在分发**完整图标库或其实质部分**时保留版权声明；项目内单独使用不需要。
本项目只取 48 个图标（总数 3229 个，约 1.5%），不构成"实质部分"，
但仍主动随源码附带 `NOTICE.txt`（许可证全文 + 出处 + 版本 + 获取日期），
并在 `frontend/README.md` 与根 `README.md` 中致谢，避免课程审查时对来源与授权产生疑问。

## 三、交付形态：只存 path 数据，不引入依赖

Remix Icon 的 `*-line` 图标文件结构统一为**单条 `<path>` 的纯填充路径**，不含
`<circle>` / `<rect>` / `<g>` 等元素（`scripts/gen_icons.mjs` 对每个目标图标强制校验 `paths=1, other=0, viewBox=24×24`）：

```svg
<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg"><path d="M4 12C4 7.58..."/></svg>
```

因此不落地 `.svg` 文件、也不使用 `v-html`，而是**只保存 `d` 字符串**：

```
frontend/src/assets/icons/
├── icons.js      # export const ICON_PATHS = { 'play-circle-line': 'M12 2C6.47...Z', ... }
├── NOTICE.txt    # Remix Icon License v1.0 全文 + 出处/版本/日期
└── ../components/Icon.vue   # <Icon name="refresh-line" :size="16" label="重试" />
```

`Icon.vue` 渲染：

```html
<svg viewBox="0 0 24 24" fill="currentColor" :width="size" :height="size" aria-hidden="true" focusable="false">
  <path :d="d" />
</svg>
```

选这个形态的理由：

1. **零新增依赖** —— `frontend/README.md` 记录的依赖白名单（`vue`、`vite`、`@vitejs/plugin-vue`、
   `echarts`、`echarts-wordcloud`，不含 UI 框架）**保持不变**，CI 的 `npm ci` 不受影响；
2. **零网络请求、零字体闪烁** —— 不像图标字体需要加载 woff2，也没有 FOUT；
3. **体积可控** —— 48 条 path 共 22.8 KB 源码，gzip 后约 8.6 KB（实测，含注释）；
4. **颜色天然正确** —— `fill="currentColor"` 让图标自动继承所在容器的文字颜色，
   不需要为每种语义色维护一套图标变体；
5. **可 tree-shake** —— 纯 ESM 常量对象，Vite 构建时按需保留。

**回退策略**：`name` 未命中时渲染一个中性圆环占位图，并在 `import.meta.env.DEV` 下
`console.warn` 提示具体名字，避免拼错图标名导致静默渲染空白。

## 四、图标清单（48 个，全部经校验为单条 path、无其它元素）

> 实际清单以 `scripts/gen_icons.mjs` 里的 `GROUPS` 为准（唯一事实来源）；下表与之一致。

| 区域 | 图标名 |
| --- | --- |
| 顶部导航（5 项） | `bilibili-line` `chat-1-line` `file-list-3-line` `bar-chart-2-line` `stack-line` |
| 左栏骨架 | `arrow-left-s-line` `arrow-right-s-line` `arrow-down-s-line` `folder-3-line` `chat-quote-line` `film-line` |
| 状态语义 | `time-line`（排队）`download-cloud-2-line`（爬取）`loader-4-line`（处理中，旋转）`checkbox-circle-line`（完成）`error-warning-line`（失败） |
| 情感极性 | `emotion-happy-line` `emotion-unhappy-line` `emotion-normal-line` `question-line` |
| 结果来源 | `flask-line`（规则占位）`robot-2-line`（模型） |
| 条目操作 | `refresh-line` `restart-line` `delete-bin-6-line` `close-line` |
| 文件与数据 | `file-excel-2-line` `file-copy-2-line` `file-3-line` `database-2-line` `upload-2-line` `upload-cloud-2-line` `download-2-line` |
| 图表卡片 | `pie-chart-2-line` `line-chart-line` `cloud-line` `bar-chart-horizontal-line` `table-line` |
| 页面控件与空态 | `play-line` `play-circle-line` `send-plane-2-line` `eraser-line` `heart-pulse-line` `cpu-line` `image-line` `pulse-line` `alert-line` |
| 评论与指标 | `thumb-up-line`（替换 `CommentList` 里的 👍 emoji，避免各平台 emoji 字形不一致） |

## 五、视觉规则

**风格**：单色线稿（Remix `*-line` 风格），`fill="currentColor"`，图标自身绝不写死颜色。

**尺寸**：`size` 默认 16。行内文字旁 14；工具栏按钮 15；左栏条目操作 14；空态大图 40；
图表卡片标题 16。图标用 `vertical-align: -0.125em` 对齐中文基线，并 `flex: 0 0 auto` 防压扁。

**图标 + 文字 vs 纯图标**：

- 主操作按钮（提交、上传、开始预测、导出、打开 CSV 等）**保留全部现有中文文案**，仅在左侧加图标，
  通过 `display: inline-flex; align-items: center; gap: 6px` 排版；
- 条目上的紧凑控件（删除、重试、复制路径、清空全部、关闭弹窗）改为**纯图标按钮**，
  但**必须**同时提供 `title` 与 `aria-label`，命中区不小于 26×26；
- 左栏折叠箭头由 `«` `»` `▾` 字符改为 `arrow-left-s-line` / `arrow-right-s-line` / `arrow-down-s-line`。

**动效**：只有「处理中」的 `loader-4-line` 旋转（`1s linear infinite`）；
`@media (prefers-reduced-motion: reduce)` 下停止旋转。不引入任何其他动画。

**无障碍**：装饰性图标一律 `aria-hidden="true" focusable="false"`；
传入 `label` 时改为 `role="img"` 并渲染 `<title>`；纯图标按钮的 `aria-label` 与 `title` 保持一致。

**品牌标识与 favicon 保持原创**：现存的「情」字方块标记保留，仅做描边、圆角与投影的细节打磨。
这是许可证第 3.3 条的红线，不做妥协。

## 六、改动文件

**新增（4）**

- `frontend/src/assets/icons/icons.js`（48 个图标，由仓库内 `scripts/gen_icons.mjs` 从官方包抽取生成，可重跑且带一致性校验）
- `frontend/src/assets/icons/NOTICE.txt`
- `frontend/src/components/Icon.vue`
- `scripts/gen_icons.mjs`（抽取脚本；`--check` 在「代码引用了清单外的图标名」时非零退出）

**实现期调整（相对本设计初稿）**

- 图标数由 49 定为 **48**：删掉 `external-link-line`、`file-search-line`、`inbox-archive-line`
  三个最终未被任何界面引用的图标（YAGNI），并补进 `play-circle-line` —— 初稿清单漏了它，
  空态首次运行时已触发占位回退；`scripts/gen_icons.mjs --check` 正是为拦住这类拼写错误而加。
- 新增 `thumb-up-line`，替换 `CommentList.vue` 里的 `👍` emoji（emoji 字形随平台字体变化）。
- 原计划给全局 `.hint` 加 flex，实测会破坏含 `<code>` 的行内提示换行，改为新增可选类
  `.hint--icon`，只在确实带图标的提示条上使用。
- 品牌标记由「情感」二字改为单字「情」（28×28 方块内更稳），仍为原创图形。

**样式（2）**

- `frontend/src/assets/main.css` —— `.btn` 改为内联 flex；新增 `.icon`、`.icon-btn`、`.icon--spin` 工具类
- `frontend/src/assets/video.css` —— 图表卡片标题与空态相关微调

**接入组件与页面（约 20）**

`App.vue`、`BvInput.vue`、`UploadArtifactsDialog.vue`、`SidebarSection.vue`、`VideoSidebar.vue`、
`VideoListItem.vue`、`ProjectListItem.vue`、`SoloCommentItem.vue`、`SentimentBadge.vue`、
`MethodBadge.vue`、`VideoStatusBadge.vue`、`VideoDetail.vue`、`VideoHeader.vue`、`CommentList.vue`、
`ModelMetrics.vue`、`VideoProgress.vue`、`ProjectDetail.vue`、`SoloCommentDetail.vue`、
`charts/{SentimentPie,TrendLine,WordCloud,KeywordBar}.vue`、
`views/{BilibiliView,SinglePredictView,BatchPredictView,VisualizationView,ModelCompareView}.vue`、
`index.html`（favicon 细节）。

**文档（2）**

- `frontend/README.md` —— 新增「美术资源与许可」一节 + 目录结构补 3 个新文件 + 依赖白名单保持不变的说明
- `README.md`（根）—— 致谢与许可说明

## 七、验收标准

1. `npm run build` 成功；产物体积增量以 **gzip 后**为准：**实测 +9.9 KB**
   （Vite 报告：JS 30.09 → 39.69 kB、CSS 6.84 → 6.94 kB、html 0.73 → 0.97 kB）。
   原始体积 **+28,080 字节**，其中 `icons.js` 单独占 22.8 KB；CSS 反而小了约 60 B，
   因为自定义的圆点 / `×` 按钮样式被共用的 `.icon` / `.icon-btn` 取代。
   基线取自 `git worktree add --detach ... ccdd3c8` 的构建结果，可复现。
2. dev（mock 模式）下用 CDP 无头浏览器逐页截图：5 个视图 × 左栏折叠/展开 × 空态/有数据态，
   确认无布局错位、无图标挤压文字、无换行错乱。
3. 自动化断言：页面内 `<svg>` 数量 > 0；控制台 **0** 个 JS 错误；
   `/api/artifacts`、`/api/videos` 的请求条数与轮询节奏与改动前一致（纯视觉改动不得触碰数据逻辑）。
4. 逐条核对：纯图标按钮均有 `title` + `aria-label`；`prefers-reduced-motion: reduce` 下无旋转动画。
5. `frontend/README.md` 记录的依赖白名单与实际 `package.json` 仍然一致（无新增依赖）。

## 八、明确不做（YAGNI）

- 不引入 `remixicon` npm 包或图标字体；
- 不引入任何 UI 框架 / CSS 框架；
- 不改配色体系、不改字体、不改布局骨架、不改任何接口调用或轮询逻辑；
- 不做「图标总览」预览页（本次不需要）；
- 不为品牌标识与 favicon 设计新图形（许可证禁止）。
