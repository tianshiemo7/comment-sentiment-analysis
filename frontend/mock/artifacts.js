/**
 * mock/artifacts.js — 「数据项目」与「单独评论」内存仓库（仅 mock 层）
 *
 * 严格按 docs/API契约.md 第 4.5 节返回字段；状态随时间自然推进
 * （上传后 queued → processing → completed），真实后端就绪后随 mock/ 一起删除。
 */

import { buildProjectData } from './artifactFixtures.js'
import { toIso } from './fixtures.js'

/** 时间线：上传后约 15 秒推进到完成，便于演示轮询。 */
const PIPELINE = { queuedMs: 1_500, processingMs: 13_000 }
/** 预置的「进行中」项目故意很慢，演示期间不会跑完（与视频预置数据同理）。 */
const SLOW_PIPELINE = { queuedMs: 1_500, processingMs: 6 * 3600 * 1000 }

const TERMINAL = new Set(['completed', 'failed'])

let seq = 0

function nextId(prefix) {
  seq += 1
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`
  return `${prefix}_${stamp}_${String(seq).padStart(4, '0')}`
}

function createProject({ name, sources, createdAtMs, timeline, seed, method = 'rule_placeholder', shape }) {
  const sourceRows = sources.reduce((acc, item) => acc + (item.rows || 0), 0) || 312
  // 三张表的行数保持「初始 ≥ 筛选后」的自洽关系（清洗会排除一部分）
  const rows = shape || {
    initial: sourceRows,
    clean: Math.max(1, Math.round(sourceRows * 0.82)),
    excluded: Math.max(1, Math.round(sourceRows * 0.13)),
  }
  const data = buildProjectData({
    seed,
    initial: rows.initial,
    clean: rows.clean,
    excluded: rows.excluded,
    offsetMs: createdAtMs,
    sourceFile: sources[0]?.filename || 'uploaded.csv',
  })
  return {
    id: nextId('p'),
    name: name || '未自定义数据',
    created_at_ms: createdAtMs,
    timeline,
    sources,
    method,
    data,
    error: null,
    failed: false,
  }
}

/** 预置 2 条演示项目：1 条已完成（三张表齐全）、1 条长期处理中。 */
function seed() {
  const now = Date.now()
  return [
    createProject({
      name: '手机评论采集（示例）',
      createdAtMs: now - 26 * 3600 * 1000,
      timeline: PIPELINE,
      seed: 3,
      sources: [{ filename: 'BV1UB4y1W7Xu_comments.csv', rows: 312, size: 812_345 }],
    }),
    createProject({
      name: '',
      createdAtMs: now - 900 * 1000,
      timeline: SLOW_PIPELINE,
      seed: 7,
      sources: [{ filename: 'douyin_comments_20261015.xlsx', rows: 240, size: 534_210 }],
    }),
  ]
}

let projects = seed()

let soloComments = [
  {
    id: 'c_demo_0001',
    text: '这个商品质量很好，物流也快，非常满意。',
    label: 'positive',
    confidence: 0.8084,
    probs: { positive: 0.8084, negative: 0.1187, neutral: 0.0729 },
    method: 'rule_placeholder',
    created_at: toIso(new Date(Date.now() - 3600 * 1000)),
  },
  {
    id: 'c_demo_0002',
    text: '质量太差了，用了一天就坏了，非常失望。',
    label: 'negative',
    confidence: 0.7741,
    probs: { positive: 0.1401, negative: 0.7741, neutral: 0.0858 },
    method: 'rule_placeholder',
    created_at: toIso(new Date(Date.now() - 1800 * 1000)),
  },
]

function clamp01(value) {
  return Math.min(1, Math.max(0, value))
}

/** 某阶段 CSV 的（模拟）落盘路径——mock 不真的写文件，只回路径字符串。 */
function filePathOf(id, stage) {
  return `D:\\DevforDS\\comment-sentiment-analysis\\data\\uploads\\${id}\\${stage}.csv`
}

/** 项目当前状态（按创建时间自然推进）。 */
function projectState(project, now = Date.now()) {
  if (project.failed) return { status: 'failed', progress: 62, message: '处理失败' }
  const elapsed = Math.max(0, now - project.created_at_ms)
  const { queuedMs, processingMs } = project.timeline
  if (elapsed < queuedMs) return { status: 'queued', progress: 0, message: '已加入处理队列，等待调度…' }
  const after = elapsed - queuedMs
  if (after < processingMs) {
    const f = clamp01(after / processingMs)
    return { status: 'processing', progress: Math.min(99, Math.round(5 + 94 * f)), message: '正在清洗与情感分析…' }
  }
  return { status: 'completed', progress: 100, message: '处理完成' }
}

/** 对外项目对象（契约 4.5.1）。 */
function serializeProject(project, now = Date.now()) {
  const state = projectState(project, now)
  const done = state.status === 'completed'
  const stages = [
    { key: 'initial', label: '初始数据', rows: project.data.initial.rows.length, columns: project.data.initial.columns.length },
    { key: 'clean', label: '筛选后结果', rows: project.data.clean.rows.length, columns: project.data.clean.columns.length },
    { key: 'excluded', label: '排除记录', rows: project.data.excluded.rows.length, columns: project.data.excluded.columns.length },
  ]
  return {
    id: project.id,
    name: project.name || '未自定义数据',
    created_at: toIso(new Date(project.created_at_ms)),
    status: state.status,
    progress: state.progress,
    message: state.message,
    sources: project.sources,
    // 未完成时阶段行数不可知，按契约精神返回结构完整、数值为 0 的占位
    stages: done ? stages : stages.map((item) => ({ ...item, rows: 0, columns: 0 })),
    sentiment: done ? project.data.sentiment : null,
    method: project.method,
    error: project.failed ? project.error : null,
  }
}

export function listProjects() {
  const now = Date.now()
  return [...projects].sort((a, b) => b.created_at_ms - a.created_at_ms).map((item) => serializeProject(item, now))
}

function findProject(id) {
  return projects.find((item) => item.id === id) || null
}

export function getProject(id) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  return { ok: true, status: 200, body: serializeProject(project) }
}

const ALLOWED_EXT = ['.csv', '.txt', '.tsv', '.xlsx']
const MAX_FILE = 20 * 1024 * 1024
const MAX_TOTAL = 50 * 1024 * 1024

export function uploadProject({ files, name }) {
  if (!Array.isArray(files) || files.length === 0) {
    return { ok: false, status: 400, body: { detail: '请至少选择一个 .csv / .xlsx 文件' } }
  }
  let total = 0
  for (const file of files) {
    const filename = String(file.filename || '')
    const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase()
    if (!ALLOWED_EXT.includes(ext)) {
      return { ok: false, status: 400, body: { detail: `不支持的文件格式：${filename}` } }
    }
    if (file.size === 0) return { ok: false, status: 400, body: { detail: `文件为空：${filename}` } }
    if (file.size > MAX_FILE) return { ok: false, status: 413, body: { detail: `单个文件超过 20 MB：${filename}` } }
    total += file.size
  }
  if (total > MAX_TOTAL) return { ok: false, status: 413, body: { detail: '文件合计超过 50 MB' } }

  const now = Date.now()
  const sources = files.map((file) => ({
    filename: String(file.filename).split(/[\\/]/).pop(),
    rows: 80 + (file.size % 260),
    size: file.size,
  }))
  const project = createProject({
    name: typeof name === 'string' ? name.trim() : '',
    sources,
    createdAtMs: now,
    timeline: PIPELINE,
    seed: (seq + 11) % 90,
  })
  projects = [project, ...projects]
  console.log(`[mock] 上传 ${files.length} 个文件 → 数据项目 ${project.id}（${project.name}）`)
  return { ok: true, status: 200, body: serializeProject(project, now) }
}

export function analyzeProject(id) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  project.created_at_ms = Date.now()
  project.failed = false
  project.timeline = PIPELINE
  console.log(`[mock] 重新处理数据项目 ${id}`)
  return { ok: true, status: 200, body: serializeProject(project) }
}

export function getStages(id) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  const body = serializeProject(project)
  return { ok: true, status: 200, body: { stages: body.stages } }
}

const STAGES = ['initial', 'clean', 'excluded']

export function getTable(id, { stage = 'clean', offset = 0, limit = 100 } = {}) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  if (!STAGES.includes(stage)) return { ok: false, status: 400, body: { detail: `非法阶段：${stage}` } }
  const safeLimit = Math.min(500, Math.max(1, Number(limit) || 100))
  const safeOffset = Math.max(0, Number(offset) || 0)
  const state = projectState(project)
  if (state.status !== 'completed') {
    return {
      ok: true,
      status: 200,
      body: { stage, columns: [], rows: [], total_rows: 0, offset: safeOffset, limit: safeLimit, encoding: 'utf-8-sig' },
    }
  }
  const table = project.data[stage]
  return {
    ok: true,
    status: 200,
    body: {
      stage,
      columns: table.columns,
      rows: table.rows.slice(safeOffset, safeOffset + safeLimit),
      total_rows: table.rows.length,
      offset: safeOffset,
      limit: safeLimit,
      encoding: 'utf-8-sig',
    },
  }
}

export function getComments(id, { offset = 0, limit = 50, label = '' } = {}) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  if (label && !['positive', 'negative', 'neutral'].includes(label)) {
    return { ok: false, status: 400, body: { detail: `非法情感标签：${label}` } }
  }
  const safeLimit = Math.min(500, Math.max(1, Number(limit) || 50))
  const safeOffset = Math.max(0, Number(offset) || 0)
  const state = projectState(project)
  if (state.status !== 'completed') {
    return { ok: true, status: 200, body: { total: 0, offset: safeOffset, limit: safeLimit, items: [] } }
  }
  const all = label ? project.data.comments.filter((item) => item.label === label) : project.data.comments
  return {
    ok: true,
    status: 200,
    body: { total: all.length, offset: safeOffset, limit: safeLimit, items: all.slice(safeOffset, safeOffset + safeLimit) },
  }
}

export function getAnalysis(id) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  const state = projectState(project)
  if (state.status !== 'completed') {
    return {
      ok: true,
      status: 200,
      body: { method: project.method, total: 0, sentiment: null, keywords: { positive: [], negative: [] }, length: null, sample: [] },
    }
  }
  return { ok: true, status: 200, body: project.data.analysis }
}

export function openProject(id, { stage = 'clean' } = {}) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  if (!STAGES.includes(stage)) return { ok: false, status: 400, body: { detail: `非法阶段：${stage}` } }
  const path = filePathOf(id, stage)
  console.log(`[mock] 打开 ${path}`)
  return { ok: true, status: 200, body: { opened: true, path } }
}

/**
 * 只读查询某阶段 CSV 路径（契约新增 `GET /api/artifacts/{id}/path`）：
 * **不调用系统 opener**；`exists` 表示该阶段文件是否已生成（未处理完为 false）。
 */
export function getProjectPath(id, { stage = 'clean' } = {}) {
  const project = findProject(id)
  if (!project) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  if (!STAGES.includes(stage)) return { ok: false, status: 400, body: { detail: `非法阶段：${stage}` } }
  return {
    ok: true,
    status: 200,
    body: {
      stage,
      path: filePathOf(id, stage),
      exists: projectState(project).status === 'completed',
    },
  }
}

export function deleteProject(id) {
  if (!findProject(id)) return { ok: false, status: 404, body: { detail: `未找到数据项目 ${id}` } }
  projects = projects.filter((item) => item.id !== id)
  return { ok: true, status: 200, body: { deleted: true } }
}

// ---------------------------------------------------------------------------
// 单独评论
// ---------------------------------------------------------------------------

const LABELS = ['positive', 'negative', 'neutral']

export function listSolo({ offset = 0, limit = 50 } = {}) {
  const safeLimit = Math.min(500, Math.max(1, Number(limit) || 50))
  const safeOffset = Math.max(0, Number(offset) || 0)
  const all = [...soloComments].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  return { total: all.length, offset: safeOffset, limit: safeLimit, items: all.slice(safeOffset, safeOffset + safeLimit) }
}

export function saveSolo(payload = {}) {
  const text = typeof payload.text === 'string' ? payload.text.trim() : ''
  if (!text) return { ok: false, status: 400, body: { detail: '文本不能为空' } }
  const label = String(payload.label || '')
  if (!LABELS.includes(label)) return { ok: false, status: 400, body: { detail: `非法情感标签：${label}` } }
  const confidence = Number(payload.confidence)
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    return { ok: false, status: 400, body: { detail: 'confidence 必须是 0~1 之间的数值' } }
  }
  const item = {
    id: nextId('c'),
    text,
    label,
    confidence,
    probs: payload.probs && typeof payload.probs === 'object' ? payload.probs : {},
    method: payload.method || 'rule_placeholder',
    created_at: toIso(new Date()),
  }
  soloComments = [item, ...soloComments]
  return { ok: true, status: 200, body: item }
}

export function deleteSolo(id) {
  const before = soloComments.length
  soloComments = soloComments.filter((item) => item.id !== id)
  if (soloComments.length === before) return { ok: false, status: 404, body: { detail: `未找到单条评论 ${id}` } }
  return { ok: true, status: 200, body: { deleted: true, deleted_count: 1 } }
}

export function deleteSoloMany(ids = []) {
  if (!Array.isArray(ids) || ids.length === 0) {
    return { ok: false, status: 400, body: { detail: '请提供要删除的 ids' } }
  }
  const before = soloComments.length
  soloComments = soloComments.filter((item) => !ids.includes(item.id))
  return { ok: true, status: 200, body: { deleted: true, deleted_count: before - soloComments.length } }
}

/** 仅调试：恢复预置数据。 */
export function resetArtifacts() {
  projects = seed()
  soloComments = soloComments.slice(0, 2)
}

export { TERMINAL }
