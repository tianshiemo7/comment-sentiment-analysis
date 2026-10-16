/**
 * mock/pipeline.js — 任务流水线的纯函数实现（无 HTTP、无状态）
 *
 * 只负责把「创建时间 + 时间线配置」换算成契约里的 status / progress / message / phases。
 * 契约见 docs/API契约.md 第一节（状态语义）。
 */

/** 阶段名固定四个，与契约示例一致；phases 由数组渲染，前端不写死数量。 */
export const PHASE_NAMES = ['采集评论', '文本清洗', '情感推理', '统计分析']

/** 默认时间线：queued 5s → crawling 10s → processing 15s ≈ 30s 走完。 */
export const DEFAULT_TIMELINE = {
  queuedMs: 5_000,
  crawlingMs: 10_000,
  processingMs: 15_000,
}

export function totalMs(timeline = DEFAULT_TIMELINE) {
  return timeline.queuedMs + timeline.crawlingMs + timeline.processingMs
}

function clamp01(value) {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

function phases(statuses) {
  return PHASE_NAMES.map((name, index) => ({ name, status: statuses[index] }))
}

const PHASE_PENDING = ['pending', 'pending', 'pending', 'pending']
const PHASE_DONE = ['done', 'done', 'done', 'done']

/**
 * 计算当前流水线快照。
 * @param {number} createdAtMs 任务创建时间戳（毫秒）
 * @param {number} nowMs 当前时间戳（毫秒）
 * @param {{queuedMs:number,crawlingMs:number,processingMs:number}} [timeline]
 * @returns {{status:'queued'|'crawling'|'processing'|'completed', progress:number, message:string, phases:Array<{name:string,status:string}>}}
 */
export function snapshot(createdAtMs, nowMs, timeline = DEFAULT_TIMELINE) {
  const elapsed = Math.max(0, nowMs - createdAtMs)
  const { queuedMs, crawlingMs, processingMs } = timeline

  if (elapsed < queuedMs) {
    return { status: 'queued', progress: 0, message: '已加入分析队列，等待调度…', phases: phases(PHASE_PENDING) }
  }

  const afterQueue = elapsed - queuedMs
  if (afterQueue < crawlingMs) {
    const f = clamp01(afterQueue / crawlingMs)
    // 爬取阶段整体进度 5 → 45，平滑递增；「采集评论」在整个爬取期间保持 active
    const progress = Math.min(45, Math.round(5 + 40 * f))
    return { status: 'crawling', progress, message: '正在爬取评论…', phases: phases(['active', 'pending', 'pending', 'pending']) }
  }

  const afterCrawl = afterQueue - crawlingMs
  if (afterCrawl < processingMs) {
    const f = clamp01(afterCrawl / processingMs)
    // 处理阶段整体进度 45 → 99
    const progress = Math.min(99, Math.round(45 + 54 * f))
    let actives
    if (f < 0.34) actives = ['done', 'active', 'pending', 'pending']
    else if (f < 0.67) actives = ['done', 'done', 'active', 'pending']
    else actives = ['done', 'done', 'done', 'active']
    return { status: 'processing', progress, message: '正在清洗与情感分析…', phases: phases(actives) }
  }

  return { status: 'completed', progress: 100, message: '分析完成', phases: phases(PHASE_DONE) }
}

/** 爬取阶段已入库评论数（按爬取比例增长，便于左栏实时看到评论数变化）。 */
export function crawledCount(snap, commentTotal, timeline = DEFAULT_TIMELINE, elapsed = 0) {
  if (snap.status === 'queued') return 0
  if (snap.status === 'completed' || snap.status === 'processing') return commentTotal
  const f = clamp01((elapsed - timeline.queuedMs) / timeline.crawlingMs)
  return Math.floor(commentTotal * (0.15 + 0.85 * f))
}
