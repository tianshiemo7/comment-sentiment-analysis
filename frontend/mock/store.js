/**
 * mock/store.js — 内存任务仓库（mock 专用，真实后端就绪后随 mock/ 一起删除）
 *
 * 对上层暴露与 docs/API契约.md + 2026-10-17 分步流水线设计一致的数据结构：
 *   - 旧任务（提交时未带 settings）：状态随时间一次跑完 queued → crawling → processing → completed；
 *   - 新任务（提交时带 settings）：分阶段执行，阶段边界可暂停为
 *     crawled（待清洗）/ cleaned（待分析），由 continueVideo 继续；
 *     自动流程（auto_clean / auto_analyze）开启时连续执行；
 *   - 预置一条 crawled 演示任务，打开页面即可直接体验「开始清洗」按钮。
 *
 * 分步流水线的时间线定义在 pipeline.js 的 STAGE_PLAN。
 */

import { DEFAULT_CRAWL, DEFAULT_PIPELINE, getAccountMode, getSavedSettings, hasSavedCookie } from './settings.js'
import {
  DEFAULT_TIMELINE,
  STAGE_PLAN,
  completedSnapshot,
  crawledCount,
  snapshot,
  stagedCommentCount,
  stageSnapshot,
  waitingSnapshot,
} from './pipeline.js'
import { OWNER_POOL, TITLE_POOL, buildCompletedDetail, presetVideos, toIso } from './fixtures.js'

/** 教学用途：提交这个 BV 号会走「失败」分支，用于演示失败态与「重试」。 */
export const MOCK_FAIL_BVID = 'BV1m0ckfai11'

const BV_PATTERN = /^BV[0-9A-Za-z]{10}$/

const TERMINAL = new Set(['completed', 'failed'])
const WAITING = new Set(['crawled', 'cleaned'])

function hashCode(text) {
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) % 100000
  return hash
}

function nowMs() {
  return Date.now()
}

function createRecord({
  bvid,
  createdAt,
  title = null,
  owner = null,
  pubdate = null,
  timeline = { ...DEFAULT_TIMELINE },
  commentTotal = null,
  deriveCommentTotal = false,
  alwaysRevealMeta = false,
}) {
  const hash = hashCode(bvid)
  return {
    bvid,
    title,
    owner,
    cover_url: null,
    pubdate,
    created_at_ms: createdAt,
    crawled_at_ms: null,
    completed_at_ms: null,
    timeline,
    // deriveCommentTotal=true 时由 fixtures 的趋势数据推导，保证 total 与趋势自洽
    comment_total: deriveCommentTotal ? null : commentTotal ?? 2_800 + (hash % 9_500),
    alwaysRevealMeta,
    error: null,
    // ---------- 分步流水线（settings 提交的任务才有） ----------
    staged: false,
    /** 本任务设置快照（不含 Cookie）：{account:{mode}, crawl:{...}, pipeline:{...}} */
    settings: null,
    /** 当前阶段：crawl | clean | analyze | done */
    stage: 'crawl',
    /** 当前阶段开始时间（毫秒）；阶段推进时按边界时刻顺延 */
    stage_start_ms: null,
    /** 暂停态：null | 'crawled' | 'cleaned' */
    waiting: null,
    /** 失败快照（动态注入后固定下来，避免每次读取都重新计算） */
    failure: null,
  }
}

