/**
 * src/api/videos.js — B 站视频评论分析接口封装（新增，与既有 client.js 并列）
 *
 * 契约来源：docs/API契约.md 与
 *   docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md（字段名不得改名）
 *   1. POST   /api/videos                       {"bvid":str,"settings"?:object} -> {"bvid","status","progress","created_at"}
 *                                               settings 省略 = 旧行为（一次跑完四个阶段）
 *   2. GET    /api/videos                       -> {"total":int,"videos":[{bvid,title,owner,cover_url,
 *                                                    status,progress,message,phases,comment_count,
 *                                                    created_at,updated_at}]}
 *   3. GET    /api/videos/{bvid}/detail         -> {"video":{...},"analysis":{...},"sentiment":null|{...},
 *                                                    "trend":[],"keywords":{positive,negative},
 *                                                    "model":null|{...},"top_comments":{positive,negative},
 *                                                    "pipeline"?:{...},"error":null|str}
 *   4. POST   /api/videos/{bvid}/retry          -> 同接口 1
 *   5. POST   /api/videos/{bvid}/continue       -> 同接口 1（仅 crawled / cleaned 可继续，否则 409）
 *   6. DELETE /api/videos/{bvid}                -> {"deleted":true}
 *
 * 全部使用同源相对路径；开发时由 Vite 代理（或 mock 插件）处理。
 * 错误统一抛出带中文 detail 的 Error，并挂上 err.status 供界面区分 400/404/409。
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

/**
 * 1) 提交 BV 号，创建分析任务。
 * @param {string} bvid
 * @param {object|null} [settings] 本次任务的爬虫/流水线参数（与已保存设置部分合并，不含 Cookie）；
 *                                省略即旧行为（后端一次跑完四个阶段，不暂停）
 */
export function submitVideo(bvid, settings = null) {
  const body = { bvid }
  if (settings && typeof settings === 'object') body.settings = settings
  return request('/api/videos', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
}

/** 2) 视频列表（左栏，3 秒轮询） */
export function listVideos() {
  return request('/api/videos')
}

/** 3) 视频详情（右侧详情区） */
export function getVideoDetail(bvid) {
  return request(`/api/videos/${encodeURIComponent(bvid)}/detail`)
}

/** 4) 重试失败任务 */
export function retryVideo(bvid) {
  return request(`/api/videos/${encodeURIComponent(bvid)}/retry`, { method: 'POST' })
}

/**
 * 5) 分步流水线：从「待清洗 / 待分析」继续执行。
 * 其他状态后端返回 409（detail 形如「任务当前状态 queued，无需继续」）。
 */
export function continueVideo(bvid) {
  return request(`/api/videos/${encodeURIComponent(bvid)}/continue`, { method: 'POST' })
}

/** 6) 删除任务 */
export function deleteVideo(bvid) {
  return request(`/api/videos/${encodeURIComponent(bvid)}`, { method: 'DELETE' })
}

export default { submitVideo, listVideos, getVideoDetail, retryVideo, continueVideo, deleteVideo }
