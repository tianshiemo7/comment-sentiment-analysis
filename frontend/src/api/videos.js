/**
 * src/api/videos.js — B 站视频评论分析接口封装（新增，与既有 client.js 并列）
 *
 * 契约来源：docs/API契约.md（唯一契约来源，字段名不得改名）
 *   1. POST   /api/videos                       {"bvid":str} -> {"bvid","status","progress","created_at"}
 *   2. GET    /api/videos                       -> {"total":int,"videos":[{bvid,title,owner,cover_url,
 *                                                    status,progress,message,phases,comment_count,
 *                                                    created_at,updated_at}]}
 *   3. GET    /api/videos/{bvid}/detail         -> {"video":{...},"analysis":{...},"sentiment":null|{...},
 *                                                    "trend":[],"keywords":{positive,negative},
 *                                                    "model":null|{...},"top_comments":{positive,negative},"error":null|str}
 *   4. POST   /api/videos/{bvid}/retry          -> 同接口 1
 *   5. DELETE /api/videos/{bvid}                -> {"deleted":true}
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

/** 1) 提交 BV 号，创建分析任务 */
export function submitVideo(bvid) {
  return request('/api/videos', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ bvid }),
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

/** 5) 删除任务 */
export function deleteVideo(bvid) {
  return request(`/api/videos/${encodeURIComponent(bvid)}`, { method: 'DELETE' })
}

export default { submitVideo, listVideos, getVideoDetail, retryVideo, deleteVideo }