/** 预置 3 条演示数据：1 条 completed、1 条 crawling 进行中、1 条 crawled 待清洗。 */
function seed() {
  const { completed, crawling, paused } = presetVideos(nowMs())
  const records = new Map()

  const completedRecord = createRecord({
    bvid: completed.bvid,
    createdAt: completed.created_at_ms,
    title: completed.title,
    owner: completed.owner,
    pubdate: completed.pubdate,
    timeline: completed.timeline,
    deriveCommentTotal: true,
    alwaysRevealMeta: true,
  })
  completedRecord.crawled_at_ms = completed.crawled_at_ms
  completedRecord.completed_at_ms = completed.crawled_at_ms
  records.set(completedRecord.bvid, completedRecord)

  const crawlingRecord = createRecord({
    bvid: crawling.bvid,
    createdAt: crawling.created_at_ms,
    title: crawling.title,
    owner: crawling.owner,
    pubdate: crawling.pubdate,
    timeline: crawling.timeline,
    commentTotal: crawling.comment_total,
    alwaysRevealMeta: true,
  })
  records.set(crawlingRecord.bvid, crawlingRecord)

  // 分步流水线演示任务：采集已完成，停在「待清洗」，点继续即可走完清洗 → 分析
  const pausedRecord = createRecord({
    bvid: paused.bvid,
    createdAt: paused.created_at_ms,
    title: paused.title,
    owner: paused.owner,
    pubdate: paused.pubdate,
    commentTotal: paused.comment_total,
    alwaysRevealMeta: true,
  })
  pausedRecord.staged = true
  pausedRecord.settings = {
    account: { mode: 'anonymous' },
    crawl: { ...DEFAULT_CRAWL },
    pipeline: { ...DEFAULT_PIPELINE },
  }
  pausedRecord.stage = 'clean'
  pausedRecord.stage_start_ms = paused.created_at_ms
  pausedRecord.crawled_at_ms = paused.crawled_at_ms
  pausedRecord.waiting = 'crawled'
  records.set(pausedRecord.bvid, pausedRecord)

  return records
}

let records = seed()

function totalComments(record) {
  return record.comment_total ?? buildCompletedDetail(record).comment_total
}

function metaVisible(record, state) {
  if (record.alwaysRevealMeta) return true
  // 契约：尚未爬到时 title / owner 为 null；爬取阶段开始后即可拿到视频元信息
  return state.status !== 'queued'
}

/* ==========================================================================
 * 状态计算
 * ========================================================================== */

/** 旧任务（无 settings）：原来的「一次跑完四阶段」时间线。 */
function legacyState(record, now) {
  const elapsed = Math.max(0, now - record.created_at_ms)
  const snap = snapshot(record.created_at_ms, now, record.timeline)
  const total = totalComments(record)
  if (record.bvid === MOCK_FAIL_BVID) {
    const failAt = record.timeline.queuedMs + record.timeline.crawlingMs
    if (elapsed >= failAt) {
      return {
        status: 'failed',
        progress: Math.min(99, snap.progress),
        message: '采集失败：评论接口返回 412（限流）',
        phases: [
          { name: '采集评论', status: 'done' },
          { name: '文本清洗', status: 'failed' },
          { name: '情感推理', status: 'pending' },
          { name: '统计分析', status: 'pending' },
        ],
        error: '评论采集被限流（HTTP 412），已入库 1,024 条，未达到最小样本量（mock 演示）',
        comment_count: 1024,
      }
    }
  }
  return {
    status: snap.status,
    progress: snap.progress,
    message: snap.message,
    phases: snap.phases,
    error: null,
    comment_count: crawledCount(snap, total, record.timeline, elapsed),
  }
}

/** 等待态快照（crawled / cleaned）。 */
function waitingState(record) {
  const snap = waitingSnapshot(record.waiting)
  return { ...snap, comment_count: totalComments(record), error: null }
}

/**
 * 分步任务状态：按「阶段 + 阶段开始时间」推进；阶段完成时根据自动流程决定继续还是暂停。
 * 这里在读取时推进阶段（mock 无后台线程，读取即推进，与真实后端的后台任务等价）。
 */
