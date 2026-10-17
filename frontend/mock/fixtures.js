/**
 * mock/fixtures.js — 演示数据（只属于 mock 层，业务组件一律不得内联假数据）
 *
 * 数据按 docs/API契约.md 第三节「视频详情」逐字段构造；
 * 词云/关键词/趋势/代表性评论全部在这里生成，真实后端就绪后整个 mock/ 目录可删除。
 */

/** 本地时区 ISO 字符串（形如 2026-10-16T14:30:00+08:00），与契约示例一致。 */
export function toIso(date = new Date()) {
  const pad = (n) => String(n).padStart(2, '0')
  const offset = -date.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  const abs = Math.abs(offset)
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  )
}

/** 按天趋势（14 天）。sentiment 的计数由本数组汇总得到，保证 total 与趋势自洽。 */
const TREND = [
  { bucket: '2026-09-01', positive: 318, negative: 164, neutral: 41 },
  { bucket: '2026-09-02', positive: 402, negative: 188, neutral: 55 },
  { bucket: '2026-09-03', positive: 366, negative: 151, neutral: 47 },
  { bucket: '2026-09-04', positive: 441, negative: 203, neutral: 62 },
  { bucket: '2026-09-05', positive: 512, negative: 236, neutral: 71 },
  { bucket: '2026-09-06', positive: 587, negative: 248, neutral: 83 },
  { bucket: '2026-09-07', positive: 463, negative: 197, neutral: 58 },
  { bucket: '2026-09-08', positive: 371, negative: 168, neutral: 44 },
  { bucket: '2026-09-09', positive: 344, negative: 159, neutral: 39 },
  { bucket: '2026-09-10', positive: 398, negative: 181, neutral: 51 },
  { bucket: '2026-09-11', positive: 427, negative: 205, neutral: 57 },
  { bucket: '2026-09-12', positive: 469, negative: 224, neutral: 66 },
  { bucket: '2026-09-13', positive: 388, negative: 176, neutral: 45 },
  { bucket: '2026-09-14', positive: 324, negative: 148, neutral: 38 },
]

const POSITIVE_WORDS = [
  { word: '好用', count: 320 },
  { word: '流畅', count: 287 },
  { word: '拍照清晰', count: 244 },
  { word: '性价比高', count: 231 },
  { word: '爱了', count: 208 },
  { word: '续航给力', count: 186 },
  { word: '手感好', count: 163 },
  { word: '屏幕细腻', count: 148 },
  { word: '值得买', count: 131 },
  { word: '系统顺滑', count: 118 },
  { word: '音质不错', count: 102 },
  { word: '颜值在线', count: 94 },
  { word: '客服耐心', count: 76 },
  { word: '充电快', count: 68 },
  { word: '推荐', count: 61 },
  { word: '做工扎实', count: 55 },
  { word: '散热好', count: 47 },
  { word: '更新及时', count: 39 },
]

const NEGATIVE_WORDS = [
  { word: '卡顿', count: 213 },
  { word: '发热', count: 189 },
  { word: '掉帧', count: 154 },
  { word: '续航差', count: 137 },
  { word: '信号差', count: 121 },
  { word: '品控差', count: 108 },
  { word: '广告多', count: 96 },
  { word: '太贵了', count: 88 },
  { word: '屏幕泛绿', count: 77 },
  { word: '售后差', count: 66 },
  { word: '耗电快', count: 59 },
  { word: '系统卡', count: 51 },
  { word: '做工一般', count: 44 },
  { word: '降价快', count: 37 },
  { word: '失望', count: 31 },
  { word: '反光严重', count: 26 },
  { word: '不保值', count: 21 },
  { word: '重量大', count: 17 },
]

const POSITIVE_COMMENTS = [
  { text: '用了一个月，系统流畅度确实提升明显，日常完全够用。', like: 1284, confidence: 0.981 },
  { text: '这代影像真的越级了，夜景直出就能发朋友圈，省了后期。', like: 967, confidence: 0.974 },
  { text: '续航比上一代好太多，中度使用一天半没问题。', like: 823, confidence: 0.962 },
  { text: '手感和小屏旗舰的感觉回来了，单手握持很舒服。', like: 641, confidence: 0.958 },
  { text: '价格虽然不便宜，但配置给得实在，性价比还是可以的。', like: 512, confidence: 0.947 },
  { text: '屏幕素质在线，阳光下也能看清，看视频很享受。', like: 438, confidence: 0.936 },
  { text: '客服响应很快，售后换新流程走下来没扯皮，好评。', like: 302, confidence: 0.921 },
  { text: '充电速度给力，早上洗个澡的功夫就八成电了。', like: 268, confidence: 0.918 },
]

