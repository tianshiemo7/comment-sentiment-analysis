/**
 * mock/index.js — Vite 开发服务器 mock 插件
 *
 * ⚠️ 默认不加载：只有显式 `VITE_USE_MOCK=1 npm run dev`（demo 模式）才会接入本插件；
 *    删除 vite.config.js 里的 `mockApiPlugin()` 引用（及本目录）即可彻底移除，
 *    前端业务代码一行都不用改（mock 只依赖契约字段约定）。
 *
 * 挂载方式：configureServer 中「直接」调用 server.middlewares.use(...)，
 * 中间件注册位置早于 Vite 内建中间件（含 server.proxy），因此：
 *   - /api/videos*、/api/artifacts*、/api/solo-comments*、/api/predict、/api/settings、
 *     /api/crawler/session/check、/health 被本插件接管；
 *   - 其它路径（如 /api/models、/api/predict/batch）继续 next() → 走 proxy 到 FastAPI。
 * 同源请求，无需 CORS。
 *
 * 2026-10-17 起新增：设置持久化（进程内存）、登录态检查、分步流水线 continue 与新状态。
 */

import {
  analyzeProject,
  deleteProject,
  deleteSolo,
  deleteSoloMany,
  getAnalysis,
  getComments,
  getProject,
  getProjectPath,
  getStages,
  getTable,
  listProjects,
  listSolo,
  openProject,
  resetArtifacts,
  saveSolo,
  uploadProject,
} from './artifacts.js'
import { predictRuleBased } from './predict.js'
import { cancelLogin, getLoginStatus, resetLogin, startLogin } from './account.js'
import { checkSession, getSettingsPayload, resetSettings, updateSettings } from './settings.js'
import {
  continueVideo,
  deleteVideo,
  getDetail,
  listVideos,
  retryVideo,
  resetStore,
  submitVideo,
} from './store.js'

const BV = 'BV[0-9A-Za-z]{10}'
const RE_DETAIL = new RegExp(`^/api/videos/(${BV})/detail$`)
const RE_RETRY = new RegExp(`^/api/videos/(${BV})/retry$`)
const RE_CONTINUE = new RegExp(`^/api/videos/(${BV})/continue$`)
const RE_ITEM = new RegExp(`^/api/videos/(${BV})$`)

const RE_ARTIFACT_ITEM = /^\/api\/artifacts\/([^/]+)$/
const RE_ARTIFACT_ACTION = /^\/api\/artifacts\/([^/]+)\/(stages|table|comments|analysis|path|open|analyze)$/
const RE_SOLO_ITEM = /^\/api\/solo-comments\/([^/]+)$/

function sendJson(res, status, body) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(body))
}

/** 解析 JSON 请求体（契约里 POST 均为 application/json）。 */
function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = ''
    req.on('data', (chunk) => {
      raw += chunk
      if (raw.length > 1_000_000) req.destroy()
    })
    req.on('end', () => {
      if (!raw) return resolve({})
      try {
        resolve(JSON.parse(raw))
      } catch (err) {
        reject(err)
      }
    })
    req.on('error', reject)
  })
}

/** 读取原始请求体（multipart 上传用）。 */
function readRawBody(req, limit = 60 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > limit) {
        reject(new Error('请求体超过 50 MB 上限'))
        req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

/**
 * 极简 multipart/form-data 解析：只需要字段文本与文件名/大小（mock 不保存文件内容）。
 * @returns {{fields: Record<string,string>, files: Array<{field:string,filename:string,size:number}>}}
 */
export function parseMultipart(buffer, contentType) {
  const match = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType || '')
  const boundary = match ? (match[1] || match[2] || '').trim() : ''
  const fields = {}
  const files = []
  if (!boundary) return { fields, files }

  const delimiter = Buffer.from(`--${boundary}`)
  let cursor = buffer.indexOf(delimiter)
  if (cursor === -1) return { fields, files }
  cursor += delimiter.length

  while (cursor < buffer.length) {
    if (buffer.slice(cursor, cursor + 2).toString('latin1') === '--') break
    if (buffer.slice(cursor, cursor + 2).toString('latin1') === '\r\n') cursor += 2

    const next = buffer.indexOf(delimiter, cursor)
    if (next === -1) break
    const end = Math.max(cursor, next - 2) // 去掉结尾的 \r\n
    const part = buffer.slice(cursor, end)
    const sep = part.indexOf('\r\n\r\n')
    if (sep !== -1) {
      const headers = part.slice(0, sep).toString('utf8')
      const body = part.slice(sep + 4)
      const nameMatch = /name="([^"]*)"/i.exec(headers)
      const fileMatch = /filename="([^"]*)"/i.exec(headers)
      if (nameMatch) {
        if (fileMatch && fileMatch[1]) {
          files.push({ field: nameMatch[1], filename: fileMatch[1], size: body.length })
        } else {
          fields[nameMatch[1]] = body.toString('utf8')
        }
      }
    }
    cursor = next + delimiter.length
  }
  return { fields, files }
}

