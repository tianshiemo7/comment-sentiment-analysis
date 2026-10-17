/**
 * mock/settings.js — mock 层的设置仓库（真实后端 outputs/settings.json 的替身）
 *
 * 契约：docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md 第 2 节。
 * 只保存在 dev server 进程内存里：重启 dev server 恢复默认值（mock 不落盘，
 * 避免在仓库里伪造运行时产物）；GET/PUT 永远只回 masked_cookie，Cookie 明文不出进程。
 *
 * 默认值、校验范围、错误文案（中文）均与契约保持一致，便于前端在无后端时完整演示。
 */

import { toIso } from './fixtures.js'

/** 采集参数默认值（spec 第 2 节） */
export const DEFAULT_CRAWL = {
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
}

export const DEFAULT_PIPELINE = {
  auto_clean: false,
  auto_analyze: false,
}

const ACCOUNT_MODES = ['anonymous', 'account']
const SORT_MODES = ['time', 'hot']

/** 数值参数范围：key → [min, max|null, 中文名, 是否整数] */
const NUMBER_LIMITS = {
  max_pages: [0, 50, '最大采集页数', true],
  max_sub_pages: [0, 20, '最大楼中楼页数', true],
  page_size: [1, 50, '每页一级评论数', true],
  max_items: [0, null, '最多新增评论数', true],
  delay_seconds: [2, 60, '请求最小间隔（秒）', false],
  // timeout_seconds 只要求 > 0（允许 0.5 这类小数）
  timeout_seconds: [0.1, 60, '单次请求超时（秒）', false],
}

function createDefaultState() {
  return {
    account: { mode: 'anonymous', cookie: '', uname: '', mid: 0 },
    crawl: { ...DEFAULT_CRAWL },
    pipeline: { ...DEFAULT_PIPELINE },
    updated_at: null,
  }
}

let state = createDefaultState()

/** Cookie → 脱敏展示值（形如 SESSD…9f2a） */
export function maskCookie(cookie) {
  const raw = String(cookie || '')
  if (!raw) return ''
  const match = /SESSDATA=([^;]+)/i.exec(raw)
  const value = (match ? match[1] : raw).trim()
  if (!value) return ''
  if (value.length <= 8) return '***'
  return `${value.slice(0, 5)}…${value.slice(-4)}`
}

/** GET/PUT 响应（Cookie 脱敏；只读字段 has_cookie / masked_cookie / uname / mid） */
export function getSettingsPayload() {
  return {
    account: {
      mode: state.account.mode,
      has_cookie: Boolean(state.account.cookie),
      masked_cookie: maskCookie(state.account.cookie),
      uname: state.account.uname,
      mid: state.account.mid,
    },
    crawl: { ...state.crawl },
    pipeline: { ...state.pipeline },
    updated_at: state.updated_at,
  }
}

/** mock 内部（任务提交合并用）：含 Cookie 明文，**不得**返回给 HTTP 响应。 */
export function getSavedSettings() {
  return {
    account: { ...state.account },
    crawl: { ...state.crawl },
    pipeline: { ...state.pipeline },
  }
}

export function hasSavedCookie() {
  return Boolean(state.account.cookie)
}

export function getAccountMode() {
  return state.account.mode
}

function fail(detail) {
  return { ok: false, status: 400, body: { detail } }
}

/**
 * PUT /api/settings 的 mock 实现：部分更新 + 与契约一致的校验与清除凭据规则。
 * @param {object} patch
 */
