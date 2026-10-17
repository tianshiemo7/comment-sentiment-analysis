/**
 * src/api/account.js — 「人工登录窗口」接口封装（与 settings.js / videos.js 并列）
 *
 * 契约来源：docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md 第 2.4 节
 *   1. POST /api/account/login/start   打开后端所在机器的登录窗口（幂等）
 *   2. GET  /api/account/login/status  轮询状态（前端 2 秒一次）
 *   3. POST /api/account/login/cancel  取消登录并关闭窗口
 *
 * 状态：idle | opening | waiting | success | failed | cancelled | timeout
 *   - 浏览器依赖缺失时 start 返回 **503**（detail 是中文安装指引）；
 *   - Cookie 永不通过接口回传明文；成功时后端已把凭据写入设置（mode=account / uname / mid），
 *     前端随后刷新 GET /api/settings 即可。
 *
 * 错误统一抛出带中文 detail 的 Error，并挂 err.status（503 特判用于展示 Playwright 安装指引）。
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

const BACKEND_HINT = '请确认 FastAPI 服务已启动：python -m uvicorn src.inference.app:app --port 8000'

/**
 * 统一请求：解析 JSON、非 2xx 抛错。
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
    error.detail = typeof detail === 'string' ? detail : text
    throw error
  }

  return data
}

/** 1) 打开登录窗口（已有窗口在等待时直接返回当前状态，幂等） */
export function startBrowserLogin() {
  return request('/api/account/login/start', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({}),
  })
}

/** 2) 查询登录窗口状态 */
export function getBrowserLoginStatus() {
  return request('/api/account/login/status')
}

/** 3) 取消登录并关闭窗口 */
export function cancelBrowserLogin() {
  return request('/api/account/login/cancel', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({}),
  })
}

/** 浏览器依赖缺失（503）时展示的安装指引（后端 detail 会附在下方） */
export const PLAYWRIGHT_INSTALL_HINT = [
  '「登录 B 站账号（弹出登录窗口）」需要后端安装 Playwright 浏览器依赖：',
  'pip install playwright',
  'python -m playwright install chromium',
  'Windows 已安装 Edge/Chrome 时通常可直接使用（后端优先 channel=msedge），无需再下载 Chromium。',
].join('\n')

export default { startBrowserLogin, getBrowserLoginStatus, cancelBrowserLogin, PLAYWRIGHT_INSTALL_HINT }