function stagedState(record, now) {
  for (let guard = 0; guard < 10; guard += 1) {
    if (record.failure) return record.failure
    if (record.waiting) return waitingState(record)
    if (!record.staged) return legacyState(record, now)

    const plan = STAGE_PLAN[record.stage]
    if (!plan) {
      return { ...completedSnapshot(), comment_count: totalComments(record), error: null }
    }

    const start = record.stage_start_ms ?? record.created_at_ms
    const elapsed = Math.max(0, now - start)
    const snap = stageSnapshot(record.stage, elapsed)

    // 「必须登录」但当前未登录：采集阶段开始后直接失败（契约：require_login 未登录即失败）
    const loginMissing =
      record.stage === 'crawl' &&
      Boolean(record.settings?.crawl?.require_login) &&
      !(record.settings?.account?.mode === 'account' && hasSavedCookie())
    if (loginMissing && snap.status === 'crawling') {
      record.failure = {
        status: 'failed',
        progress: Math.min(44, snap.progress),
        message: '采集失败：已开启「必须登录」但当前未登录',
        phases: [
          { name: '采集评论', status: 'done' },
          { name: '文本清洗', status: 'failed' },
          { name: '情感推理', status: 'pending' },
          { name: '统计分析', status: 'pending' },
        ],
        error: '任务设置了「必须登录」（require_login=true），但当前为匿名/未登录状态（mock 模拟）',
        comment_count: stagedCommentCount('crawl', elapsed, totalComments(record)),
      }
      return record.failure
    }

    if (!snap.done) {
      return {
        ...snap,
        comment_count: stagedCommentCount(record.stage, elapsed, totalComments(record)),
        error: null,
      }
    }

    // 当前阶段完成：按自动流程推进到下一阶段，或停在等待态
    const finishedAt = start + plan.queuedMs + plan.activeMs
    if (record.stage === 'crawl') {
      record.crawled_at_ms = finishedAt
      if (record.settings?.pipeline?.auto_clean) {
        record.stage = 'clean'
        record.stage_start_ms = finishedAt
        continue
      }
      record.waiting = 'crawled'
      record.stage_start_ms = finishedAt
      return waitingState(record)
    }
    if (record.stage === 'clean') {
      if (record.settings?.pipeline?.auto_analyze) {
        record.stage = 'analyze'
        record.stage_start_ms = finishedAt
        continue
      }
      record.waiting = 'cleaned'
      record.stage_start_ms = finishedAt
      return waitingState(record)
    }

    // analyze 完成 → 任务完成
    record.completed_at_ms = finishedAt
    record.stage = 'done'
    return { ...completedSnapshot(), comment_count: totalComments(record), error: null }
  }

  // 防御：理论上不会走到这里
  return { ...completedSnapshot(), comment_count: totalComments(record), error: null }
}

/** 任务当前快照（含动态失败判定）。 */
function stateOf(record, now = nowMs()) {
  if (record.failure) return record.failure
  if (record.waiting) return waitingState(record)
  if (record.staged) return stagedState(record, now)
  return legacyState(record, now)
}

/* ==========================================================================
 * 序列化
 * ========================================================================== */

/** GET /api/videos 的一项。 */
function serializeListItem(record, now) {
  const state = stateOf(record, now)
  const visible = metaVisible(record, state)
  return {
    bvid: record.bvid,
    title: visible ? record.title : null,
    owner: visible ? record.owner : null,
    cover_url: record.cover_url,
    status: state.status,
    progress: state.progress,
    message: state.message,
    phases: state.phases,
    comment_count: state.comment_count,
    created_at: toIso(new Date(record.created_at_ms)),
    updated_at: toIso(new Date(now)),
  }
}

const EMPTY_KEYWORDS = { positive: [], negative: [] }
const EMPTY_COMMENTS = { positive: [], negative: [] }

/**
 * 详情响应新增的 pipeline 对象（契约 3.4）：旧任务没有任务级 settings 快照，
 * 用当前已保存设置兜底（next_action 仍按实际状态计算）。
 */