/** 三段的唯一数据源：GET /api/artifacts（契约 4.5 的 groups）。 */
function artifactsPayload() {
  return {
    groups: [
      { key: 'projects', label: '数据项目', items: listProjects() },
      { key: 'solo', label: '单独评论', items: listSolo({ offset: 0, limit: 500 }).items },
      { key: 'videos', label: '视频任务', items: listVideos().videos },
    ],
  }
}

function resolveByResult(res, result) {
  sendJson(res, result.status, result.body)
}

/** 把 URLSearchParams 里的数字参数解析成 number（缺省时返回 undefined）。 */
function numParam(search, key) {
  const raw = search.get(key)
  if (raw === null || raw === '') return undefined
  const value = Number(raw)
  return Number.isFinite(value) ? value : undefined
}

async function handle(req, res) {
  const url = new URL(req.url, 'http://localhost')
  const path = url.pathname
  const method = (req.method || 'GET').toUpperCase()

  // 健康检查：后端没起也能通过，避免首页自检报错
  if (path === '/health' && method === 'GET') {
    sendJson(res, 200, {
      status: 'ok',
      service: 'comment-sentiment-analysis (vite mock)',
      version: '0.1.0-mock',
    })
    return true
  }

  // ---------- 系统设置与账号（spec 第 2 节；Cookie 只回 masked） ----------
  if (path === '/api/settings' && method === 'GET') {
    sendJson(res, 200, getSettingsPayload())
    return true
  }

  if (path === '/api/settings' && method === 'PUT') {
    const body = await readJsonBody(req)
    const result = updateSettings(body)
    if (result.ok) console.log('[mock] 设置已更新（Cookie 仅存内存，不回显明文）')
    resolveByResult(res, result)
    return true
  }

  if (path === '/api/crawler/session/check' && method === 'POST') {
    let body = {}
    try {
      body = await readJsonBody(req)
    } catch {
      body = {}
    }
    resolveByResult(res, checkSession(body && body.cookie))
    return true
  }

  // ---------- 人工登录窗口（spec 2.4；mock 不拉起浏览器、不触网） ----------
  if (path === '/api/account/login/start' && method === 'POST') {
    let body = {}
    try {
      body = await readJsonBody(req)
    } catch {
      body = {}
    }
    const result = startLogin(body)
    console.log('[mock] 已启动人工登录窗口模拟（约 3 秒后成功）')
    resolveByResult(res, result)
    return true
  }

  if (path === '/api/account/login/status' && method === 'GET') {
    resolveByResult(res, getLoginStatus())
    return true
  }

  if (path === '/api/account/login/cancel' && method === 'POST') {
    console.log('[mock] 取消人工登录窗口')
    resolveByResult(res, cancelLogin())
    return true
  }

  // ---------- 单条预测（规则占位，保证「保存到左栏」链路在无后端时可演示） ----------
  if (path === '/api/predict' && method === 'POST') {
    const body = await readJsonBody(req)
    const text = body && typeof body.text === 'string' ? body.text : ''
    if (!text.trim()) {
      sendJson(res, 400, { detail: '评论内容不能为空' })
      return true
    }
    sendJson(res, 200, predictRuleBased(text))
    return true
  }

  // ---------- 数据项目与单独评论（契约 4.5） ----------
  if (path === '/api/artifacts' && method === 'GET') {
    sendJson(res, 200, artifactsPayload())
    return true
  }

  if (path === '/api/artifacts/upload' && method === 'POST') {
    const raw = await readRawBody(req)
    const { fields, files } = parseMultipart(raw, req.headers['content-type'])
    resolveByResult(res, uploadProject({ files, name: fields.name }))
    return true
  }

  if (path === '/api/solo-comments' && method === 'GET') {
    sendJson(res, 200, listSolo({ offset: numParam(url.searchParams, 'offset'), limit: numParam(url.searchParams, 'limit') }))
    return true
  }

  if (path === '/api/solo-comments' && method === 'POST') {
    const body = await readJsonBody(req)
    const result = saveSolo(body)
    if (result.ok) console.log(`[mock] 保存单条评论 ${result.body.id} → ${result.body.label}`)
    resolveByResult(res, result)
    return true
  }

  if (path === '/api/solo-comments' && method === 'DELETE') {
    const raw = url.searchParams.get('ids') || ''
    const ids = raw.split(',').map((item) => item.trim()).filter(Boolean)
    resolveByResult(res, deleteSoloMany(ids))
    return true
  }

  const soloItem = RE_SOLO_ITEM.exec(path)
  if (soloItem && method === 'DELETE') {
    console.log(`[mock] 删除单条评论 ${soloItem[1]}`)
    resolveByResult(res, deleteSolo(soloItem[1]))
    return true
  }

  const artifactAction = RE_ARTIFACT_ACTION.exec(path)
  if (artifactAction) {
    const id = artifactAction[1]
    const action = artifactAction[2]
    const search = url.searchParams

    if (action === 'stages' && method === 'GET') {
      resolveByResult(res, getStages(id))
      return true
    }
    if (action === 'table' && method === 'GET') {
      resolveByResult(
        res,
        getTable(id, {
          stage: search.get('stage') || 'clean',
          offset: numParam(search, 'offset'),
          limit: numParam(search, 'limit'),
        }),
      )
      return true
    }
    if (action === 'comments' && method === 'GET') {
      resolveByResult(
        res,
        getComments(id, {
          offset: numParam(search, 'offset'),
          limit: numParam(search, 'limit'),
          label: search.get('label') || '',
        }),
      )
      return true
    }
    if (action === 'analysis' && method === 'GET') {
      resolveByResult(res, getAnalysis(id))
      return true
    }
    // 只读取路径：不触发「打开」，用于前端「复制文件地址」
    if (action === 'path' && method === 'GET') {
      resolveByResult(res, getProjectPath(id, { stage: search.get('stage') || 'clean' }))
      return true
    }
    if (action === 'open' && method === 'POST') {
      let body = {}
      try {
        body = await readJsonBody(req)
      } catch {
        body = {}
      }
      resolveByResult(res, openProject(id, { stage: (body && body.stage) || 'clean' }))
      return true
    }
    if (action === 'analyze' && method === 'POST') {
      resolveByResult(res, analyzeProject(id))
      return true
    }
  }

  const artifactItem = RE_ARTIFACT_ITEM.exec(path)
  if (artifactItem) {
    const id = artifactItem[1]
    if (method === 'GET') {
      resolveByResult(res, getProject(id))
      return true
    }
    if (method === 'DELETE') {
      console.log(`[mock] 删除数据项目 ${id}`)
      resolveByResult(res, deleteProject(id))
      return true
    }
  }

  // ---------- 视频任务 ----------
  if (path === '/api/videos' && method === 'GET') {
    sendJson(res, 200, listVideos())
    return true
  }

  if (path === '/api/videos' && method === 'POST') {
    const body = await readJsonBody(req)
    const result = submitVideo(body && body.bvid, (body && body.settings) || null)
    if (result.ok) {
      console.log(`[mock] 提交 ${body.bvid} → queued${body.settings ? '（分步流水线）' : ''}`)
    }
    resolveByResult(res, result)
    return true
  }

  const detail = RE_DETAIL.exec(path)
  if (detail && method === 'GET') {
    resolveByResult(res, getDetail(detail[1]))
    return true
  }

  const retry = RE_RETRY.exec(path)
  if (retry && method === 'POST') {
    console.log(`[mock] 重试 ${retry[1]}`)
    resolveByResult(res, retryVideo(retry[1]))
    return true
  }

  const resume = RE_CONTINUE.exec(path)
  if (resume && method === 'POST') {
    console.log(`[mock] 继续 ${resume[1]}`)
    resolveByResult(res, continueVideo(resume[1]))
    return true
  }

  const item = RE_ITEM.exec(path)
  if (item && method === 'DELETE') {
    console.log(`[mock] 删除 ${item[1]}`)
    resolveByResult(res, deleteVideo(item[1]))
    return true
  }

  // 调试用：恢复预置的演示数据（仅 mock 存在）
  if (path === '/__mock/reset' && method === 'POST') {
    resetStore()
    resetArtifacts()
    resetSettings()
    resetLogin()
    sendJson(res, 200, { reset: true })
    return true
  }

  return false
}

/** @returns {import('vite').Plugin} */
export function mockApiPlugin() {
  return {
    name: 'mock-bilibili-api',
    apply: 'serve', // 只在 dev server 生效；生产构建产物不含任何 mock 代码
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        handle(req, res).then(
          (handled) => {
            if (!handled) next()
          },
          (err) => {
            sendJson(res, 500, { detail: `mock 服务异常：${err.message}` })
          },
        )
      })
    },
  }
}

export default mockApiPlugin
