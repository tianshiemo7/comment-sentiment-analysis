# `src/artifacts/` —— 数据项目与单独评论（后端）

本包实现 `docs/API契约.md` **第四点五节**锁定的两段后端能力：

- **数据项目**：界面右上角「上传爬取数据」→ 落盘 → 自动跑 `clean` 模式预处理管线
  → 产出三张可查看的表 → 占位情感分析 → 本机打开 CSV；
- **单独评论**：把「单条预测」页的结果保存到左栏「单独评论」段（增 / 删 / 查）。

> **占位声明（必须诚实标注）**：本包所有情感结果都来自
> `src.inference.service.predict_rule_based` —— 一个**规则 / 词典占位预测器**，
> **不是训练模型**，准确率远低于真实模型，仅用于骨架演示与接口联调。
> 对外 `method` 字段固定为 `rule_placeholder`，前端据此显示「规则占位」标识。

## 模块分工

| 文件 | 职责 |
| --- | --- |
| `schemas.py` | 契约的代码化：枚举、ID 规则、请求 / 响应模型（字段名严格照契约） |
| `store.py` | 落盘：`data/uploads/<project_id>/` 与 `solo_comments.json`（线程安全 + 原子写） |
| `config.py` | 阶段节奏时长等可配置项（环境变量覆盖） |
| `errors.py` | 业务异常（HTTP 层映射为 400 / 404 / 413） |
| `messages.py` | 状态文案 |
| `pipeline.py` | 后台流水线：clean 管线 → 占位情感 → 聚合统计 |
| `preview.py` | 只读解析 CSV：阶段行列数、分页表格、逐条评论 |
| `opener.py` | 本机打开 CSV（`os.startfile` / `open` / `xdg-open`，可注入假实现） |
| `service.py` | 业务服务：项目 CRUD、单线程队列、启动恢复、单独评论 CRUD |

## 落盘布局（默认根目录 `data/uploads/`，可用 `ARTIFACTS_DIR` 覆盖）

```
data/uploads/
├── <project_id>/
│   ├── upload.json      上传元数据（原始文件名、大小、sha256、上传时间、显示名）
│   ├── project.json     项目状态与阶段清单（对外主状态文件）
│   ├── initial.csv      初始数据（上传原件解析后的统一 CSV，UTF-8 BOM）
│   ├── clean.csv        筛选后-保留（清洗 + 分词后的评论）
│   ├── excluded.csv     筛选后-排除（含 exclude_reason 列）
│   ├── sentiment.csv    逐条情感（占位实现：label / confidence / 概率）
│   └── analysis.json    聚合分析（占比、词频、长度分布、抽样）
└── solo_comments.json   全部单独评论（数组）
```

`initial.csv` / `clean.csv` / `excluded.csv` / `sentiment.csv` 统一为 **UTF-8 with BOM**，
Excel 双击即可正确显示中文。

## 接口清单（契约锁定，不得改名）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/artifacts` | 左栏三段：`projects` / `solo` / `videos` |
| POST | `/api/artifacts/upload` | multipart `files`（可多个）+ 可选 `name`，校验后缀与大小后自动入队 |
| GET | `/api/artifacts/{id}` | 项目对象；不存在 404 |
| POST | `/api/artifacts/{id}/analyze` | 重新处理（清旧产物 + 重置为 queued） |
| GET | `/api/artifacts/{id}/stages` | `{stages:[{key,label,rows,columns}×3]}` |
| GET | `/api/artifacts/{id}/table` | `?stage=&offset=&limit=`，`limit` 上限 500 |
| GET | `/api/artifacts/{id}/comments` | `?offset=&limit=&label=`，逐条占位情感 |
| GET | `/api/artifacts/{id}/analysis` | 占比 / 词频 / 长度分布 / 抽样 |
| GET | `/api/artifacts/{id}/path` | **只读**返回 `{stage,path,exists}`；**绝不调用 opener**（「复制文件地址」用） |
| POST | `/api/artifacts/{id}/open` | 在服务器本机打开某阶段 CSV（会真的调用系统程序） |
| DELETE | `/api/artifacts/{id}` | 删除项目目录 |
| POST | `/api/solo-comments` | 保存一条单独评论 |
| GET | `/api/solo-comments` | 列表（`created_at` 倒序） |
| DELETE | `/api/solo-comments/{id}` | 删除单条；不存在 → 404 |
| DELETE | `/api/solo-comments?ids=a,b` | 批量删除（**幂等**）；`ids` 缺省 / 为空 / 不存在 → 200 且 `deleted_count` 可为 0，只有 id 形态非法才 400 |