const NEGATIVE_COMMENTS = [
  { text: '打游戏十几分钟就开始烫手，帧率也稳不住，有点劝退。', like: 1103, confidence: 0.978 },
  { text: '系统广告越来越多，关了一半还是有推送，体验倒退。', like: 892, confidence: 0.965 },
  { text: '这个价位屏幕还泛绿，暗光下看着很难受。', like: 754, confidence: 0.961 },
  { text: '续航是真的拉胯，重度使用下午三点就得找充电器。', like: 621, confidence: 0.952 },
  { text: '信号比老机器差，地铁里经常断流，很难受。', like: 505, confidence: 0.944 },
  { text: '首发一个月就降价六百，早知道再等等了。', like: 447, confidence: 0.938 },
  { text: '收到第一台就有异响，换货又等了五天，品控堪忧。', like: 358, confidence: 0.927 },
  { text: '机身太重了，躺床上刷视频手腕酸。', like: 276, confidence: 0.913 },
]

const MODEL = {
  name: 'bilstm_attention',
  accuracy: 0.912,
  precision: 0.908,
  recall: 0.915,
  f1: 0.911,
}

/** 归一化权重：count / maxCount，落在 0~1。 */
function withWeight(words) {
  const max = words.reduce((acc, item) => Math.max(acc, item.count), 0) || 1
  return words.map((item) => ({ ...item, weight: Number((item.count / max).toFixed(4)) }))
}

/** 预置趋势数据的自然总量（分步任务按 comment_total 等比缩放时用来算倍率）。 */
const BASE_TREND_TOTAL = TREND.reduce(
  (acc, item) => acc + item.positive + item.negative + item.neutral,
  0,
)

/** 由趋势数组汇总情感占比，保证 sentiment.total 与 trend 一致。 */
function sentimentFromTrend(trend) {
  const sum = (key) => trend.reduce((acc, item) => acc + item[key], 0)
  const positive = sum('positive')
  const negative = sum('negative')
  const neutral = sum('neutral')
  const total = positive + negative + neutral
  const ratio = (value) => Number((value / total).toFixed(4))
  return {
    total,
    positive,
    negative,
    neutral,
    positive_ratio: ratio(positive),
    negative_ratio: ratio(negative),
    neutral_ratio: ratio(neutral),
  }
}

/** 关键词/评论计数按比例缩放，让不同视频的数据看起来不雷同。 */
function scaleWords(words, factor) {
  return withWeight(words.map((item) => ({ ...item, count: Math.max(1, Math.round(item.count * factor)) })))
}

function scaleComments(comments, factor, shift) {
  return comments.map((item) => ({
    ...item,
    like: Math.max(1, Math.round(item.like * factor)),
    confidence: Number(Math.min(0.999, Math.max(0.6, item.confidence + shift)).toFixed(3)),
  }))
}

/** 稳定的字符串哈希，保证同一 bvid 每次生成的 mock 数据一致。 */
function hashCode(text) {
  let hash = 0
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) % 100000
  }
  return hash
}

/**
 * 构造一条「已完成」视频的完整详情。
 * @param {object} record 任务记录（需要 bvid / title / owner / pubdate / crawled_at）
 *
 * 分步流水线任务（record.staged）会把趋势/情感计数等比缩放到本任务估算出的评论数，
 * 保证「列表评论数 / 详情评论总数 / 情感占比总数」三者自洽；旧任务保持原有随机倍率。
 */
