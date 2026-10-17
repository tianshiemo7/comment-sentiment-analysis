# 设置入口 + 爬虫参数 GUI + 分步流水线 设计（2026-10-17）

> 本文件是实现前的**接口约定**，后端、前端、文档三个子任务都以本文件为准。
> 实现过程中如需偏离，必须先通知 Lead 并同步修改本文件。

## 1. 目标

1. **左上角品牌标记「情」改为设置入口**：点击打开「系统设置」弹窗（取代原品牌方块的位置）。
   顶部同时显示当前账号模式（匿名 / 已登录用户名）。
2. **初始为匿名爬取**；设置页可切换到「登录账号」。提供两种方式：
   **推荐「弹出登录窗口、由人操作登录」**（后端在本机打开真实浏览器窗口，用户扫码 /
   输入账号密码 + 验证码完成登录，后端自动捕获 Cookie）；也保留手填 Cookie 的兜底方式。
   凭据只落在服务端本地文件，接口永不回传明文。
3. **爬虫开始前弹框确认参数**：点击「开始分析」不再直接提交，而是先弹出「爬虫参数」弹窗，
   参数从设置页默认值预填，用户可逐次覆盖；确认后才把 `settings` 随任务提交到后端。
4. **爬取完不立刻清洗、清洗完不立刻分析**：流水线在阶段边界暂停为
   `crawled`（待清洗）/ `cleaned`（待分析），由前端按钮触发下一步；
   设置页可分别开启「自动清洗」「自动分析」实现连续执行。
5. README 与 `docs/API契约.md` 同步更新，说明「Python 需要参数的场景改为前端弹窗询问、
   传后端运行」的新交互。

## 2. 账号与设置模型（服务端唯一真相）

设置落盘：`outputs/settings.json`（默认，已被 .gitignore 的 `outputs/` 覆盖；
   可用环境变量 `CSA_SETTINGS_PATH` 覆盖）。原子写入，失败不抛给 HTTP。
**Cookie 明文只存在于该文件；`GET /api/settings` 只返回 `has_cookie` / `masked_cookie`。**

```jsonc
{
  "account": {
    "mode": "anonymous",        // anonymous | account
    "has_cookie": false,        // 只读
    "masked_cookie": "",        // 只读，形如 "SESSD…9f2a"
    "uname": "",                // 最近一次检查登录态成功的昵称（只读缓存）
    "mid": 0                    // 最近一次检查登录态成功的 mid（只读缓存）
  },
  "crawl": {
    "real_crawl": true,         // true=真实爬虫；false=占位数据（离线演示）
    "max_pages": 1,             // 0~50，0=不限（与爬虫 CLI 一致）
    "max_sub_pages": 1,         // 0~20，0=不限
    "page_size": 20,            // 1~50，一级评论每页条数
    "include_replies": false,   // 楼中楼
    "sort_mode": "time",        // time | hot
    "max_items": 200,           // ≥0，0=不限；本次任务最多新增评论数
    "delay_seconds": 2.5,       // 2.0~60，请求最小间隔（限速硬性要求）
    "timeout_seconds": 15,      // >0，单次请求超时
    "require_login": false,     // true 时未登录直接失败（建议登录账号后开启）
    "init_buvid": false         // 是否初始化 buvid3/buvid4 设备指纹
  },
  "pipeline": {
    "auto_clean": false,        // 采集完成后自动清洗
    "auto_analyze": false       // 清洗完成后自动情感分析
  },
  "updated_at": "2026-10-17T18:00:00+08:00"
}
```

默认值即上表（匿名、真实采集、关闭自动连续）。设置文件不存在时按默认值返回。

### 2.1 `GET /api/settings`

200，返回上面的对象（Cookie 脱敏）。不存在的字段以默认值补齐；非法/损坏文件回退默认值。

### 2.2 `PUT /api/settings`

请求体为**部分更新**（缺省字段保持不变，只做浅层嵌套合并）：

```jsonc
{
  "account": { "mode": "account", "cookie": "SESSDATA=...; bili_jct=..." },
  "crawl": { "max_pages": 3, "include_replies": true },
  "pipeline": { "auto_clean": true, "auto_analyze": false }
}
```

规则：

- `account.cookie` 非空 → 保存；`account.cookie: ""` 或 `account.mode == "anonymous"` → 清除凭据；
- `account.mode == "account"` 但没有凭据 → **400**（`detail` 中文提示去设置页填写 Cookie）；
- 数值越界、枚举非法 → **400**（不做 422 之外的宽松裁剪；`detail` 带具体字段与范围）；
- 成功返回与 `GET /api/settings` 相同结构（脱敏）。

### 2.3 `POST /api/crawler/session/check`