## 复用而非重写

| 能力 | 复用来源 |
| --- | --- |
| CSV / TSV / XLSX 解析、统一 CSV 落盘 | `src.preprocessing.dataset`（`load_records` / `save_records`） |
| 清洗 + 分词 + 去重 + 排除原因（clean 模式） | `src.preprocessing.pipeline.prepare_comments` |
| 占位情感预测 | `src.inference.service.predict_rule_based` |
| 后台任务模式（单线程队列 / 状态落盘 / 启动恢复 / 优雅退出） | 与 `src/videos/` 同一套做法 |

清洗阶段把 `prepare_comments` 的输出根指向临时目录，跑完只把
`comments_cleaned.csv` / `comments_excluded.csv` 搬进项目目录并改名为
`clean.csv` / `excluded.csv`；**本包没有重写任何清洗或分词逻辑**。

## 三阶段的真实列名（`data/sample/bilibili_demo.csv`，27 行）

三张表都是 `src/preprocessing/dataset.py:save_records` 写出的 CSV，列来自
「记录字段 + 元信息字段」的并集，因此**列名取决于上传文件带了哪些列**。以仓库自带的
演示样例（列为 `comment_id,video_id,text,label,source`）为例：

| 阶段 | 行 / 列 | 列名 |
| --- | --- | --- |
| `initial` | 27 / 7 | `text`, `label`, `comment_id`, `input_file`, `input_row`, `source`, `video_id` |
| `clean` | 25 / 12 | `text`, `label`, `tokens`, `comment_id`, `input_file`, `input_row`, `provided_label`, `source`, `status`, `text_hash`, `text_raw`, `video_id` |
| `excluded` | 2 / 12 | `text`, `label`, `tokens`, `comment_id`, `exclude_reason`, `input_file`, `input_row`, `source`, `status`, `text_hash`, `text_raw`, `video_id` |

**为什么 `clean`（12 列）比 `initial`（7 列）列更多？**
clean 阶段**在原始列基础上追加派生列，不删列**，新增的 5 列是
`tokens`（分词结果）、`text_raw`（原文）、`provided_label`（上传数据里原本的标注）、
`status`（`ok` / `excluded`）、`text_hash`（去重用的原文哈希）；
`excluded` 再额外多出 `exclude_reason`。所以「清洗」在这里是**标准化 + 派生 + 打标**，
而不是裁剪列——列数变多是预期行为。

**原文链路（已实测）**：`clean.text_raw` 与 `initial.text` 按 `comment_id` 一一对应，
演示样例 25/25 条完全一致（`preview.text_of` 取 `text_raw` 优先）。

> 注：`initial.csv` **不是把上传文件原样拷一份**，而是用
> `src/preprocessing.dataset.load_records` 解析后用 `save_records` 统一写出的
> UTF-8 BOM CSV（多个上传文件会合并成同一张表，列取并集）。

## 安全约定（契约 4.5.3，硬要求）

- `table` / `comments` / `analysis` / `open` **只接受服务端生成的 `id` + 固定 `stage` 枚举**，
  对外绝不接受任何文件路径；
- `project_id` / 评论 `id` 都必须匹配 `^p_…` / `^c_…` 正则，`..`、路径分隔符一律拒绝（404）；
- `open` 额外要求：扩展名必须是 `.csv`，且 `resolve()` 后父目录必须位于
  `data/uploads/` 或 `data/processed/` 之内，否则 400；
