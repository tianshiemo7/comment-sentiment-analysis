/**
 * src/utils/sentimentSeries.js — 情感序列可见性工具（纯 ESM / 纯函数，无 Vue 依赖）
 *
 * 背景：当前上线模型是二分类（positive / negative），但接口契约 `detail.sentiment`、
 * `trend[]` 仍保留 neutral / neutral_ratio 字段（恒为 0，属契约，后端不删）。
 * 前端据此决定展示哪些类别：
 *   - positive / negative：始终展示（字段缺失时按 0 处理，不抛错）；
 *   - neutral：仅当 count > 0 或 ratio > 0 时展示——二分类数据不再出现
 *     「中性 0.0%」扇区/比例行/图例；三分类（或回退数据）neutral > 0 时照常展示。
 *
 * 供 components/charts/SentimentPie.vue（饼图 data / 图例 / 比例行）与
 * components/charts/TrendLine.vue（series / 图例）共用，保证两处判定完全一致。
 */

import { SENTIMENT_COLORS, SENTIMENT_LABELS } from './sentimentLabels.js'

/** 契约中的情感类别顺序（与后端字段、图例顺序保持一致） */
export const SENTIMENT_KEYS = ['positive', 'negative', 'neutral']

/** 二分类下恒定展示的类别 */
export const ALWAYS_VISIBLE_KEYS = ['positive', 'negative']

/** 可选类别：只有出现非零数据时才展示 */
export const OPTIONAL_KEY = 'neutral'

/**
 * 安全转数值：缺失 / null / NaN / Infinity / 非数字字符串 → 0，永不抛错。
 * @param {unknown} value
 * @returns {number}
 */
export function toSentimentNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0
  if (typeof value === 'string' && value.trim() !== '') {
    const num = Number(value)
    return Number.isFinite(num) ? num : 0
  }
  return 0
}

/**
 * 单个数值是否非零（neutral 这类可选类别的判定基础）。
 * 同时接受 number / 数字字符串，以及 `{ count, ratio, value }` 形态的对象。
 * @param {unknown} item
 * @returns {boolean}
 */
export function hasNonZero(item) {
  if (item === null || item === undefined) return false
  if (typeof item !== 'object') return toSentimentNumber(item) > 0
  return ['count', 'ratio', 'value'].some((field) => toSentimentNumber(item[field]) > 0)
}

/**
 * 中性是否应展示：任一来源的 neutral 计数或 neutral_ratio 非零即展示。
 * 支持传入多个来源（如饼图的 sentiment 汇总 + 趋势桶列表）。
 * @param {...(object|null|undefined)} sources
 * @returns {boolean}
 */
export function isNeutralVisible(...sources) {
  return sources.some(
    (source) =>
      source &&
      typeof source === 'object' &&
      (hasNonZero(source.neutral) || hasNonZero(source.neutral_ratio)),
  )
}

/**
 * 趋势桶里是否存在任一 neutral 非零的桶（TrendLine 决定是否加中性 series）。
 * 非数组 / 空数组 / 字段缺失均安全返回 false。
 * @param {Array<object>|null|undefined} buckets
 * @returns {boolean}
 */
export function hasVisibleNeutral(buckets) {
  return Array.isArray(buckets) && isNeutralVisible(...buckets)
}

/**
 * 情感占比的可见序列：饼图 data、图例、下方比例行共用同一份结果。
 *
 * @param {object|null|undefined} sentiment 形如
 *   `{ total, positive, negative, neutral, positive_ratio, negative_ratio, neutral_ratio }`
 * @param {...(object|null|undefined)} extraSources 额外判定来源（可选）
 * @returns {Array<{ key: string, label: string, color: string, count: number, ratio: number }>}
 *   positive / negative 恒在（缺失字段按 0）；neutral 只在非零时出现。
 */
export function visibleSentimentSeries(sentiment, ...extraSources) {
  const source = sentiment && typeof sentiment === 'object' ? sentiment : {}
  const showNeutral = isNeutralVisible(source, ...extraSources)
  return SENTIMENT_KEYS.filter((key) => key !== OPTIONAL_KEY || showNeutral).map((key) => ({
    key,
    label: SENTIMENT_LABELS[key],
    color: SENTIMENT_COLORS[key],
    count: toSentimentNumber(source[key]),
    ratio: toSentimentNumber(source[`${key}_ratio`]),
  }))
}
