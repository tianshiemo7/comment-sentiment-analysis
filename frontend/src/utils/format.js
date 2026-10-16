/** 展示层通用格式化工具（薄封装，无业务逻辑） */

const LABEL_TEXT = {
  positive: '正面',
  negative: '负面',
}

/** 情感标签 -> 中文文案 */
export function labelText(label) {
  if (!label) return '未知'
  return LABEL_TEXT[String(label).toLowerCase()] || String(label)
}

/** 情感标签 -> 徽章样式类 */
export function labelClass(label) {
  const key = String(label || '').toLowerCase()
  if (key === 'positive') return 'badge badge--positive'
  if (key === 'negative') return 'badge badge--negative'
  return 'badge'
}

/** 置信度/指标数值格式化，保留 4 位小数 */
export function formatMetric(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return num.toFixed(4)
}

/** 0~1 数值 -> 百分比字符串 */
export function formatPercent(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  return `${(num * 100).toFixed(2)}%`
}

/** 0~1 数值 -> 进度条宽度百分比（供内联 style 使用） */
export function percentWidth(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '0%'
  return `${Math.max(0, Math.min(100, num * 100)).toFixed(2)}%`
}

/** 文本截断（表格展示用） */
export function truncate(text, max = 80) {
  const str = String(text ?? '')
  return str.length > max ? `${str.slice(0, max)}…` : str
}