- 上传文件名**只取 basename**，落盘固定为 `initial.csv`，绝不用用户文件名做路径。

## 状态机与并发

`queued → processing → completed`（失败 `failed`），`progress` 0~100 单调不减。

- 单 daemon 线程 + `queue.Queue`，不引入 Celery / Redis / APScheduler；
- 状态写入 `project.json`；启动时把残留的 `queued` / `processing` 重置为 `queued` 并重新入队；
- 关停时 `stop_event` 置位，流水线在阶段内立刻退出并把项目恢复成 `queued`；
- `run_id`（代次）防止「重新分析」后旧流水线覆盖新状态；
- 单进程生效（多 worker 各自持有独立队列）。

## 环境变量

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `ARTIFACTS_DIR` | `data/uploads` | 项目与单独评论落盘根目录 |
| `ARTIFACT_CLEAN_SECONDS` | `0.4` | 清洗阶段节奏耗时（秒） |
| `ARTIFACT_SENTIMENT_SECONDS` | `0.5` | 情感阶段节奏耗时（秒） |
| `ARTIFACT_STATS_SECONDS` | `0.3` | 统计阶段节奏耗时（秒） |
| `ARTIFACT_STEPS_PER_STAGE` | `4` | 每阶段推进 `progress` 的步数 |
| `ARTIFACT_SIMULATE_FAILURE` | `0` | 置 `1` 时在情感阶段模拟失败（验证 `failed` 分支） |
| `ARTIFACTS_OPENER` | 空 | 置 `noop` 时不真正调用系统程序（**仅用于自动化验收 / 无桌面环境**） |

## 本机打开（`POST /open`）

- Windows `os.startfile`；macOS `open`；Linux `xdg-open`；
- 测试与自动化验收通过 `opener.set_opener(假实现)` 注入，绝不真的拉起 Excel；
- 服务器无桌面时用 `ARTIFACTS_OPENER=noop`（接口返回结构不变）。

## 已知取舍（写在这里，避免歧义）

1. **`clean.csv` 的 `label` 列通常为空**：clean 模式按 `src/preprocessing` 的既有约定
   「不发明标签」，因此预测结果不会写回 `label`；上传数据里已有的标注会保留在
   `provided_label` 元信息列里，模型/占位预测结果单独写在 `sentiment.csv`，
   并经 `/comments` 与原文对齐返回。
   另外 clean 阶段**在原始列基础上追加派生列（tokens / text_raw / provided_label /
   status / text_hash），不删列**，所以 `clean.csv` 的列数可以多于 `initial.csv`（见上文列名表）。
2. **中性（neutral）**：占位预测器只给 positive / negative 两个方向；
   当它给出的两类概率相等（`0.5 / 0.5`，即没有任何情感词命中、原始情感分为 0）时，
   本项目如实标注为 `neutral`，不凭空编造极性。
3. **`sentiment.csv` 把 `probs` 拆成 `prob_positive` / `prob_negative` 两列**，
   便于在 Excel 中直接阅读；接口 `/comments` 仍返回契约要求的 `probs` 对象。
4. **未处理完时的读接口返回空数据而不是报错**：`table` / `comments` 返回空表，
   `analysis` 返回全 0 且仍带 `method`，避免前端在轮询期间被 4xx 打断。
5. **上传即解析**：文件在请求内完成解析（才能对「无法识别评论文本列」这类错误返回 400），
   解析结果写入 `initial.csv`，后台流水线只做清洗 / 情感 / 统计三段。

## TODO

- 接入真实模型：把 `pipeline.predict_comment` 的数据来源换成
  `src.inference.service.predict_text`（真实前向），`method` 随模型名变化，接口结构不变；
- `analyze` 的 `mode=train`（制备训练集）不在本接口范围内，如需支持再单独设计；
- 单进程队列；如需多 worker 部署，应把队列外置（Redis / DB）。
