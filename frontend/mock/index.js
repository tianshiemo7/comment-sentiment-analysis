/**
 * mock/index.js — Vite 开发服务器 mock 插件
 *
 * ⚠️ 真实后端（FastAPI :8000）就绪后，删除 vite.config.js 里的一行 `mockApiPlugin(),`
 *    即可完全关闭本 mock，前端业务代码一行都不用改（mock 只依赖 docs/API契约.md 的字段约定）。
 *
 * 挂载方式：configureServer 中「直接」调用 server.middlewares.use(...)，
 * 中间件注册位置早于 Vite 内建中间件（含 server.proxy），因此：
 *   - /api/videos*、/health 被本插件接管；
 *   - 其它路径（如 /api/predict、/api/models）继续 next() → 走 proxy 到 FastAPI。
 * 同源请求，无需 CORS。
 */

import { deleteVideo, getDetail, listVideos, retryVideo, resetStore, submitVideo } from './store.js'

const BV = 'BV[0-9A-Za-z]{10}'
const RE_DETAIL = new RegExp(`^/api/videos/(${BV})/detail$`)
const RE_RETRY = new RegExp(`^/api/videos/(${BV})/retry$`)
const RE_ITEM = new RegExp(`^/api/videos/(${BV})$`)

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

function resolveByResult(res, result) {
  sendJson(res, result.status, result.body)
}

async function handle(req, res) {
  const path = new URL(req.url, 'http://localhost').pathname
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

  if (path === '/api/videos' && method === 'GET') {
    sendJson(res, 200, listVideos())
    return true
  }

  if (path === '/api/videos' && method === 'POST') {
    const body = await readJsonBody(req)
    const result = submitVideo(body && body.bvid)
    if (result.ok) console.log(`[mock] 提交 ${body.bvid} → queued`)
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

  const item = RE_ITEM.exec(path)
  if (item && method === 'DELETE') {
    console.log(`[mock] 删除 ${item[1]}`)
    resolveByResult(res, deleteVideo(item[1]))
    return true
  }

  // 调试用：恢复预置的 2 条演示数据（仅 mock 存在）
  if (path === '/__mock/reset' && method === 'POST') {
    resetStore()
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