function pipelineOf(record, status) {
  const saved = getSavedSettings()
  const settings = record.settings || {
    account: { mode: getAccountMode() },
    crawl: saved.crawl,
    pipeline: saved.pipeline,
  }
  return {
    account_mode: settings.account?.mode || 'anonymous',
    auto_clean: Boolean(settings.pipeline?.auto_clean),
    auto_analyze: Boolean(settings.pipeline?.auto_analyze),
    next_action: status === 'crawled' ? 'clean' : status === 'cleaned' ? 'analyze' : null,
    crawl: { ...(settings.crawl || saved.crawl) },
  }
}

/** GET /api/videos/{bvid}/detail。 */
function serializeDetail(record, now) {
  const state = stateOf(record, now)
  const visible = metaVisible(record, state)
  const emptyBody = {
    sentiment: null,
    trend: [],
    keywords: EMPTY_KEYWORDS,
    top_comments: EMPTY_COMMENTS,
    model: null,
    length_stats: null,
  }
  const completedBody = state.status === 'completed' ? buildCompletedDetail(record) : null
  const body = completedBody
    ? {
        sentiment: completedBody.sentiment,
        trend: completedBody.trend,
        keywords: completedBody.keywords,
        top_comments: completedBody.top_comments,
        model: completedBody.model,
        length_stats: completedBody.length_stats,
      }
    : emptyBody

  // 分步任务在采集完成时即记录 crawled_at；旧任务只在 completed 时有值
  const crawledAtIso =
    record.crawled_at_ms != null
      ? toIso(new Date(record.crawled_at_ms))
      : state.status === 'completed'
        ? toIso(new Date(record.created_at_ms + 13_000))
        : null

  return {
    video: {
      bvid: record.bvid,
      title: visible ? record.title : null,
      owner: visible ? record.owner : null,
      cover_url: record.cover_url,
      pubdate: visible ? record.pubdate : null,
      crawled_at: crawledAtIso,
      comment_total: state.status === 'completed' ? totalComments(record) : state.comment_count,
    },
    analysis: {
      status: state.status,
      progress: state.progress,
      message: state.message,
      phases: state.phases,
      length_stats: completedBody ? completedBody.length_stats : null,
    },
    ...body,
    pipeline: pipelineOf(record, state.status),
    error: state.error,
  }
}

function createdPayload(record) {
  return {
    bvid: record.bvid,
    status: 'queued',
    progress: 0,
    created_at: toIso(new Date(record.created_at_ms)),
  }
}

/* ==========================================================================
 * 分步流水线的参数合并
 * ========================================================================== */

/** 把「本次任务 settings」与已保存设置做部分合并（不含 Cookie）。 */
function mergeTaskSettings(taskSettings) {
  const saved = getSavedSettings()
  const taskAccount = taskSettings.account && typeof taskSettings.account === 'object' ? taskSettings.account : {}
  const mode =
    taskAccount.mode === 'account' || taskAccount.mode === 'anonymous' ? taskAccount.mode : saved.account.mode
  return {
    account: { mode },
    crawl: { ...DEFAULT_CRAWL, ...saved.crawl, ...(taskSettings.crawl || {}) },
    pipeline: { ...DEFAULT_PIPELINE, ...saved.pipeline, ...(taskSettings.pipeline || {}) },
  }
}

/**
 * 按采集参数估算「本次最多新增评论数」（mock 演示用，不触网）：
 *   - real_crawl=false → 离线占位数据，量级更小（演示不联网）；
 *   - 受 page_size × max_pages（0=不限）与 max_items（0=不限）双重限制；
 *   - 含楼中楼时估一个更大的量级。
 */
function estimateCommentTotal(bvid, settings) {
  const hash = hashCode(bvid)
  const crawl = settings.crawl
  const base = crawl.real_crawl ? 2_800 + (hash % 9_500) : 120 + (hash % 180)
  const caps = []
  if (crawl.max_pages > 0) caps.push(crawl.page_size * crawl.max_pages)
  if (crawl.max_items > 0) caps.push(crawl.max_items)
  let total = caps.length ? Math.min(base, ...caps) : base
  if (crawl.include_replies) total = Math.round(total * 1.6)
  return Math.max(1, total)
}

