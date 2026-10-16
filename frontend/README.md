# frontend/ — 前端展示（Vue 3 + Vite）

网站用户评论情感分析系统的 Web 界面骨架：单条预测、批量预测、可视化、模型对比。
**当前为课程设计骨架**：界面、接口封装、构建链路已就绪，算法与模型由 Python 端（`src/`）实现。

---

## 一、环境与命令

| 项目 | 值 |
| --- | --- |
| Node.js | ≥ 18（开发环境 v24 已验证） |
| 包管理器 | **npm**（不要使用 pnpm / yarn） |
| 依赖 | 仅 `vue`、`vite`、`@vitejs/plugin-vue`（不含 UI 框架、不含图表库） |
| 开发端口 | **3000**（固定，端口被占用时直接报错而非顺延） |

```bash
cd frontend
npm install       # 安装依赖
npm run dev       # 开发模式 -> http://localhost:3000
npm run build     # 生产构建 -> frontend/dist/
npm run preview   # 预览构建产物 -> http://localhost:3000
```

前端通过 Vite 代理把 `/api`、`/health` 转发到 `http://localhost:8000`，
因此**后端 FastAPI 服务需同时运行**：

```bash
# 仓库根目录
python -m uvicorn src.inference.app:app --host 0.0.0.0 --port 8000 --reload
```

未启动后端时，各页面会显示「待接入后端」提示（骨架阶段**不会**展示任何模拟结果）。

---

## 二、目录结构

```
frontend/
├── index.html              # 入口 HTML（挂载 #app）
├── vite.config.js          # 端口 3000 + /api、/health 代理
├── package.json            # scripts: dev / build / preview
├── .gitignore              # 忽略 node_modules、dist
└── src/
    ├── main.js             # createApp 挂载入口
    ├── App.vue             # 顶部导航 + 动态组件视图切换（未引入 vue-router）
    ├── api/client.js       # 接口契约的唯一封装（4 个调用 + 1 个批量下载路由）
    ├── utils/format.js     # 展示层格式化（标签中文名、小数、百分比）
    ├── assets/main.css     # 基础样式（浅色、干净、可读）
    └── views/
        ├── HomeView.vue           # 首页：项目说明、/health 自检、契约速查
        ├── SinglePredictView.vue  # 单条预测
        ├── BatchPredictView.vue   # 批量预测（上传 + 导出）
        ├── VisualizationView.vue  # 可视化四个占位卡片
        └── ModelCompareView.vue   # 模型对比表格
```

> 说明：接口白名单不含 `vue-router`，因此 `App.vue` 用「顶部导航 + `<component :is>`」实现多视图切换，
> 功能上等价于路由，后续需要 URL 可分享时可再引入 router。

---

## 三、接口契约（已锁定，不得改名）

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

## 四、页面与后端对接状态

| 页面 | 交互 | 调用的接口 | 状态 |
| --- | --- | --- | --- |
| 首页 | `/health` 连通性自检、契约速查表、快捷入口 | GET `/health` | 已封装，待后端 |
| 单条预测 | 文本域 + 开始预测 + 清空；结果区展示极性、置信度、正负概率条 | POST `/api/predict` | 已封装，待后端 |
| 批量预测 | 文件选择（前端校验扩展名）+ 提交；结果表格 + 导出按钮（直接使用 `export_url` 下载） | POST `/api/predict/batch`、GET `/api/predict/batch/download/{file_id}` | 已可用（需后端运行） |
| 可视化 | 四个占位卡片 + 产物文件对照表 | 无（契约中没有图表接口） | 待后端产出 PNG，需决策接入方式 |
| 模型对比 | 指标表格（模型名/类型/Accuracy/P/R/F1/状态）+ 刷新 | GET `/api/models` | 已封装，待后端 |

对应产物路径（命名约定见 `outputs/README.md`）：

| 图表面板 | 产物文件 |
| --- | --- |
| 损失 / 准确率曲线 | `outputs/figures/loss_curve.png`、`accuracy_curve.png` |
| 混淆矩阵热力图 | `outputs/figures/confusion_matrix_*.png` |
| 正负评论词云 | `outputs/figures/wordcloud_positive.png`、`wordcloud_negative.png` |
| Attention 权重 | `outputs/figures/attention_heatmap.png` |

---

## 五、后续要接什么（TODO）

- [ ] 后端就绪后联调接口（4 个调用 + 1 个批量下载路由），核对真实字段与类型（尤其 `label` 取值范围、概率字段是否归一化）。
- [x] 批量上传字段名已锁定为 `file`（与前端 `predictBatch` 默认值一致）。
- [ ] 可视化页接图表：确认是「FastAPI 静态挂载 `outputs/figures/`」还是「新增图表清单接口」。
- [ ] 单条预测：补充阳性/阴性概率条的 0~1 裁剪与异常值兜底（后端返回异常时页面不崩）。
- [ ] 批量预测：大文件（> 数万行）的分页显示或虚拟滚动，避免一次性渲染表格卡顿。
- [ ] 模型对比：接入 ECharts 指标条形图与混淆矩阵热力图（按课程安排自行接入）。
- [ ] 错误处理细化：区分「后端未启动」「请求超时」「后端返回校验失败」三类提示。
- [ ] 视觉/无障碍：导航键盘可达性、表格 `<caption>`、移动端断点微调。
- [ ] 可选：引入 `vue-router` 使视图可分享 URL（当前用动态组件切换）。

---

## 六、构建验证

```bash
cd frontend
npm install
npm run build      # 成功后在 frontend/dist/ 生成 index.html + assets/
ls -la dist        # 确认 dist/index.html 存在
```

构建产物已由 `.gitignore` 忽略（`frontend/dist/`、`node_modules/`），不入库。
