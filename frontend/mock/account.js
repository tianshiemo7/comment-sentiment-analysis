/**
 * mock/account.js — 「人工登录窗口」mock 状态机（spec 2.4）
 *
 * 真实后端用 Playwright 在本机弹出 B 站登录窗口、由人扫码/输入账号后捕获 Cookie；
 * mock 不拉起浏览器、不触网，只按时间推进状态，保证默认 `npm run dev` 下可完整演示：
 *   POST /api/account/login/start   → opening
 *   GET  /api/account/login/status  → 约 1 秒后 waiting，约 3 秒后 success（示例用户）
 *   POST /api/account/login/cancel  → cancelled
 * 另外支持通过 start 请求体模拟 timeout / failed（仅 mock，调试用）：
 *   { "mock_success_after_ms": 5000 }  自定义成功时间
 *   { "mock_timeout_after_ms": 3000 }  让本轮在 3 秒后超时
 *   { "mock_fail": "依赖缺失（mock 模拟）" } 让本轮失败
 *
 * 登录成功后把 Cookie 写入 mock 设置（account.mode=account / uname / mid），
 * 与真实后端一致；接口只回 masked_cookie。
 */

import { applyBrowserLogin, hasSavedCookie } from './settings.js'
import { toIso } from './fixtures.js'

const RUNNING = new Set(['opening', 'waiting'])
/** mock 默认：启动后 ~1s 进入 waiting，~3s 登录成功 */
const DEFAULT_OPEN_DELAY_MS = 800
const DEFAULT_SUCCESS_DELAY_MS = 3_000
/** 契约：等待 300 秒超时 */
const DEFAULT_TIMEOUT_MS = 300_000

let session = createIdle()

function createIdle() {
  return {
    state: 'idle',
    message: '尚未开始登录',
    uname: '',
    mid: 0,
    started_at_ms: null,
    updated_at_ms: null,
    open_at_ms: 0,
    success_at_ms: 0,
    timeout_at_ms: 0,
    fail_message: '',
  }
}

function isRunning() {
  return RUNNING.has(session.state)
}

/** 对外状态对象（与契约字段一致；has_cookie 反映设置里的凭据） */
function payload() {
  return {
    state: session.state,
    message: session.message,
    uname: session.uname,
    mid: session.mid,
    has_cookie: hasSavedCookie(),
    started_at: session.started_at_ms ? toIso(new Date(session.started_at_ms)) : null,
    updated_at: toIso(new Date(session.updated_at_ms || Date.now())),
  }
}

/** 按时间推进状态机：opening → waiting → success / timeout / failed */
function advance() {
  if (!isRunning()) return
  const now = Date.now()
  session.updated_at_ms = now

  if (now >= session.timeout_at_ms) {
    session.state = 'timeout'
    const seconds = Math.max(1, Math.round((session.timeout_at_ms - session.started_at_ms) / 1000))
    session.message = `登录窗口等待超时（${seconds} 秒）：请重新点击「登录 B 站账号」再试`
    return
  }
  if (session.fail_message) {
    session.state = 'failed'
    session.message = session.fail_message
    return
  }
  if (now >= session.success_at_ms) {
    // mock：不触网直接认为登录成功，写入示例凭据
    applyBrowserLogin({
      cookie: 'SESSDATA=mockhumanlogin9f2a; bili_jct=mockcsrf',
      uname: '示例用户',
      mid: 123456,
    })
    session.state = 'success'
    session.uname = '示例用户'
    session.mid = 123456
    session.message = '已登录：示例用户'
    return
  }
  if (session.state === 'opening' && now >= session.open_at_ms) {
    session.state = 'waiting'
    session.message = '已打开登录窗口，请在运行后端的这台电脑上完成扫码/账号登录'
  }
}

/**
 * POST /api/account/login/start（幂等：已有窗口等待时直接返回当前状态）。
 * @param {object} [body] mock 调试参数（真实后端忽略）
 */
export function startLogin(body = {}) {
  advance()
  if (isRunning()) {
    return { ok: true, status: 200, body: payload() }
  }

  const now = Date.now()
  const successDelay = Number(body.mock_success_after_ms) > 0 ? Number(body.mock_success_after_ms) : DEFAULT_SUCCESS_DELAY_MS
  const timeoutDelay =
    Number(body.mock_timeout_after_ms) > 0 ? Number(body.mock_timeout_after_ms) : DEFAULT_TIMEOUT_MS

  session = {
    ...createIdle(),
    state: 'opening',
    message: '正在打开 B 站登录窗口…',
    started_at_ms: now,
    updated_at_ms: now,
    open_at_ms: now + DEFAULT_OPEN_DELAY_MS,
    success_at_ms: now + successDelay,
    timeout_at_ms: now + timeoutDelay,
    fail_message: typeof body.mock_fail === 'string' ? body.mock_fail : '',
  }
  return { ok: true, status: 200, body: payload() }
}

/** GET /api/account/login/status */
export function getLoginStatus() {
  advance()
  return { ok: true, status: 200, body: payload() }
}

/** POST /api/account/login/cancel */
export function cancelLogin() {
  advance()
  if (isRunning()) {
    session.state = 'cancelled'
    session.message = '已取消登录'
    session.updated_at_ms = Date.now()
  }
  return { ok: true, status: 200, body: payload() }
}

/** /__mock/reset 用：回到 idle */
export function resetLogin() {
  session = createIdle()
}