/* ==========================================================================
 * 对外接口（与契约一致）
 * ========================================================================== */

export function listVideos() {
  const now = nowMs()
  const videos = [...records.values()]
    .sort((a, b) => b.created_at_ms - a.created_at_ms)
    .map((record) => serializeListItem(record, now))
  return { total: videos.length, videos }
}

export function getDetail(bvid) {
  const record = records.get(bvid)
  if (!record) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }
  return { ok: true, status: 200, body: serializeDetail(record, nowMs()) }
}

/**
 * POST /api/videos：带 settings 时走分步流水线（可暂停），不带时保持旧行为（一次跑完）。
 * @param {string} bvid
 * @param {object|null} [settings]
 */
export function submitVideo(bvid, settings = null) {
  if (typeof bvid !== 'string' || !BV_PATTERN.test(bvid)) {
    return { ok: false, status: 400, body: { detail: 'BV 号格式不正确，应形如 BV1xx411c7mD' } }
  }
  const existing = records.get(bvid)
  if (existing) {
    const state = stateOf(existing, nowMs())
    if (!TERMINAL.has(state.status)) {
      const paused = WAITING.has(state.status)
      return {
        ok: false,
        status: 409,
        body: {
          detail: paused
            ? '该视频已暂停在等待阶段，请在详情页点「开始清洗 / 开始分析」继续'
            : '该视频已在分析队列中',
        },
      }
    }
    records.delete(bvid) // 已完成的视频再次提交 = 用新参数重新分析
  }

  const hash = hashCode(bvid)
  const createdAt = nowMs()
  const record = createRecord({
    bvid,
    createdAt,
    title: TITLE_POOL[hash % TITLE_POOL.length],
    owner: OWNER_POOL[hash % OWNER_POOL.length],
    pubdate: toIso(new Date(createdAt - (30 + (hash % 400)) * 24 * 3600 * 1000)),
  })

  if (settings && typeof settings === 'object') {
    record.staged = true
    record.settings = mergeTaskSettings(settings)
    record.stage = 'crawl'
    record.stage_start_ms = createdAt
    record.comment_total = estimateCommentTotal(bvid, record.settings)
  }

  records.set(bvid, record)
  return { ok: true, status: 200, body: createdPayload(record) }
}

export function retryVideo(bvid) {
  const record = records.get(bvid)
  if (!record) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }
  record.created_at_ms = nowMs()
  record.crawled_at_ms = null
  record.completed_at_ms = null
  record.failure = null
  if (record.staged) {
    record.waiting = null
    record.stage = 'crawl'
    record.stage_start_ms = record.created_at_ms
  }
  return { ok: true, status: 200, body: createdPayload(record) }
}

/**
 * POST /api/videos/{bvid}/continue：仅 crawled / cleaned 允许，其他状态 409。
 * 继续后任务回到 queued（进度显示 0），由阶段状态机推进。
 */
export function continueVideo(bvid) {
  const record = records.get(bvid)
  if (!record) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }

  const now = nowMs()
  const state = stateOf(record, now)
  if (!WAITING.has(state.status)) {
    return { ok: false, status: 409, body: { detail: `任务当前状态 ${state.status}，无需继续` } }
  }
  if (!record.staged) {
    return { ok: false, status: 409, body: { detail: '该任务不是分步流水线任务，无需继续' } }
  }

  record.waiting = null
  record.failure = null
  record.stage = state.status === 'crawled' ? 'clean' : 'analyze'
  record.stage_start_ms = now
  if (record.crawled_at_ms == null) record.crawled_at_ms = now
  return { ok: true, status: 200, body: createdPayload(record) }
}

export function deleteVideo(bvid) {
  if (!records.has(bvid)) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }
  records.delete(bvid)
  return { ok: true, status: 200, body: { deleted: true } }
}

/** 仅测试/调试用：把仓库恢复成预置的 3 条演示数据。 */
export function resetStore() {
  records = seed()
}
