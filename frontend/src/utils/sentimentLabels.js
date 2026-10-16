/**
 * src/utils/sentimentLabels.js — 情感标签展示层工具（新增，纯函数）
 *
 * 说明：既有 utils/format.js 只覆盖 positive/negative；数据项目与单独评论的契约
 * （docs/API契约.md 4.5）还包含 neutral，因此这里单独提供一份，不改动既有工具的行为。
 */

export const SENTIMENT_LABELS = {
  positive: '正面',
  negative: '负面',
  neutral: '中性',
}

/** 标签 → 中文文案（未知标签原样返回） */
export function sentimentText(label) {
  const key = String(label || '').toLowerCase()
  if (!key) return '未知'
  return SENTIMENT_LABELS[key] || String(label)
}

/** 标签 → 色调名（positive | negative | neutral | unknown），供样式类使用 */
export function sentimentTone(label) {
  const key = String(label || '').toLowerCase()
  return key in SENTIMENT_LABELS ? key : 'unknown'
}

/** 标签 → 展示配色（图表与概率条共用） */
export const SENTIMENT_COLORS = {
  positive: '#16a34a',
  negative: '#dc2626',
  neutral: '#94a3b8',
  unknown: '#94a3b8',
}
