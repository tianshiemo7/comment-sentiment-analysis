/**
 * mock/artifactFixtures.js — 「数据项目」演示数据（只属于 mock 层）
 *
 * 按 docs/API契约.md 第 4.5.1 节构造三张表（initial / clean / excluded）与逐条情感；
 * 真实后端就绪后本文件随 mock/ 目录一起删除，业务组件不含任何假数据。
 */

import { toIso } from './fixtures.js'

/** 表头：与真实后端一样由后端返回，前端一律动态渲染（mock 只是模仿后端）。 */
export const INITIAL_COLUMNS = ['comment_id', 'text_raw', 'author', 'like', 'created_at', 'source_file']
export const CLEAN_COLUMNS = ['comment_id', 'text', 'label', 'tokens', 'text_raw', 'confidence']
export const EXCLUDED_COLUMNS = ['comment_id', 'text_raw', 'reason']

const POSITIVE_TEXTS = [
  '这个商品质量很好，物流也快，非常满意。',
  '用了一个月，系统流畅度确实提升明显，日常完全够用。',
  '拍照效果超出预期，夜景直出就能发朋友圈。',
  '性价比很高，同价位里找不到更能打的。',
  '客服态度特别好，问题很快就解决了。',
  '续航给力，中度使用一天半没问题。',
  '手感很舒服，单手握持也不累。',
  '屏幕素质在线，看视频很享受。',
  '充电速度真快，早上洗漱的功夫就八成电。',
  '做工扎实，细节处理到位，值这个价。',
  '系统更新很及时，新功能上手也简单。',
  '散热表现不错，长时间玩游戏也只是温热。',
]

const NEGATIVE_TEXTS = [
  '质量太差了，用了一天就坏了，非常失望。',
  '打游戏十几分钟就开始烫手，帧率也稳不住。',
  '系统广告越来越多，关了一半还有推送。',
  '这个价位屏幕还泛绿，暗光下看着很难受。',
  '续航是真的拉胯，下午三点就得找充电器。',
  '发货慢，客服也不理人，差评。',
  '信号比老机器差，地铁里经常断流。',
  '首发一个月就降价六百，早知道再等等。',
  '收到第一台就有异响，换货又等了五天。',
  '机身太重了，躺床上刷视频手腕酸。',
  '不好用，非常不满意，后悔买了。',
  '软件优化一塌糊涂，开个相机都要卡两秒。',
]

const NEUTRAL_TEXTS = [
  '已收到，包装完好，还没开始用。',
  '先占个位置，用一段时间再来补充评价。',
  '外观和描述一致，具体表现待观察。',
  '帮朋友买的，他说还行，我没细问。',
  '快递有点慢，东西本身没什么问题。',
  '和上一代比变化不大，见仁见智吧。',
  '看参数挺不错，实际体验还需要时间验证。',
  '颜色比图片略深，可以接受。',
]

/** 会被「排除记录」表收录的低质量文本。 */
const EXCLUDED_TEXTS = [
  '',
  '   ',
  '。。。',
  '😀😀😀',
  '好',
  '顶',
  '【推广】点击链接领取优惠券 http://example.com/xxx',
  '【推广】加群 XX 领红包',
  '重复内容：这个商品质量很好',
  '重复内容：这个商品质量很好',
  'asdfghjkl',
  '?????',
]

const EXCLUDE_REASONS = ['文本为空', '纯空白/符号', '纯表情', '长度不足 2 字', '广告推广内容', '重复评论', '无有效中文词']

const AUTHORS = ['路人甲', '数码控', '萌新报道', '南方小土豆', '键盘上的猫', '一杯冰美式', '山城雾都', '追风少年']

const POSITIVE_WORDS = [
  { word: '好用', count: 182 },
  { word: '流畅', count: 161 },
  { word: '拍照清晰', count: 143 },
  { word: '性价比高', count: 128 },
  { word: '续航给力', count: 112 },
  { word: '手感好', count: 98 },
  { word: '值得买', count: 86 },
  { word: '客服耐心', count: 74 },
  { word: '充电快', count: 63 },
  { word: '屏幕细腻', count: 55 },
  { word: '散热好', count: 47 },
  { word: '系统顺滑', count: 39 },
  { word: '颜值在线', count: 31 },
  { word: '做工扎实', count: 24 },
]

const NEGATIVE_WORDS = [
  { word: '卡顿', count: 154 },
  { word: '发热', count: 138 },
  { word: '掉帧', count: 113 },
  { word: '续航差', count: 96 },
  { word: '信号差', count: 84 },
  { word: '品控差', count: 71 },
  { word: '广告多', count: 62 },
  { word: '太贵了', count: 54 },
  { word: '售后差', count: 46 },
  { word: '屏幕泛绿', count: 38 },
  { word: '耗电快', count: 31 },
  { word: '失望', count: 25 },
  { word: '降价快', count: 19 },
  { word: '不保值', count: 14 },
]

function hash(text) {
  let value = 0
  const str = String(text)
  for (let i = 0; i < str.length; i += 1) value = (value * 31 + str.charCodeAt(i)) % 100000
  return value
}

function pick(list, index) {
  return list[index % list.length]
}

