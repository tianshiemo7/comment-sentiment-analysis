/**
 * src/api/artifacts.js — 「数据项目」与「单独评论」接口封装（与 videos.js / client.js 并列）
 *
 * 契约来源：docs/API契约.md 第四点五节（唯一契约来源，字段名不得改名）
 *   GET    /api/artifacts                            -> {"groups":[{key,label,items}×3]}
 *   POST   /api/artifacts/upload                     multipart: files(多个)+name -> 项目对象
 *   GET    /api/artifacts/{id}                       -> 项目对象
 *   POST   /api/artifacts/{id}/analyze               -> 项目对象（重新处理）
 *   GET    /api/artifacts/{id}/stages                -> {stages:[{key,label,rows,columns}]}
 *   GET    /api/artifacts/{id}/table?stage&offset&limit -> {stage,columns,rows,total_rows,offset,limit,encoding}
 *   GET    /api/artifacts/{id}/comments?offset&limit&label -> {total,offset,limit,items}
 *   GET    /api/artifacts/{id}/analysis              -> {method,total,sentiment,keywords,length,sample}
 *   POST   /api/artifacts/{id}/open                  -> {"opened":true,"path":str}（会真调用系统 opener）
 *   GET    /api/artifacts/{id}/path?stage=clean      -> {"stage":str,"path":str,"exists":bool}（只读，不弹 Excel）
 *   DELETE /api/artifacts/{id}                       -> {"deleted":true}
 *   POST   /api/solo-comments                        -> 单条评论对象
 *   GET    /api/solo-comments?offset&limit           -> {total,offset,limit,items}
 *   DELETE /api/solo-comments/{id}                   -> {"deleted":true,"deleted_count":n}
 *   DELETE /api/solo-comments?ids=a,b                -> {"deleted":true,"deleted_count":n}
 *
 * 全部使用同源相对路径；错误统一抛出带中文 detail 的 Error，并挂 err.status。
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

const BACKEND_HINT = '请确认 FastAPI 服务已启动：python -m uvicorn src.inference.app:app --port 8000'

/**
 * 统一请求：解析 JSON、非 2xx 抛错（优先使用后端 detail 文案）。
 * @param {string} path
 * @param {RequestInit} [options]
 */
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(path, options)
  } catch (err) {
    throw new Error(`无法连接分析服务 ${path}：${err.message}。${BACKEND_HINT}`)
  }

  const raw = await res.text()
  let data = null
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      data = { detail: raw.slice(0, 300) }
    }
  }

  if (!res.ok) {
    const detail = data && data.detail !== undefined ? data.detail : ''
    const text = detail ? (typeof detail === 'string' ? detail : JSON.stringify(detail)) : `请求失败（HTTP ${res.status}）`
    const error = new Error(text)
    error.status = res.status
    throw error
  }

  return data
}

/** 左栏三段的唯一数据源。 */
export function listArtifacts() {
  return request('/api/artifacts')
}

/**
 * 上传爬取数据（多选文件）。不要手动设置 Content-Type，浏览器需自动补 multipart boundary。
 * @param {File[]} files
 * @param {string} [name] 项目显示名；为空则由后端显示「未自定义数据」
 */
export function uploadArtifacts(files, name = '') {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  const trimmed = String(name ?? '').trim()
  if (trimmed) form.append('name', trimmed)
  return request('/api/artifacts/upload', { method: 'POST', body: form })
}

/** 项目状态 */
export function getArtifact(projectId) {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}`)
}

/** 重新处理项目 */
export function analyzeArtifact(projectId, mode = 'auto') {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/analyze`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ mode }),
  })
}

/** 阶段清单（key/label/rows/columns） */
export function getArtifactStages(projectId) {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/stages`)
}

/**
 * 三张表之一的分页数据。
 * @param {string} projectId
 * @param {{stage?: 'initial'|'clean'|'excluded', offset?: number, limit?: number}} [query]
 */
export function getArtifactTable(projectId, query = {}) {
  const search = new URLSearchParams()
  if (query.stage) search.set('stage', query.stage)
  if (query.offset !== undefined) search.set('offset', String(query.offset))
  if (query.limit !== undefined) search.set('limit', String(query.limit))
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/table?${search.toString()}`)
}

/**
 * 逐条评论情感（分页 + label 筛选）。
 * @param {string} projectId
 * @param {{offset?: number, limit?: number, label?: string}} [query]
 */
export function getArtifactComments(projectId, query = {}) {
  const search = new URLSearchParams()
  if (query.offset !== undefined) search.set('offset', String(query.offset))
  if (query.limit !== undefined) search.set('limit', String(query.limit))
  if (query.label) search.set('label', query.label)
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/comments?${search.toString()}`)
}

/** 聚合情感分析（占比 / 词频 / 长度分布） */
export function getArtifactAnalysis(projectId) {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/analysis`)
}

/** 在服务器本机用系统默认程序打开 CSV（会真正调用系统 opener） */
export function openArtifact(projectId, stage = 'clean') {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/open`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ stage }),
  })
}

/**
 * 只读查询某阶段 CSV 的绝对路径：**不调用系统 opener、不弹 Excel**。
 * 契约：`GET /api/artifacts/{id}/path?stage=clean` -> {"stage":"clean","path":"D:\\...\\clean.csv","exists":true}
 * @param {string} projectId
 * @param {'initial'|'clean'|'excluded'} [stage]
 */
export function getArtifactPath(projectId, stage = 'clean') {
  const search = new URLSearchParams()
  search.set('stage', stage)
  return request(`/api/artifacts/${encodeURIComponent(projectId)}/path?${search.toString()}`)
}

/** 删除项目 */
export function deleteArtifact(projectId) {
  return request(`/api/artifacts/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
}

/**
 * 保存一条单独评论。
 * @param {{text: string, label: string, confidence: number, probs?: object, method?: string}} payload
 */
export function saveSoloComment(payload) {
  return request('/api/solo-comments', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
}

/** 单独评论列表（按 created_at 倒序） */
export function listSoloComments(query = {}) {
  const search = new URLSearchParams()
  if (query.offset !== undefined) search.set('offset', String(query.offset))
  if (query.limit !== undefined) search.set('limit', String(query.limit))
  return request(`/api/solo-comments?${search.toString()}`)
}

/** 删除单条评论 */
export function deleteSoloComment(id) {
  return request(`/api/solo-comments/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** 批量删除（清空全部） */
export function deleteSoloComments(ids) {
  const search = new URLSearchParams()
  search.set('ids', (Array.isArray(ids) ? ids : []).join(','))
  return request(`/api/solo-comments?${search.toString()}`, { method: 'DELETE' })
}

export default {
  listArtifacts,
  uploadArtifacts,
  getArtifact,
  analyzeArtifact,
  getArtifactStages,
  getArtifactTable,
  getArtifactComments,
  getArtifactAnalysis,
  openArtifact,
  getArtifactPath,
  deleteArtifact,
  saveSoloComment,
  listSoloComments,
  deleteSoloComment,
  deleteSoloComments,
}
