/**
 * src/utils/videoFormat.js — B 站分析页专用展示层工具（纯函数，无请求、无假数据）
 *
 * 状态文案与颜色严格对应 docs/API契约.md 第一节：
 * queued 排队中(灰) / crawling 爬取中(蓝) / processing 处理中(橙) / completed 已完成(绿) / failed 失败(红)
 */

/** 状态 → 文案 + 样式色调；新增状态时只改这里。 */
export const STATUS_META = {
  queued: { label: '排队中', tone: 'queued' },
  crawling: { label: '爬取中', tone: 'crawling' },
  processing: { label: '处理中', tone: 'processing' },
  completed: { label: '已完成', tone: 'completed' },
  failed: { label: '失败', tone: 'failed' },
}

/** 终态：轮询停止条件（契约第三节）。 */
export const TERMINAL_STATUSES = ['completed', 'failed']

export function statusMeta(status) {
  return STATUS_META[status] || { label: status || '未知', tone: 'unknown' }
}

export function isTerminal(status) {
  return TERMINAL_STATUSES.includes(status)
}

/** BV 号校验规则（契约：前后端一致），与 mock 中的正则保持一致。 */
export const BV_PATTERN = /^BV[0-9A-Za-z]{10}$/

export function validateBvid(value) {
  const bvid = String(value ?? '').trim()
  if (!bvid) return '请输入 BV 号'
  if (!BV_PATTERN.test(bvid)) return 'BV 号格式不正确，应形如 BV1xx411c7mD'
  return ''
}

/** ISO 时间 → 「YYYY-MM-DD HH:mm」；空值返回「—」。 */
export function formatDateTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return String(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 千分位整数。 */
export function formatNumber(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return num.toLocaleString('zh-CN')
}

/** 0~1 小数 → 百分比文案。 */
export function formatRatio(value, digits = 1) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${(num * 100).toFixed(digits)}%`
}

/** 0~1 指标 → 保留 3 位小数文案。 */
export function formatMetric(value, digits = 3) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return num.toFixed(digits)
}

/** 截断长文本（列表展示用）。 */
export function truncate(text, max = 60) {
  const str = String(text ?? '')
  return str.length > max ? `${str.slice(0, max)}…` : str
}
