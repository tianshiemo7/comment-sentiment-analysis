/**
 * mock/store.js — 内存任务仓库（mock 专用，真实后端就绪后随 mock/ 一起删除）
 *
 * 对上层暴露与 docs/API契约.md 完全一致的数据结构；
 * 状态随时间自然推进（默认 30 秒走完 queued → crawling → processing → completed）。
 */

import { DEFAULT_TIMELINE, crawledCount, snapshot } from './pipeline.js'
import { OWNER_POOL, TITLE_POOL, buildCompletedDetail, presetVideos, toIso } from './fixtures.js'

/** 教学用途：提交这个 BV 号会走「失败」分支，用于演示失败态与「重试」。 */
export const MOCK_FAIL_BVID = 'BV1m0ckfai11'

const BV_PATTERN = /^BV[0-9A-Za-z]{10}$/

const TERMINAL = new Set(['completed', 'failed'])

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
    timeline,
    // deriveCommentTotal=true 时由 fixtures 的趋势数据推导，保证 total 与趋势自洽
    comment_total: deriveCommentTotal ? null : commentTotal ?? 2_800 + (hash % 9_500),
    alwaysRevealMeta,
    error: null,
  }
}

/** 预置 2 条演示数据：1 条 completed、1 条 crawling 进行中。 */
function seed() {
  const { completed, crawling } = presetVideos(nowMs())
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
  return records
}

let records = seed()

/** 任务当前快照（含「失败」魔法 BV 的判定）。 */
function stateOf(record, now = nowMs()) {
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

function totalComments(record) {
  return record.comment_total ?? buildCompletedDetail(record).comment_total
}

function metaVisible(record, state) {
  if (record.alwaysRevealMeta) return true
  // 契约：尚未爬到时 title / owner 为 null；爬取阶段开始后即可拿到视频元信息
  return state.status !== 'queued'
}

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

  const crawledAt = state.status === 'completed' ? toIso(new Date(record.crawled_at_ms ?? record.created_at_ms + 13_000)) : null

  return {
    video: {
      bvid: record.bvid,
      title: visible ? record.title : null,
      owner: visible ? record.owner : null,
      cover_url: record.cover_url,
      pubdate: visible ? record.pubdate : null,
      crawled_at: crawledAt,
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

export function submitVideo(bvid) {
  if (typeof bvid !== 'string' || !BV_PATTERN.test(bvid)) {
    return { ok: false, status: 400, body: { detail: 'BV 号格式不正确，应形如 BV1xx411c7mD' } }
  }
  const existing = records.get(bvid)
  if (existing) {
    const state = stateOf(existing, nowMs())
    if (!TERMINAL.has(state.status)) {
      return { ok: false, status: 409, body: { detail: '该视频已在分析队列中' } }
    }
    // 已完成的视频再次提交 = 重新分析（mock 的友好行为，契约未禁止）
    existing.created_at_ms = nowMs()
    existing.crawled_at_ms = null
    return { ok: true, status: 200, body: createdPayload(existing) }
  }

  const hash = hashCode(bvid)
  const record = createRecord({
    bvid,
    createdAt: nowMs(),
    title: TITLE_POOL[hash % TITLE_POOL.length],
    owner: OWNER_POOL[hash % OWNER_POOL.length],
    pubdate: toIso(new Date(nowMs() - (30 + (hash % 400)) * 24 * 3600 * 1000)),
  })
  records.set(bvid, record)
  return { ok: true, status: 200, body: createdPayload(record) }
}

export function retryVideo(bvid) {
  const record = records.get(bvid)
  if (!record) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }
  record.created_at_ms = nowMs()
  record.crawled_at_ms = null
  return { ok: true, status: 200, body: createdPayload(record) }
}

export function deleteVideo(bvid) {
  if (!records.has(bvid)) return { ok: false, status: 404, body: { detail: `未找到视频 ${bvid} 的分析任务` } }
  records.delete(bvid)
  return { ok: true, status: 200, body: { deleted: true } }
}

/** 仅测试/调试用：把仓库恢复成预置的 2 条演示数据。 */
export function resetStore() {
  records = seed()
}