export function buildCompletedDetail(record) {
  const hash = hashCode(record.bvid)
  const targetTotal = record.staged ? Number(record.comment_total) : NaN
  const scale =
    Number.isFinite(targetTotal) && targetTotal > 0
      ? targetTotal / BASE_TREND_TOTAL
      : 0.7 + (hash % 65) / 100 // 旧任务：0.70 ~ 1.34
  const trend = TREND.map((bucket) => ({
    bucket: bucket.bucket,
    positive: Math.round(bucket.positive * scale),
    negative: Math.round(bucket.negative * scale),
    neutral: Math.round(bucket.neutral * scale),
  }))
  // 分步任务：把四舍五入误差补到最后一个 bucket，保证 sentiment.total 与任务评论数完全一致
  if (Number.isFinite(targetTotal) && targetTotal > 0 && trend.length) {
    const currentTotal = trend.reduce((acc, item) => acc + item.positive + item.negative + item.neutral, 0)
    const delta = targetTotal - currentTotal
    const last = trend[trend.length - 1]
    last.neutral = Math.max(0, last.neutral + delta)
  }
  const sentiment = sentimentFromTrend(trend)
  const shift = ((hash % 9) - 4) / 1000 // ±0.004

  return {
    sentiment,
    trend,
    keywords: {
      positive: scaleWords(POSITIVE_WORDS, scale),
      negative: scaleWords(NEGATIVE_WORDS, scale),
    },
    top_comments: {
      positive: scaleComments(POSITIVE_COMMENTS, scale, shift).map((item) => ({ ...item, label: 'positive' })),
      negative: scaleComments(NEGATIVE_COMMENTS, scale, shift).map((item) => ({ ...item, label: 'negative' })),
    },
    model: MODEL,
    length_stats: {
      avg_chars: Number((34 + (hash % 90) / 10).toFixed(1)),
      max_chars: 180 + (hash % 60),
    },
    comment_total: sentiment.total,
  }
}

/** 预置演示数据：1 条已完成 + 1 条长期爬取中 + 1 条「待清洗」分步任务。 */
export function presetVideos(now = Date.now()) {
  const completedAt = now - 26 * 3600 * 1000
  const completed = {
    bvid: 'BV1xx411c7mD',
    title: '小米 15 Ultra 一个月深度使用体验：影像真的越级了吗？',
    owner: '数码闲聊站',
    cover_url: null,
    pubdate: '2026-09-01T12:00:00+08:00',
    created_at_ms: completedAt,
    crawled_at_ms: completedAt + 13_000,
    timeline: { queuedMs: 5_000, crawlingMs: 10_000, processingMs: 15_000 },
  }
  const crawling = {
    bvid: 'BV1Ks4y1V7bE',
    title: '【4K 修复】老式电脑开机声音合集，听到第三个就破防了',
    owner: '复古数码收藏馆',
    cover_url: null,
    pubdate: '2026-08-21T20:15:00+08:00',
    // 起点拉早 22.8 小时 + 24 小时的爬取窗口 → 演示期间一直停在 crawling（约 43% 缓慢递增）
    created_at_ms: now - Math.round(22.8 * 3600 * 1000),
    crawled_at_ms: null,
    timeline: { queuedMs: 5_000, crawlingMs: 24 * 3600 * 1000, processingMs: 15_000 },
    comment_total: 15230,
  }
  // 分步流水线演示任务：采集阶段已完成，停在「待清洗」，点「开始清洗」即可继续
  const paused = {
    bvid: 'BV1Wa1t4C1eA',
    title: '分步流水线演示：采集已完成，点「开始清洗」继续',
    owner: '流水线演示数据',
    cover_url: null,
    pubdate: '2026-09-18T09:30:00+08:00',
    created_at_ms: now - 30 * 60 * 1000,
    crawled_at_ms: now - 30 * 60 * 1000 + 15_000,
    comment_total: 20,
  }
  return { completed, crawling, paused }
}

/** 新提交任务的标题/UP 主候选池（爬取开始后揭开，避免所有条目长得一样）。 */
export const TITLE_POOL = [
  '实测！这个价位最值得买的五款机型横向对比',
  '开箱 | 等了三个月的旗舰终于到手，第一印象如何',
  '3000 元档位怎么选？看完这条就不纠结了',
  '一个月真实体验：优点很明显，缺点也很致命',
  '拆机实录：这次散热堆料到底有没有诚意',
  '从零搭建本地情感分析服务，附完整代码',
  '老设备翻新记：花 200 块让它重新上岗',
  '这波更新值得升吗？三个细节告诉你答案',
]

export const OWNER_POOL = [
  '科技小辛',
  '数码测评君',
  '老周说硬件',
  '一颗柠檬不酸',
  '极客湾观察',
  '深夜刷机指南',
  '拆解实验室',
  '甲方说的都对',
]
