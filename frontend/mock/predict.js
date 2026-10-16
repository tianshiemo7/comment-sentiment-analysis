/**
 * mock/predict.js — 单条预测的**规则占位**实现（仅 mock 层）
 *
 * 目的：`VITE_USE_MOCK` 默认开启时「单条预测」页与「保存到左栏」链路也能完整演示，
 * 不依赖 FastAPI :8000 是否启动。
 *
 * ⚠️ 这是规则/词典占位，不是训练模型：真实后端（src/inference/service.py 的
 *    predict_rule_based）就绪后把 VITE_USE_MOCK=0 即可走真实接口，本文件随 mock/ 一起废弃。
 *    返回字段严格按契约：{text,label,confidence,probs:{positive,negative},method}，
 *    其中 method 为结果来源标识（当前 rule_placeholder，真实模型接入后为模型名）。
 */

const POSITIVE_WORDS = [
  '很好', '好用', '不错', '满意', '喜欢', '流畅', '清晰', '给力', '值得', '推荐',
  '舒服', '快', '棒', '优秀', '惊喜', '完美', '靠谱', '耐心', '实惠', '划算',
  '稳定', '省心', '漂亮', '到位', '点赞', '好评', '超值', '顺滑', '耐用',
  '好', '解决', '满意', '态度好', '快',
]

const NEGATIVE_WORDS = [
  '差', '垃圾', '失望', '卡', '卡顿', '发热', '烫', '慢', '坏', '退货',
  '坑', '假', '后悔', '糟糕', '难看', '恼火', '生气', '投诉', '敷衍', '不行',
  '问题', '故障', '掉帧', '耗电', '虚标', '广告', '贵', '难受', '烦', '劝退',
]

const DEGREE_WORDS = ['非常', '特别', '很', '太', '十分', '极其', '相当', '超级']
const NEGATIONS = ['不', '没', '无', '别', '未', '不能', '不要']

function countMatches(text, words) {
  let count = 0
  for (const word of words) {
    let from = 0
    for (;;) {
      const at = text.indexOf(word, from)
      if (at === -1) break
      count += 1
      from = at + word.length
    }
  }
  return count
}

/** 否定词出现在情感词之前 1~2 字内 → 该词极性翻转。 */
function negationFlips(text, words) {
  let flips = 0
  for (const word of words) {
    let from = 0
    for (;;) {
      const at = text.indexOf(word, from)
      if (at === -1) break
      const before = text.slice(Math.max(0, at - 2), at)
      if (NEGATIONS.some((neg) => before.includes(neg))) flips += 1
      from = at + word.length
    }
  }
  return flips
}

function degreeBoost(text) {
  let boost = 1
  for (const word of DEGREE_WORDS) if (text.includes(word)) boost = Math.min(1.6, boost + 0.15)
  return boost
}

/**
 * 规则占位预测：确定性（同文本同结果），概率永不取 0/1。
 * 「被否定的正面词」计为负面证据，「被否定的负面词」计为正面证据，保证
 * 「不好用，非常不满意。」这类文本方向正确。
 * @param {string} text
 */
export function predictRuleBased(text) {
  const raw = String(text ?? '')
  const positive = countMatches(raw, POSITIVE_WORDS)
  const negative = countMatches(raw, NEGATIVE_WORDS)
  const posFlips = negationFlips(raw, POSITIVE_WORDS)
  const negFlips = negationFlips(raw, NEGATIVE_WORDS)
  const boost = degreeBoost(raw)

  const positiveScore = positive - posFlips + negFlips
  const negativeScore = negative - negFlips + posFlips
  const score = (positiveScore - negativeScore) * boost
  // 逻辑斯蒂压缩到 (0,1)，再做 0.02~0.98 截断，避免 0/1
  const positiveProb = Math.min(0.98, Math.max(0.02, 1 / (1 + Math.exp(-0.9 * score))))
  const prob = Number(positiveProb.toFixed(4))
  const label = prob >= 0.5 ? 'positive' : 'negative'
  const confidence = Number(Math.max(prob, 1 - prob).toFixed(4))
  return {
    text: raw,
    label,
    confidence,
    probs: { positive: prob, negative: Number((1 - prob).toFixed(4)) },
    // 契约新增：结果来源（当前占位实现为 rule_placeholder，真实模型接入后为模型名）
    method: 'rule_placeholder',
  }
}