请求体可省略：

```jsonc
{ "cookie": "SESSDATA=...;" }   // 可选：用临时凭据检查（尚未保存时使用）
```

- 未提供时用已保存凭据；都没有 → 200 返回匿名态（`is_login=false`），不报错；
- 调用爬虫真实登录态接口（`BiliClient.login_status`）；网络/接口异常 → **502** + 中文原因；
- 200 响应：

```jsonc
{
  "mode": "account",          // anonymous | account
  "has_credentials": true,
  "is_login": true,
  "uname": "示例用户",
  "mid": 123456,
  "message": "已登录：示例用户"
}
```

登录成功后把 `uname` / `mid` 缓存进设置文件（只读字段）。

### 2.4 人工登录窗口（推荐方式，2026-10-17 追加）

> 背景：B 站密码登录会触发极验点选验证码，**不允许/不适合由后端自动破解**，
> 因此「登录账号」的默认交互改为：**前端点一下按钮，后端在运行服务的那台机器上
> 弹出一个真实浏览器窗口，由人来完成扫码 / 密码 + 验证码登录**；后端只负责等待、
> 捕获 Cookie 并写入设置。Cookie 获取成功后窗口自动关闭。

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/account/login/start` | 打开登录窗口并启动捕获；已有窗口在等待时直接返回当前状态（幂等） |
| `GET` | `/api/account/login/status` | 轮询登录窗口状态（前端 2 秒一次） |
| `POST` | `/api/account/login/cancel` | 取消本次登录并关闭窗口 |

状态对象（start / status / cancel 共用）：

```jsonc
{
  "state": "idle",        // idle | opening | waiting | success | failed | cancelled | timeout
  "message": "尚未开始登录",
  "uname": "",            // success 后为 B 站昵称
  "mid": 0,
  "has_cookie": false,    // 设置中是否已有可用 Cookie
  "started_at": null,     // ISO8601 或 null
  "updated_at": "2026-10-17T18:00:00+08:00"
}
```

语义：

- `start`：在后台线程启动 Playwright（优先系统 Edge `channel="msedge"`，其次 Chromium），
  打开 `https://passport.bilibili.com/login` 并置前；状态 `opening → waiting`；
  浏览器依赖缺失时返回 **503**，`detail` 说明安装命令（`pip install playwright` +
  `python -m playwright install chromium`，或安装 Edge/Chrome）；
- 捕获线程每 2 秒读一次 Cookie，出现 `SESSDATA` 后调用
  `https://api.bilibili.com/x/web-interface/nav` 取 `uname` / `mid`，把 Cookie 写入设置
  （`account.mode="account"`、`uname`、`mid`），状态 `success`，随后关闭浏览器；
- 用户关闭窗口 → `cancelled`；超过 300 秒 → `timeout`；异常 → `failed` + 可读原因；
- 同一时间只允许一个登录窗口；重复 `start` 返回当前状态；
- 窗口打开在**后端所在机器**的桌面（本地课程项目即用户本机）；README 必须写清这一点；
- Cookie 永不通过接口回传明文；日志不得打印 Cookie。

依赖与兼容：

- `requirements.txt` 增加可选依赖 `playwright`（注释说明仅「人工登录窗口」需要）；
  未安装时其它功能完全不受影响，`start` 返回 503 并给出安装指引；
- 测试用注入的伪浏览器驱动覆盖状态机（不真正拉起窗口、不触网），另可用
  monkeypatch 模拟 503。

## 3. 任务提交与分步流水线

### 3.1 状态枚举扩展

在原有 5 个状态基础上**新增两个等待态**（其余不变，旧客户端可忽略新状态）：

| 状态 | 含义 | 是否自动轮询 |
| --- | --- | --- |
| `queued` / `crawling` / `processing` | 进行中 | 是 |
| **`crawled`** | 采集完成，等待用户点「开始清洗」 | 否（等待操作） |
| **`cleaned`** | 清洗完成，等待用户点「开始分析」 | 否（等待操作） |
| `completed` / `failed` | 终态 | 否 |

流水线阶段仍为 `采集评论 → 文本清洗 → 情感推理 → 统计分析`（phases 名称不变）。

状态迁移：

```
queued → crawling → [采集完成]
                        ├─ auto_clean=true  → processing（清洗）→ [清洗完成]
                        └─ auto_clean=false → crawled（暂停）
crawled →（POST continue）→ queued → processing（清洗）→ [清洗完成]
                        ├─ auto_analyze=true  → processing（情感推理→统计分析）→ completed
                        └─ auto_analyze=false → cleaned（暂停）
cleaned →（POST continue）→ queued → processing（情感推理→统计分析）→ completed
任意阶段异常 → failed（error 带可读原因）；retry 清空中间产物、回到 queued。
```

