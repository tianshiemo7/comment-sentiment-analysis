/**
 * src/api/client.js — 前后端接口契约封装（唯一依赖的接口契约）
 *
 * 契约已由项目 README「前后端接口契约」一节锁定，前后端均不得改名：
 *   1. GET  /health              -> {"status":"ok","service":str,"version":str}
 *   2. POST /api/predict         {"text":str}
 *                               -> {"text":str,"label":"positive"|"negative",
 *                                   "confidence":float,"probs":{"positive":float,"negative":float}}
 *   3. POST /api/predict/batch   multipart 文件（.csv/.xlsx）
 *                               -> {"total":int,"results":[{"index":int,"text":str,"label":str,"confidence":float}],
 *                                   "export_url":str}
 *                               说明：后端把批量结果落盘到 outputs/predictions/ 并返回可下载 URL（总是非 null）；
 *                               下载走新增路由 GET /api/predict/batch/download/{file_id}
 *                               （返回 text/csv 附件，Excel 可直接打开，带 UTF-8 BOM；非法文件名 400、不存在 404）。
 *   4. GET  /api/models          -> {"models":[{"name":str,"type":str,
 *                                   "metrics":{"accuracy":float,"precision":float,"recall":float,"f1":float},
 *                                   "ready":bool}]}
 *
 * 所有请求使用同源相对路径，由 Vite 代理（vite.config.js）转发到 http://localhost:8000。
 * 骨架阶段若后端未启动，函数会抛出带中文说明的 Error，供界面展示「待接入后端」提示。
 */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

const BACKEND_HINT = '请确认 FastAPI 服务已启动：python -m uvicorn src.inference.app:app --port 8000'

/**
 * 统一请求封装：解析 JSON、非 2xx 抛错、网络异常给出可读提示。
 * @param {string} path 同源相对路径
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(path, options)
  } catch (err) {
    throw new Error(`无法连接后端服务 ${path}：${err.message}。${BACKEND_HINT}`)
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
    const suffix = detail
      ? `：${typeof detail === 'string' ? detail : JSON.stringify(detail)}`
      : ''
    throw new Error(`请求 ${path} 失败（HTTP ${res.status}）${suffix}`)
  }

  return data
}

/** 1) 健康检查 */
export function getHealth() {
  return request('/health')
}

/**
 * 2) 单条评论情感预测
 * @param {string} text 评论文本
 */
export function predict(text) {
  return request('/api/predict', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ text }),
  })
}

/**
 * 3) 批量预测（multipart 上传 .csv / .xlsx）
 * 注意：不要手动设置 Content-Type，浏览器需自动补 multipart boundary。
 * @param {File} file
 * @param {string} [fieldName] 表单字段名，后端默认按 "file" 读取
 */
export function predictBatch(file, fieldName = 'file') {
  const form = new FormData()
  form.append(fieldName, file, file.name)
  return request('/api/predict/batch', {
    method: 'POST',
    body: form,
  })
}

/** 4) 已训练模型列表与指标 */
export function getModels() {
  return request('/api/models')
}

/** 读取所选模型的真实实验图表与评估信息。 */
export function getVisualizations(model = 'textcnn') {
  return request(`/api/visualizations?model=${encodeURIComponent(model)}`)
}

/** 契约速查表（供界面/文档渲染，字段与 README 保持一致） */
export const API_CONTRACT = [
  { method: 'GET', path: '/health', request: '—', response: '{"status":"ok","service":str,"version":str}' },
  { method: 'POST', path: '/api/predict', request: '{"text":str}', response: '{"text","label","confidence","probs":{"positive","negative"}}' },
  { method: 'POST', path: '/api/predict/batch', request: 'multipart 文件（.csv/.xlsx）', response: '{"total","results":[{"index","text","label","confidence"}],"export_url":str}' },
  { method: 'GET', path: '/api/models', request: '—', response: '{"models":[{"name","type","metrics":{...},"ready"}]}' },
  { method: 'GET', path: '/api/visualizations', request: '?model=textcnn|bilstm_attention', response: '{"model","model_label","dataset_version","best_epoch","metrics","figures"}' },
  { method: 'GET', path: '/api/visualizations/figures/{figure_id}', request: '?download=true（可选）', response: 'image/png（固定实验图表）' },
  { method: 'GET', path: '/api/predict/batch/download/{file_id}', request: '—', response: 'text/csv 附件（批量结果导出）' },
]

export default { getHealth, predict, predictBatch, getModels, getVisualizations, API_CONTRACT }
