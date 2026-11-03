/**
 * src/utils/requestError.js — 请求错误展示辅助（纯函数，无 Vue 依赖，零新依赖）
 *
 * 后端已接入：只有「连接类」失败（后端没起 / 网络断开 / 5xx）才提示启动后端；
 * HTTP 4xx 等业务错误只展示失败原因，避免把任何失败都误报成「后端未接入」。
 *
 * 供 SinglePredictView / BatchPredictView / ModelCompareView 共用。
 */

/** 启动后端的命令（与仓库根 README「前后端接口契约」一致） */
export const BACKEND_START_CMD = 'python -m uvicorn src.inference.app:app --port 8000'

/** 连接类错误：fetch 网络失败，或后端 5xx（服务未启动 / 已崩溃）。 */
const CONNECTION_ERROR_PATTERN = /无法连接|fetch failed|failed to fetch|networkerror|network error|load failed|HTTP 5\d\d/i

/**
 * 判断一条错误消息是否属于「连接类失败」。
 * @param {unknown} message 任意错误消息（null/undefined/非字符串均安全）
 * @returns {boolean}
 */
export function isConnectionError(message) {
  return CONNECTION_ERROR_PATTERN.test(String(message ?? ''))
}

/**
 * 连接类错误 → 返回应附加的「启动后端」提示；非连接类 → 返回空字符串（不提示）。
 * api/client.js 的网络失败消息已自带含 uvicorn 的提示，检测到就返回空串，避免同一句话出现两遍。
 * @param {unknown} message
 * @returns {string}
 */
export function backendHintFor(message) {
  const text = String(message ?? '')
  if (!isConnectionError(text)) return ''
  if (text.includes('uvicorn')) return ''
  return `请确认后端已启动：${BACKEND_START_CMD}`
}