### 3.2 `POST /api/videos`（扩展请求体，向后兼容）

```jsonc
{
  "bvid": "BV1xx411c7mD",
  "settings": {                        // 可选；省略 = 旧行为（占位流水线一次跑完，不暂停）
    "account": { "mode": "anonymous" },// 只用模式；Cookie 永远取服务端设置，不随任务传
    "crawl": { "real_crawl": true, "max_pages": 2, "include_replies": true },
    "pipeline": { "auto_clean": false, "auto_analyze": false }
  }
}
```

- `settings` 与已保存设置做**部分合并**后作为本任务快照（不含 Cookie）写入任务记录；
- 任务记录新增内部字段：`settings_snapshot`（不含 Cookie）、`resume_stage`
  （`crawl` / `clean` / `analyze`）、`stage_state`；响应不暴露 Cookie；
- 响应结构不变：`{"bvid","status","progress","created_at"}`。

### 3.3 `POST /api/videos/{bvid}/continue`

- 仅当任务处于 `crawled` / `cleaned` 时允许；
- 其他状态 → **409**，`detail` 形如「任务当前状态 queued，无需继续」；
- 未知 bvid → **404**；
- 成功：重置为 `queued`、`progress=0`、phases 保留已完成阶段，重新入队，
  响应同 `POST /api/videos`（`status="queued"`）。

### 3.4 详情响应新增字段（additive）

`GET /api/videos/{bvid}/detail` 顶层新增 `pipeline` 对象（未完成时也返回）：

```jsonc
"pipeline": {
  "account_mode": "anonymous",
  "auto_clean": false,
  "auto_analyze": false,
  "next_action": "clean",       // null | "clean" | "analyze"
  "crawl": { "real_crawl": true, "max_pages": 1, "...": "..." }
}
```

`analysis.status` 直接使用新状态枚举；`analysis.message` 为「采集完成，等待清洗」/
「清洗完成，等待情感分析」等中文提示。

### 3.5 中间产物与恢复

- 任务级中间产物落盘到 `outputs/videos/<bvid>/`（详情文件仍是 `outputs/videos/<bvid>.json`）：
  `crawl.json`（原始评论或占位计数）、`clean.json`（清洗后评论）；
- 服务重启：`queued/crawling/processing` 任务重置为 `queued` 重新跑；
  `crawled/cleaned` **保持等待态**，等待用户操作；
- `retry` 清空中间产物与 `resume_stage`，从头开始；
- 自动连续开启时不允许在中间态停留（除非该阶段失败）。

## 4. 后端实现约束

1. **旧行为零回归**：`POST /api/videos` 不带 `settings` 时，走原有「一次跑完四个阶段」
   路径（全部现有 57 个 videos 测试必须继续通过）。新增行为只在 `settings` 存在时启用。
2. 账号凭据注入：给 `BiliClient` 增加可选 `cookie` 参数（与 `BILI_COOKIE` 环境变量同格式，
   逐任务注入，不读全局环境写回），`crawler_adapter.VideoCrawlConfig` 增加
   `cookie / page_size / sort_mode / init_buvid / require_login` 字段并透传；日志与错误信息
   必须脱敏（不得出现完整 Cookie）。
3. `init_buvid` 透传 `BiliClient(init_buvid=...)`；`sort_mode` 映射 `time→2 / hot→3`。
4. 清洗阶段真实执行：调用 `src/preprocessing/clean.clean_text`（去 URL/HTML/特殊字符、
   规范化空白）并对空文本去重；情感阶段对每条清洗后评论调用
   `src.inference.service.predict_rule_based`；统计阶段复用 `placeholder.build_detail`
   的聚合逻辑（传入评论记录）生成 detail。
5. 占位模式（`real_crawl=false`）也遵守阶段暂停：采集阶段只产生确定性计划条数，
   清洗阶段空操作，分析阶段生成占位 detail。
6. 安全：设置文件写盘权限尽量收紧（`0o600`，Windows 上失败忽略）；任何响应/日志不得含
   完整 Cookie；任务索引文件不得存 Cookie。
7. 新增单元/接口测试覆盖：设置脱敏与部分更新、越界 400、匿名/账号模式、session check
   （注入假客户端，不触网）、提交带 settings、暂停→continue→completed、auto 连续、
   409/404、retry 清理、重启恢复等待态。
