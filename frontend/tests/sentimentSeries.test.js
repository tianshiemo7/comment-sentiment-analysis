/**
 * frontend/tests/sentimentSeries.test.js — 情感序列可见性工具单测
 *
 * 运行：cd frontend && npm test   （= node --test tests/，Node 内置，零新增依赖）
 *
 * 覆盖（对应任务 task-2）：
 *   1. 二分类数据 neutral 全 0：饼图/比例行/图例不展示中性，趋势不加中性 series；
 *   2. neutral > 0（count 或 ratio 任一非零）：中性照常展示（三分类/回退数据兼容）；
 *   3. 空对象 / null / 缺字段 / 非数字：不抛错，positive/negative 回退为 0。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  hasNonZero,
  hasVisibleNeutral,
  isNeutralVisible,
  toSentimentNumber,
  visibleSentimentSeries,
} from '../src/utils/sentimentSeries.js'

/** 真实后端二分类详情（BV16Qx46kEWK）的 sentiment 片段 */
const BINARY_SENTIMENT = {
  total: 40,
  positive: 15,
  negative: 25,
  neutral: 0,
  positive_ratio: 0.375,
  negative_ratio: 0.625,
  neutral_ratio: 0,
}

/** 真实后端二分类详情（BV16Qx46kEWK）的 trend 片段 */
const BINARY_TREND = [
  { bucket: '2026-11-01', positive: 10, negative: 19, neutral: 0 },
  { bucket: '2026-11-02', positive: 5, negative: 6, neutral: 0 },
]

test('二分类数据（neutral 全 0）：饼图/比例行不展示中性', () => {
  const series = visibleSentimentSeries(BINARY_SENTIMENT)
  assert.deepEqual(
    series.map((item) => item.key),
    ['positive', 'negative'],
  )
  // 数值与配色仍按契约展示，没有「中性 0.0%」
  assert.equal(series[0].label, '正面')
  assert.equal(series[0].count, 15)
  assert.equal(series[0].ratio, 0.375)
  assert.equal(series[1].label, '负面')
  assert.equal(series[1].count, 25)
  assert.ok(series.every((item) => item.label !== '中性'))
})

test('二分类趋势（neutral 全 0）：趋势图不加中性 series', () => {
  assert.equal(hasVisibleNeutral(BINARY_TREND), false)
  assert.equal(isNeutralVisible(...BINARY_TREND), false)
})

test('neutral > 0（计数）：中性照常展示（三分类兼容）', () => {
  const sentiment = { ...BINARY_SENTIMENT, neutral: 3, neutral_ratio: 0.075 }
  const series = visibleSentimentSeries(sentiment)
  assert.deepEqual(
    series.map((item) => item.key),
    ['positive', 'negative', 'neutral'],
  )
  const neutral = series.find((item) => item.key === 'neutral')
  assert.equal(neutral.label, '中性')
  assert.equal(neutral.count, 3)
  assert.equal(neutral.ratio, 0.075)
})

test('neutral 仅比例非零（count 为 0）：仍展示中性', () => {
  const sentiment = { ...BINARY_SENTIMENT, neutral: 0, neutral_ratio: 0.2 }
  assert.equal(isNeutralVisible(sentiment), true)
  assert.ok(visibleSentimentSeries(sentiment).some((item) => item.key === 'neutral'))
})

test('趋势桶中存在 neutral > 0：加中性 series；仅比例非零也算', () => {
  const trend = [
    { bucket: '2026-11-01', positive: 10, negative: 19, neutral: 0 },
    { bucket: '2026-11-02', positive: 5, negative: 6, neutral: 4 },
  ]
  assert.equal(hasVisibleNeutral(trend), true)
  assert.equal(hasVisibleNeutral([{ bucket: 'x', neutral: 0, neutral_ratio: 0 }]), false)
  assert.equal(hasVisibleNeutral([{ bucket: 'x', neutral: 0, neutral_ratio: 0.1 }]), true)
  // 空 / 非数组 / 缺失字段：安全返回 false
  assert.equal(hasVisibleNeutral([]), false)
  assert.equal(hasVisibleNeutral(null), false)
  assert.equal(hasVisibleNeutral(undefined), false)
  assert.equal(hasVisibleNeutral('not-an-array'), false)
  assert.equal(hasVisibleNeutral([{ bucket: 'x' }]), false)
})

test('空对象 / 缺字段：不抛错，positive/negative 回退为 0，neutral 不展示', () => {
  for (const sentiment of [{}, null, undefined]) {
    const series = visibleSentimentSeries(sentiment)
    assert.deepEqual(
      series.map((item) => item.key),
      ['positive', 'negative'],
    )
    assert.ok(series.every((item) => item.count === 0 && item.ratio === 0))
  }
})

test('非数字字段（字符串/NaN/Infinity/布尔）：不抛错且按 0 处理', () => {
  const sentiment = {
    total: 10,
    positive: 'abc',
    negative: NaN,
    neutral: '0',
    positive_ratio: null,
    negative_ratio: Infinity,
    neutral_ratio: '',
  }
  const series = visibleSentimentSeries(sentiment)
  assert.deepEqual(
    series.map((item) => item.key),
    ['positive', 'negative'],
  )
  assert.ok(series.every((item) => item.count === 0 && item.ratio === 0))
  assert.equal(toSentimentNumber('3'), 3)
  assert.equal(toSentimentNumber(''), 0)
  assert.equal(toSentimentNumber(null), 0)
  assert.equal(toSentimentNumber(undefined), 0)
  assert.equal(toSentimentNumber(Number.NaN), 0)
})

test('hasNonZero：接受数字/数字字符串/{count,ratio,value}，缺失安全', () => {
  assert.equal(hasNonZero(1), true)
  assert.equal(hasNonZero('2'), true)
  assert.equal(hasNonZero(0), false)
  assert.equal(hasNonZero('abc'), false)
  assert.equal(hasNonZero(NaN), false)
  assert.equal(hasNonZero(null), false)
  assert.equal(hasNonZero(undefined), false)
  assert.equal(hasNonZero({ count: 0, ratio: 0 }), false)
  assert.equal(hasNonZero({ count: 2 }), true)
  assert.equal(hasNonZero({ ratio: 0.01 }), true)
  assert.equal(hasNonZero({}), false)
})