export function updateSettings(patch = {}) {
  const next = getSavedSettings()
  const body = patch && typeof patch === 'object' ? patch : {}

  // ---------- account ----------
  if (body.account !== undefined) {
    if (body.account === null || typeof body.account !== 'object') return fail('account 必须是对象')
    const account = body.account
    if (account.mode !== undefined && !ACCOUNT_MODES.includes(account.mode)) {
      return fail('账号模式非法：只能是 anonymous 或 account')
    }
    if (account.cookie !== undefined && typeof account.cookie !== 'string') {
      return fail('Cookie 必须是字符串')
    }
    if (typeof account.cookie === 'string' && account.cookie.trim()) {
      next.account.cookie = account.cookie.trim()
      next.account.mode = account.mode || 'account'
      // 新凭据：清掉上一个账号的昵称缓存，等检查登录态后重新写入
      next.account.uname = ''
      next.account.mid = 0
    }
    if (account.mode !== undefined) next.account.mode = account.mode
    if (account.cookie === '' || next.account.mode === 'anonymous') {
      next.account.cookie = ''
      next.account.uname = ''
      next.account.mid = 0
    }
    if (next.account.mode === 'account' && !next.account.cookie) {
      return fail('已选择登录账号模式，但还没有保存 Cookie。请先在设置页填写 B 站 Cookie。')
    }
  }

  // ---------- crawl ----------
  if (body.crawl !== undefined) {
    if (body.crawl === null || typeof body.crawl !== 'object') return fail('crawl 必须是对象')
    for (const [key, value] of Object.entries(body.crawl)) {
      if (key === 'sort_mode') {
        if (!SORT_MODES.includes(value)) return fail('排序方式非法：只能是 time 或 hot')
        next.crawl.sort_mode = value
        continue
      }
      if (key in NUMBER_LIMITS) {
        const [min, max, label, integer] = NUMBER_LIMITS[key]
        const num = Number(value)
        const range = max === null ? `≥ ${min}` : `${min} ~ ${max}`
        if (!Number.isFinite(num) || (integer && !Number.isInteger(num)) || num < min || (max !== null && num > max)) {
          const hint = integer ? `需为 ${range} 的整数` : `需在 ${range} 秒之间`
          return fail(`${label}${hint}`)
        }
        next.crawl[key] = num
        continue
      }
      if (key in DEFAULT_CRAWL) {
        if (typeof value !== 'boolean') return fail(`采集参数 ${key} 必须是布尔值`)
        next.crawl[key] = value
        continue
      }
      return fail(`未知的采集参数：${key}`)
    }
  }

  // ---------- pipeline ----------
  if (body.pipeline !== undefined) {
    if (body.pipeline === null || typeof body.pipeline !== 'object') return fail('pipeline 必须是对象')
    for (const [key, value] of Object.entries(body.pipeline)) {
      if (!(key in DEFAULT_PIPELINE) || typeof value !== 'boolean') {
        return fail(`未知或非法的自动流程参数：${key}`)
      }
      next.pipeline[key] = value
    }
  }

  state = { ...next, updated_at: toIso(new Date()) }
  return { ok: true, status: 200, body: getSettingsPayload() }
}

/**
 * POST /api/crawler/session/check 的 mock 实现（不触网）。
 * - 无 Cookie → 200 匿名态；
 * - 含 SESSDATA= 且长度足够、且不含 invalid/expired → 登录成功，缓存 uname/mid；
 * - 否则 200 is_login=false（契约要求只有网络/接口异常才 502）。
 * @param {string} [cookieInput] 临时凭据（尚未保存时使用）；省略用已保存凭据
 */
export function checkSession(cookieInput) {
  const temp = typeof cookieInput === 'string' ? cookieInput.trim() : ''
  const cookie = temp || state.account.cookie
  if (!cookie) {
    return {
      ok: true,
      status: 200,
      body: {
        mode: 'anonymous',
        has_credentials: false,
        is_login: false,
        uname: '',
        mid: 0,
        message: '当前为匿名模式：尚未提供 Cookie',
      },
    }
  }

  const match = /SESSDATA=([^;]+)/i.exec(cookie)
  const sessionValue = (match ? match[1] : '').trim()
  const valid = sessionValue.length >= 8 && !/invalid|expired/i.test(cookie)
  if (!valid) {
    return {
      ok: true,
      status: 200,
      body: {
        mode: state.account.mode,
        has_credentials: hasSavedCookie(),
        is_login: false,
        uname: '',
        mid: 0,
        message: '凭据无效或已过期（mock 模拟）：请重新获取 B 站 Cookie',
      },
    }
  }

  const uname = '示例用户'
  const mid = 123456
  // 登录成功后缓存到设置（只读字段），与真实后端行为一致
  state.account.uname = uname
  state.account.mid = mid
  if (!temp || temp === state.account.cookie) state.account.mode = 'account'
  return {
    ok: true,
    status: 200,
    body: {
      mode: 'account',
      has_credentials: hasSavedCookie(),
      is_login: true,
      uname,
      mid,
      message: `已登录：${uname}`,
    },
  }
}

/** /__mock/reset 用：恢复默认设置 */
export function resetSettings() {
  state = createDefaultState()
}

/**
 * 「人工登录窗口」成功后写入凭据（mock 专用；真实后端由 Playwright 捕获 Cookie 后写盘）。
 * 只用于 mock/account.js，Cookie 仍只存在内存、接口只回 masked。
 */
export function applyBrowserLogin({ cookie, uname, mid }) {
  state.account = {
    mode: 'account',
    cookie: String(cookie || ''),
    uname: String(uname || ''),
    mid: Number(mid) || 0,
  }
  state.updated_at = toIso(new Date())
}