8. 所有新代码风格与现有模块一致（中文 docstring、类型标注、dataclass/Pydantic）。
9. **人工登录窗口（2.4）**：新增 `src/videos/browser_login.py`（单例管理器 + 可注入驱动），
   在 `src/inference/app.py` 暴露 3 个接口；用 daemon 线程，不阻塞事件循环；Playwright
   import 延迟到使用时（未安装不影响应用启动）；`POST /api/videos` 等既有逻辑不变；
   `requirements.txt` 增加可选 `playwright` 依赖注释；测试注入假驱动，覆盖
   opening→waiting→success、cancel、timeout、依赖缺失 503、重复 start 幂等。

## 5. 前端实现约束

1. `App.vue`：把 `.brand__mark` 的「情」替换为齿轮按钮（`aria-label="设置"`），
   旁边显示账号模式徽标（匿名 / 已登录用户名）；点击打开 `SettingsDialog.vue`。
2. 新增 `components/SettingsDialog.vue`：分组展示「账号」「采集参数」「自动流程」，
   含「检查登录状态」「保存」「取消」「恢复默认」；保存调用 `PUT /api/settings`；
   初始加载 `GET /api/settings`；失败时用内置默认值并提示后端未启动。
3. 新增 `components/CrawlLaunchDialog.vue`：BvInput 提交前弹出，预填当前设置中的采集参数，
   展示账号模式与匿名提示；确认后 `POST /api/videos` 带 `settings`；取消不提交。
   `BvInput` 保持只负责输入与校验，由 `BilibiliView` 控制弹窗。
4. 等待态交互：`VideoDetail` 在 `crawled` 时显示「开始清洗」按钮、`cleaned` 时显示
   「开始分析」按钮，并提示「自动连续已关闭」；调用
   `POST /api/videos/{bvid}/continue` 后恢复轮询。`VideoSidebar` / `VideoStatusBadge` /
   `videoFormat.js` 增加「待清洗 / 待分析」文案与色调。
5. 轮询：`crawled` / `cleaned` 视为已停（不再自动刷新），continue / retry / submit 后恢复。
6. mock 层（`frontend/mock/`）同步支持 `GET/PUT /api/settings`、
   `POST /api/crawler/session/check`、`POST /api/videos/{bvid}/continue` 与新状态，
   保证 `npm run dev`（默认 mock）可完整演示；默认设置与后端一致（真实采集在 mock 中
   用占位数据模拟，不触网）。
7. 图标：新增 settings / user / lock 等图标走 `scripts/gen_icons.mjs` 生成流程
   （网络不可用时手工补 path 并同步更新 GROUPS 清单与注释）。
8. `npm run build` 必须通过；不引入新 npm 依赖。
9. **人工登录窗口（2.4）**：设置弹窗「账号」区增加主按钮「登录 B 站账号（弹出登录窗口）」，
   点击后 `POST /api/account/login/start`，并用 2 秒轮询 `GET /api/account/login/status`：
   显示「已打开登录窗口，请在运行后端的电脑上完成扫码/登录」；`success` 后自动切换为
   「已登录：{uname}」并刷新 `GET /api/settings`；支持「取消登录」；`503` 时显示
   Playwright 安装指引；窗口打开期间按钮禁用并有 loading 状态。手填 Cookie 作为
   「高级：手动粘贴 Cookie」折叠项保留。

## 6. 文档更新

- `README.md`：设置入口与账号模式、爬虫参数弹窗、分步流水线 + 自动连续开关、
  新增 API 一览、状态表更新；新增「十四、设置入口与分步执行（2026-10-17）」章节，
  含界面操作步骤与匿名/登录 Cookie 获取说明（合规提示）；修订「六、前后端接口契约」。
- `docs/API契约.md`：新增设置与分步流水线小节，更新状态枚举与提交请求体。
- `docs/大规模采集运行手册.md`：增加「Web 界面方式」与 CLI 参数对照表。
- `.env.example`：`CSA_SETTINGS_PATH`、`VIDEO_REAL_CRAWL` 说明更新（任务级 settings 优先）。

补充（2026-10-17 追加）：README 必须写清「登录账号 = 前端点击按钮 → 后端在本机弹出浏览器
窗口 → 由人扫码/输入账号完成登录 → 自动保存 Cookie」这一推荐流程，以及手填 Cookie 兜底、
Playwright 可选依赖安装（`pip install playwright` / `python -m playwright install chromium`，
Windows 已装 Edge 可直接用 channel=msedge）、窗口出现在后端机器桌面等限制。

## 7. 验收

- `python -m pytest tests/ -q` 全绿（含新增测试）；
- `cd frontend && npm run build` 成功；
- 后端 + 前端联调：匿名/账号设置保存、检查登录、提交带参数任务、
  `crawled`→开始清洗→`cleaned`→开始分析→`completed` 全链路可走通
  （用 `real_crawl=false` 的离线占位模式验证，避免测试期间真实联网）；
- `git status` 中不出现 Cookie / 运行时产物。
