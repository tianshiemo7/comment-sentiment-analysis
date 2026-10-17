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

/* ==========================================================================
 * 分步流水线（2026-10-17）：采集 → 清洗 → 分析，阶段边界可暂停为
 * crawled（待清洗）/ cleaned（待分析），等待前端点「开始清洗 / 开始分析」。
 * ========================================================================== */

/** 三个阶段：crawl → clean → analyze（analyze 覆盖「情感推理 + 统计分析」两个 phase） */
export const STAGE_KEYS = ['crawl', 'clean', 'analyze']

/**
 * 每阶段的时间线（mock 演示用，单位 ms）：
 *   queuedMs  入队等待；activeMs 真正执行；进度区间 [startProgress, endProgress]。
 * 默认参数（auto_clean=false / auto_analyze=false）下总耗时 ≈ 41s：
 *   采集 15s → 暂停；继续清洗 12s → 暂停；继续分析 14s → 完成。
 */
export const STAGE_PLAN = {
  crawl: {
    label: '采集评论',
    queuedMs: 5_000,
    activeMs: 10_000,
    startProgress: 0,
    endProgress: 45,
    activeStatus: 'crawling',
    message: '正在爬取评论…',
  },
  clean: {
    label: '文本清洗',
    queuedMs: 2_000,
    activeMs: 10_000,
    startProgress: 45,
    endProgress: 75,
    activeStatus: 'processing',
    message: '正在清洗评论…',
  },
  analyze: {
    label: '情感分析与统计',
    queuedMs: 2_000,
    activeMs: 12_000,
    startProgress: 75,
    endProgress: 95,
    activeStatus: 'processing',
    message: '正在情感分析与统计…',
  },
}

const STAGE_INDEX = { crawl: 0, clean: 1, analyze: 2 }

/**
 * 构造当前阶段对应的 phases 三态：
 * 之前阶段 done、当前阶段 active/pending、之后 pending；
 * analyze 阶段同时覆盖「情感推理 + 统计分析」两个 phase。
 */
function phasesForStage(stage, current) {
  const index = STAGE_INDEX[stage] ?? 0
  const finished = current === 'done'
  return PHASE_NAMES.map((name, i) => {
    if (stage === 'analyze') {
      if (i < 2 || finished) return { name, status: 'done' }
      if (i === 2) return { name, status: current }
      return { name, status: 'pending' }
    }
    if (i < index) return { name, status: 'done' }
    if (i === index) return { name, status: current }
    return { name, status: 'pending' }
  })
}

/**
 * 单个阶段的纯快照。
 * @param {'crawl'|'clean'|'analyze'} stage
 * @param {number} elapsedMs 距离本阶段开始的毫秒数
 * @returns {{done:boolean,status:string,progress:number,message:string,phases:Array}}
 */
export function stageSnapshot(stage, elapsedMs) {
  const plan = STAGE_PLAN[stage]
  if (!plan) {
    return { done: true, status: 'completed', progress: 100, message: '分析完成', phases: phases(PHASE_DONE) }
  }
  const elapsed = Math.max(0, Number(elapsedMs) || 0)

  if (elapsed < plan.queuedMs) {
    return {
      done: false,
      status: 'queued',
      progress: plan.startProgress,
      message: stage === 'crawl' ? '已加入分析队列，等待调度…' : `等待进入「${plan.label}」阶段…`,
      phases: phasesForStage(stage, 'pending'),
    }
  }

  const activeElapsed = elapsed - plan.queuedMs
  if (activeElapsed < plan.activeMs) {
    const f = clamp01(activeElapsed / plan.activeMs)
    const progress = Math.min(
      plan.endProgress,
      Math.round(plan.startProgress + (plan.endProgress - plan.startProgress) * f),
    )
    return {
      done: false,
      status: plan.activeStatus,
      progress,
      message: plan.message,
      phases: phasesForStage(stage, 'active'),
    }
  }

  return {
    done: true,
    status: '',
    progress: plan.endProgress,
    message: `${plan.label}完成`,
    phases: phasesForStage(stage, 'done'),
  }
}

/** 等待态快照：crawled（待清洗）/ cleaned（待分析）。 */
export function waitingSnapshot(status) {
  if (status === 'cleaned') {
    return {
      status: 'cleaned',
      progress: STAGE_PLAN.clean.endProgress,
      message: '清洗完成，等待情感分析',
      phases: phases(['done', 'done', 'pending', 'pending']),
    }
  }
  return {
    status: 'crawled',
    progress: STAGE_PLAN.crawl.endProgress,
    message: '采集完成，等待清洗',
    phases: phases(['done', 'pending', 'pending', 'pending']),
  }
}

/** 分步任务已完成快照。 */
export function completedSnapshot() {
  return { status: 'completed', progress: 100, message: '分析完成', phases: phases(PHASE_DONE) }
}

/** 分步流水线：已入库评论数（采集阶段按比例增长，之后等于总量）。 */
export function stagedCommentCount(stage, elapsedMs, commentTotal) {
  if (stage !== 'crawl') return commentTotal
  const plan = STAGE_PLAN.crawl
  const elapsed = Math.max(0, Number(elapsedMs) || 0)
  if (elapsed < plan.queuedMs) return 0
  const activeElapsed = Math.min(plan.activeMs, elapsed - plan.queuedMs)
  const f = clamp01(activeElapsed / plan.activeMs)
  return Math.max(0, Math.floor(commentTotal * (0.15 + 0.85 * f)))
}
