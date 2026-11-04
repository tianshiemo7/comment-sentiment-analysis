/**
 * src/utils/exportFiles.js — 导出 / Python 词云图 URL 构造与浏览器下载（纯函数 + 一处 DOM 副作用）
 *
 * 契约见 docs/superpowers/specs/2026-11-04-export-and-wordcloud-design.md 第 3、4 节：
 *   GET /api/artifacts/{id}/download?stage=initial|clean|excluded
 *   GET /api/videos/{bvid}/download?stage=crawl|clean
 *   GET /api/artifacts/{id}/wordcloud-image?tone=positive|negative&v=&download=true
 *   GET /api/videos/{bvid}/wordcloud-image?tone=positive|negative&v=&download=true
 *
 * URL 构造函数返回字符串，供 <a> / <img> 直接使用（不走 fetch、不需要 JSON 解析）；
 * 只有 triggerDownload 负责真实下载：先校验响应（失败抛中文 Error），再创建隐藏
 * <a download> 触发保存，不新开窗口。
 */

/** 数据项目某阶段 CSV 的下载地址（默认清洗结果）。 */
export function artifactDownloadUrl(projectId, stage = 'clean') {
  return `/api/artifacts/${encodeURIComponent(projectId)}/download?stage=${encodeURIComponent(stage)}`
}

/** 视频任务某阶段 CSV 的下载地址（默认采集数据）。 */
export function videoDownloadUrl(bvid, stage = 'crawl') {
  return `/api/videos/${encodeURIComponent(bvid)}/download?stage=${encodeURIComponent(stage)}`
}

/**
 * Python 渲染词云图的地址。
 * @param {{kind?: 'video'|'artifact', id: string, tone?: 'positive'|'negative',
 *          version?: string, download?: boolean}} options
 */
export function wordcloudImageUrl({ kind, id, tone = 'positive', version = '', download = false }) {
  const base =
    kind === 'artifact'
      ? `/api/artifacts/${encodeURIComponent(id)}/wordcloud-image`
      : `/api/videos/${encodeURIComponent(id)}/wordcloud-image`
  const search = new URLSearchParams({ tone })
  if (version) search.set('v', version)
  if (download) search.set('download', 'true')
  return `${base}?${search.toString()}`
}

/**
 * 由关键词内容生成确定性版本号（词数 + 词频总和 + 字数）。
 * 重新分析后关键词变化 → 版本号变化 → 浏览器不会命中旧图；同一份数据始终得到同一版本号。
 */
export function keywordsRevision(words) {
  const list = Array.isArray(words) ? words : []
  const total = list.reduce((sum, item) => sum + (Number(item?.count) || 0), 0)
  const chars = list.reduce((sum, item) => sum + String(item?.word || '').length, 0)
  return `${list.length}-${total}-${chars}`
}

/** 从 Content-Disposition 解析文件名（优先 RFC 5987 的 filename*，回退 filename="..."）。 */
function filenameFromDisposition(header) {
  if (!header) return ''
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''))
    } catch {
      // 非法百分号编码时回退到 ASCII 文件名
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"/i.exec(header) || /filename\s*=\s*([^;]+)/i.exec(header)
  return plain ? plain[1].trim() : ''
}

/** 响应头没有文件名时的兜底名称（形如 video_BV1xx411c7mD_crawl.csv）。 */
function fallbackFilename(url) {
  try {
    const parsed = new URL(url, 'http://localhost')
    const match = /\/api\/(artifacts|videos)\/([^/]+)\//.exec(parsed.pathname)
    const kind = match && match[1] === 'artifacts' ? 'artifact' : 'video'
    const id = match && match[2] ? decodeURIComponent(match[2]) : 'export'
    const stage = parsed.searchParams.get('stage') || ''
    return stage ? `${kind}_${id}_${stage}.csv` : `${kind}_${id}.csv`
  } catch {
    return 'export.csv'
  }
}

/** 读取错误响应里的中文 detail；非 JSON 时截断原文。 */
async function readErrorDetail(response) {
  let text = ''
  try {
    text = await response.text()
  } catch {
    return ''
  }
  if (!text) return ''
  try {
    const data = JSON.parse(text)
    if (data && data.detail !== undefined) {
      return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    }
  } catch {
    // 不是 JSON（例如代理的 HTML 错误页），回退原文
  }
  return text.length > 200 ? `${text.slice(0, 200)}…` : text
}

/**
 * 触发浏览器下载（不新开窗口）。
 *
 * 先 fetch 校验响应：后端不可达 / 404 / 400 等失败情况抛中文 Error，供界面提示；
 * 校验通过后把响应当 Blob，创建隐藏 <a download> 并点击，保证：
 *   - 不新开窗口、不跳转到错误页（后端停掉时页面不会白屏）；
 *   - 文件名优先用后端 Content-Disposition，缺失时按 URL 推导。
 *
 * @param {string} url 由 artifactDownloadUrl / videoDownloadUrl 生成
 * @param {{filename?: string}} [options] 兜底文件名（后端未给 Content-Disposition 时使用）
 * @returns {Promise<string>} 实际使用的下载文件名
 */
export async function triggerDownload(url, { filename = '' } = {}) {
  if (typeof document === 'undefined') throw new Error('当前环境不支持浏览器下载')

  let response
  try {
    response = await fetch(url)
  } catch (err) {
    throw new Error(`无法连接分析服务：${err.message}。请确认后端已启动（python -m uvicorn src.inference.app:app --port 8000）`)
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response)
    throw new Error(detail || `导出失败（HTTP ${response.status}）`)
  }

  let blob = null
  try {
    blob = await response.blob()
  } catch {
    blob = null
  }
  const name = filename || filenameFromDisposition(response.headers.get('content-disposition')) || fallbackFilename(url)

  const link = document.createElement('a')
  link.rel = 'noopener'
  link.download = name

  if (blob && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    const objectUrl = URL.createObjectURL(blob)
    link.href = objectUrl
    document.body.appendChild(link)
    link.click()
    link.remove()
    // 让浏览器先接手 Blob 再回收，避免部分浏览器下载被中断
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  } else {
    // 极端降级（无法生成 Blob URL）：直接指向下载接口，后端以 attachment 返回
    link.href = url
    document.body.appendChild(link)
    link.click()
    link.remove()
  }
  return name
}

export default {
  artifactDownloadUrl,
  videoDownloadUrl,
  wordcloudImageUrl,
  keywordsRevision,
  triggerDownload,
}