/** 极简「分词」占位：去掉标点后每 2 字一块（仅用于 mock 展示）。 */
function tokensOf(text) {
  const chars = String(text).replace(/[，。！？、,.!?~\s]/g, '')
  const out = []
  for (let i = 0; i < chars.length; i += 2) out.push(chars.slice(i, i + 2))
  return out.join(' ')
}

function withWeight(words) {
  const max = words.reduce((acc, item) => Math.max(acc, item.count), 0) || 1
  return words.map((item) => ({ ...item, weight: Number((item.count / max).toFixed(4)) }))
}

/** 由 label 与 confidence 生成三分类概率（和为 1，保留 4 位小数）。 */
export function probsOf(label, confidence) {
  const keys = ['positive', 'negative', 'neutral']
  const rest = keys.filter((key) => key !== label)
  const remain = Math.max(0, 1 - confidence)
  const first = Number((remain * 0.62).toFixed(4))
  const second = Number((remain - first).toFixed(4))
  const map = { [label]: confidence, [rest[0]]: first, [rest[1]]: second }
  return { positive: map.positive, negative: map.negative, neutral: map.neutral }
}

function labelAt(index) {
  const rest = index % 100
  if (rest < 57) return 'positive'
  if (rest < 92) return 'negative'
  return 'neutral'
}

/**
 * 构造一个数据项目的全部衍生数据（三张表 + 逐条情感 + 聚合分析）。
 * @param {{seed?: number, initial?: number, clean?: number, excluded?: number, offsetMs?: number}} [options]
 */
export function buildProjectData(options = {}) {
  const seed = options.seed ?? 0
  const initialCount = options.initial ?? 312
  const cleanCount = options.clean ?? 264
  const excludedCount = options.excluded ?? 48
  const baseMs = options.offsetMs ?? Date.now()
  const sourceFile = options.sourceFile || 'BV1UB4y1W7Xu_comments.csv'

  const initialRows = []
  for (let i = 0; i < initialCount; i += 1) {
    const label = labelAt(i + seed)
    const text = pick(label === 'positive' ? POSITIVE_TEXTS : label === 'negative' ? NEGATIVE_TEXTS : NEUTRAL_TEXTS, i + seed)
    initialRows.push([
      `c_${String(seed).padStart(2, '0')}${String(i + 1).padStart(4, '0')}`,
      text,
      pick(AUTHORS, i * 3 + seed),
      10 + ((hash(text) + i * 7) % 980),
      toIso(new Date(baseMs - (i % 400) * 3600 * 1000)),
      sourceFile,
    ])
  }

  const cleanRows = []
  const commentItems = []
  const counts = { positive: 0, negative: 0, neutral: 0 }
  const lengths = []
  for (let i = 0; i < cleanCount; i += 1) {
    const label = labelAt(i + seed)
    const text = pick(label === 'positive' ? POSITIVE_TEXTS : label === 'negative' ? NEGATIVE_TEXTS : NEUTRAL_TEXTS, i + seed)
    const confidence = Number((0.618 + ((hash(text) + i * 13) % 371) / 1000).toFixed(4))
    const commentId = `c_${String(seed).padStart(2, '0')}${String(i + 1).padStart(4, '0')}`
    counts[label] += 1
    lengths.push(text.length)
    cleanRows.push([commentId, text, label, tokensOf(text), text, confidence])
    commentItems.push({ index: i, text, label, confidence, probs: probsOf(label, confidence) })
  }

  const excludedRows = []
  for (let i = 0; i < excludedCount; i += 1) {
    const text = pick(EXCLUDED_TEXTS, i + seed)
    excludedRows.push([`x_${String(seed).padStart(2, '0')}${String(i + 1).padStart(4, '0')}`, text, pick(EXCLUDE_REASONS, i * 5 + seed)])
  }

  const total = cleanCount || 1
  const ratio = (value) => Number((value / total).toFixed(4))
  const sorted = [...lengths].sort((a, b) => a - b)
  const at = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] || 0

  return {
    initial: { columns: INITIAL_COLUMNS, rows: initialRows },
    clean: { columns: CLEAN_COLUMNS, rows: cleanRows },
    excluded: { columns: EXCLUDED_COLUMNS, rows: excludedRows },
    comments: commentItems,
    analysis: {
      method: 'rule_placeholder',
      total: cleanCount,
      sentiment: {
        positive: counts.positive,
        negative: counts.negative,
        neutral: counts.neutral,
        positive_ratio: ratio(counts.positive),
        negative_ratio: ratio(counts.negative),
        neutral_ratio: ratio(counts.neutral),
      },
      keywords: { positive: withWeight(POSITIVE_WORDS), negative: withWeight(NEGATIVE_WORDS) },
      length: {
        mean: Number((lengths.reduce((acc, item) => acc + item, 0) / total).toFixed(1)),
        median: at(0.5),
        p90: at(0.9),
        max: sorted[sorted.length - 1] || 0,
      },
      sample: commentItems.slice(0, 5).map(({ index, text, label, confidence }) => ({ index, text, label, confidence })),
    },
    sentiment: { total: cleanCount, ...counts },
  }
}
