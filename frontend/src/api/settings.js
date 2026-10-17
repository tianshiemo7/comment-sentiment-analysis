/**
 * src/api/settings.js — 「系统设置 / 账号模式」接口封装（与 videos.js / artifacts.js 并列）
 *
 * 契约来源：docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md 第 2 节
 *   1. GET  /api/settings                 -> 设置对象（Cookie 脱敏：只有 has_cookie / masked_cookie）
 *   2. PUT  /api/settings                 {"account"?, "crawl"?, "pipeline"?} 部分更新 -> 同 GET
 *   3. POST /api/crawler/session/check    可选 {"cookie":str}
 *                                         -> {mode, has_credentials, is_login, uname, mid, message}
 *
 * 安全约定：任何响应都不会回传 Cookie 明文；本模块也**不保存**明文（只透传给检查登录态接口）。
 * 错误统一抛出带中文 detail 的 Error，并挂上 err.status 供界面区分 400/502。
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

const BACKEND_HINT = '请确认 FastAPI 服务已启动：python -m uvicorn src.inference.app:app --port 8000'

/**
 * 内置默认设置：与契约（spec 第 2 节）完全一致。
 * GET /api/settings 失败时界面回退到这份默认值并提示后端未启动。
 */
export const DEFAULT_SETTINGS = {
  account: {
    mode: 'anonymous',
    has_cookie: false,
    masked_cookie: '',
    uname: '',
    mid: 0,
  },
  crawl: {
    real_crawl: true,
    max_pages: 1,
    max_sub_pages: 1,
    page_size: 20,
    include_replies: false,
    sort_mode: 'time',
    max_items: 200,
    delay_seconds: 2.5,
    timeout_seconds: 15,
    require_login: false,
    init_buvid: false,
  },
  pipeline: {
    auto_clean: false,
    auto_analyze: false,
  },
  updated_at: null,
}

export const ACCOUNT_MODES = ['anonymous', 'account']
export const SORT_MODES = ['time', 'hot']

/** 数值型采集参数的合法范围（与后端一致；0 表示不限，见契约注释）。 */
export const CRAWL_LIMITS = {
  max_pages: { min: 0, max: 50, label: '最大采集页数' },
  max_sub_pages: { min: 0, max: 20, label: '最大楼中楼页数' },
  page_size: { min: 1, max: 50, label: '每页一级评论数' },
  max_items: { min: 0, max: null, label: '最多新增评论数' },
  delay_seconds: { min: 2, max: 60, label: '请求最小间隔（秒）' },
  // 契约只要求 timeout_seconds > 0（后端允许 0.5 这类小数），前端取 0.1 ~ 60、步进 0.1
  timeout_seconds: { min: 0.1, max: 60, label: '单次请求超时（秒）' },
}

/** 深拷贝一份设置（避免弹窗直接改到共享状态）。 */
export function cloneSettings(settings = DEFAULT_SETTINGS) {
  return JSON.parse(JSON.stringify(settings))
}

function pickNumber(value, fallback) {
  const num = Number(value)
  return Number.isFinite(num) ? num : fallback
}

function pickBoolean(value, fallback) {
  return typeof value === 'boolean' ? value : fallback
}

function pickString(value, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

/**
 * 把任意来源（后端 / mock / 本地默认值）的设置对象规范化：
 * 缺失字段用默认值补齐、数字/布尔/枚举做类型纠正，保证界面拿到的结构永远可用。
 * @param {object} data
 */
export function normalizeSettings(data) {
  const source = data && typeof data === 'object' ? data : {}
  const account = source.account && typeof source.account === 'object' ? source.account : {}
  const crawl = source.crawl && typeof source.crawl === 'object' ? source.crawl : {}
  const pipeline = source.pipeline && typeof source.pipeline === 'object' ? source.pipeline : {}
  const defaults = DEFAULT_SETTINGS

  return {
    account: {
      mode: ACCOUNT_MODES.includes(account.mode) ? account.mode : defaults.account.mode,
      has_cookie: pickBoolean(account.has_cookie, defaults.account.has_cookie),
      masked_cookie: pickString(account.masked_cookie, defaults.account.masked_cookie),
      uname: pickString(account.uname, defaults.account.uname),
      mid: pickNumber(account.mid, defaults.account.mid),
    },
    crawl: {
      real_crawl: pickBoolean(crawl.real_crawl, defaults.crawl.real_crawl),
      max_pages: pickNumber(crawl.max_pages, defaults.crawl.max_pages),
      max_sub_pages: pickNumber(crawl.max_sub_pages, defaults.crawl.max_sub_pages),
      page_size: pickNumber(crawl.page_size, defaults.crawl.page_size),
      include_replies: pickBoolean(crawl.include_replies, defaults.crawl.include_replies),
      sort_mode: SORT_MODES.includes(crawl.sort_mode) ? crawl.sort_mode : defaults.crawl.sort_mode,
      max_items: pickNumber(crawl.max_items, defaults.crawl.max_items),
      delay_seconds: pickNumber(crawl.delay_seconds, defaults.crawl.delay_seconds),
      timeout_seconds: pickNumber(crawl.timeout_seconds, defaults.crawl.timeout_seconds),
      require_login: pickBoolean(crawl.require_login, defaults.crawl.require_login),
      init_buvid: pickBoolean(crawl.init_buvid, defaults.crawl.init_buvid),
    },
    pipeline: {
      auto_clean: pickBoolean(pipeline.auto_clean, defaults.pipeline.auto_clean),
      auto_analyze: pickBoolean(pipeline.auto_analyze, defaults.pipeline.auto_analyze),
    },
    updated_at: typeof source.updated_at === 'string' ? source.updated_at : null,
  }
}

/**
 * 统一请求：解析 JSON、非 2xx 抛错（优先使用后端 detail 文案，均为中文）。
 * @param {string} path
 * @param {RequestInit} [options]
 */
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(path, options)
  } catch (err) {
    throw new Error(`无法连接分析服务 ${path}：${err.message}。${BACKEND_HINT}`)
  }

  const raw = await res.text()
  let data = null
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      data = { detail: raw.slice(0, 300) }
    }
  }

  if (!res.ok) {
    const detail = data && data.detail !== undefined ? data.detail : ''
    const text = detail ? (typeof detail === 'string' ? detail : JSON.stringify(detail)) : `请求失败（HTTP ${res.status}）`
    const error = new Error(text)
    error.status = res.status
    throw error
  }

  return data
}

/** 1) 读取设置（Cookie 只返回脱敏值） */
export function fetchSettings() {
  return request('/api/settings')
}

/**
 * 2) 部分更新设置，语义与契约一致：
 *   - account.cookie 非空 → 保存；account.cookie="" 或 mode="anonymous" → 清除凭据；
 *   - account.mode="account" 且无凭据 → 后端返回 400（中文 detail）。
 * @param {{account?: object, crawl?: object, pipeline?: object}} patch
 */
export function saveSettings(patch) {
  return request('/api/settings', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(patch || {}),
  })
}

/**
 * 3) 检查 B 站登录态。
 * @param {string} [cookie] 可选：临时凭据（尚未保存时用）；省略则用服务端已保存凭据
 * @returns {Promise<{mode:string,has_credentials:boolean,is_login:boolean,uname:string,mid:number,message:string}>}
 */
export function checkLoginStatus(cookie) {
  const body = {}
  if (typeof cookie === 'string' && cookie.trim()) body.cookie = cookie.trim()
  return request('/api/crawler/session/check', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
}

export default { fetchSettings, saveSettings, checkLoginStatus, DEFAULT_SETTINGS, normalizeSettings }
