/**
 * src/composables/useArtifactDetail.js — 「数据项目详情」的数据加载状态
 *
 * 契约（docs/API契约.md 4.5.1）：
 *   GET /api/artifacts/{id}/table?stage=&offset=&limit=
 *   GET /api/artifacts/{id}/comments?offset=&limit=&label=
 *   GET /api/artifacts/{id}/analysis
 *   GET /api/artifacts/{id}/path?stage=   （只读取路径，用于「复制文件地址」）
 *   POST /api/artifacts/{id}/open         （用系统默认程序打开，用于「打开 CSV」）
 *
 * 约定：
 *   - 折叠态也先加载「逐条评论」的第一页（展开后立即可见，再翻页/筛选）；
 *   - 项目进入 completed 后自动加载三张表 + 评论首页 + 聚合分析；重新处理（回到 processing）后再次完成会重新加载；
 *   - 列名完全按后端返回的 columns 动态渲染，不写死任何列。
 */

import { computed, ref, watch } from 'vue'
import { getArtifactAnalysis, getArtifactComments, getArtifactPath, getArtifactTable, openArtifact } from '../api/artifacts.js'

/** 三张表的阶段顺序（label 用后端的 stages 覆盖，回退到这里） */
export const TABLE_STAGES = [
  { key: 'initial', label: '初始数据' },
  { key: 'clean', label: '筛选后结果' },
  { key: 'excluded', label: '排除记录' },
]

export const DEFAULT_TABLE_LIMIT = 100
export const DEFAULT_COMMENTS_LIMIT = 50

/** 复制到剪贴板（无权限 / 无 navigator.clipboard 时降级到 execCommand）。 */
async function copyText(text) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 无头浏览器、非安全上下文或用户拒绝授权时 writeText 会 reject → 走降级路径
    }
  }
  if (typeof document === 'undefined') return false
  const el = document.createElement('textarea')
  el.value = text
  el.setAttribute('readonly', 'readonly')
  el.style.position = 'fixed'
  el.style.opacity = '0'
  document.body.appendChild(el)
  el.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(el)
  return ok
}

/**
 * @param {import('vue').Ref<string>} projectId 选中的项目 id
 * @param {import('vue').Ref<string>} projectStatus 选中项目的 status（completed 时才拉表/分析）
 */
export function useArtifactDetail(projectId, projectStatus) {
  const loadedFor = ref('')

  // ---- 三张表 ----
  const stage = ref('initial')
  const tableLimit = ref(DEFAULT_TABLE_LIMIT)
  const tableOffset = ref(0)
  const columns = ref([])
  const rows = ref([])
  const totalRows = ref(0)
  const tableLoading = ref(false)
  const tableError = ref('')

  // ---- 逐条评论 ----
  const commentsOpen = ref(false)
  const commentsLabel = ref('')
  const commentsLimit = ref(DEFAULT_COMMENTS_LIMIT)
  const commentsOffset = ref(0)
  const commentsTotal = ref(0)
  const comments = ref([])
  const commentsLoading = ref(false)
  const commentsError = ref('')

  // ---- 聚合分析 ----
  const analysis = ref(null)
  const analysisLoading = ref(false)
  const analysisError = ref('')

  // ---- 底部操作 ----
  const opening = ref(false)
  const copying = ref(false)
  const actionMessage = ref('')
  const actionError = ref('')

  const tablePages = computed(() => Math.max(1, Math.ceil(totalRows.value / tableLimit.value)))
  const tablePage = computed(() => Math.floor(tableOffset.value / tableLimit.value) + 1)
  const commentsPages = computed(() => Math.max(1, Math.ceil(commentsTotal.value / commentsLimit.value)))
  const commentsPage = computed(() => Math.floor(commentsOffset.value / commentsLimit.value) + 1)

  const sentimentForChart = computed(() => {
    const data = analysis.value
    if (!data || !data.sentiment) return null
    return { total: data.total, ...data.sentiment }
  })

  function clearActionMessage() {
    actionMessage.value = ''
    actionError.value = ''
  }

  function reset() {
    stage.value = 'initial'
    tableOffset.value = 0
    columns.value = []
    rows.value = []
    totalRows.value = 0
    tableError.value = ''
    commentsOpen.value = false
    commentsLabel.value = ''
    commentsOffset.value = 0
    commentsTotal.value = 0
    comments.value = []
    commentsError.value = ''
    analysis.value = null
    analysisError.value = ''
    openPath.value = ''
    clearActionMessage()
  }

  async function loadTable() {
    const id = projectId.value
    if (!id) return
    tableLoading.value = true
    try {
      const data = await getArtifactTable(id, { stage: stage.value, offset: tableOffset.value, limit: tableLimit.value })
      if (projectId.value !== id) return
      columns.value = Array.isArray(data?.columns) ? data.columns : []
      rows.value = Array.isArray(data?.rows) ? data.rows : []
      totalRows.value = Number(data?.total_rows) || 0
      tableError.value = ''
    } catch (err) {
      if (projectId.value === id) tableError.value = err.message
    } finally {
      if (projectId.value === id) tableLoading.value = false
    }
  }

  async function loadComments() {
    const id = projectId.value
    if (!id) return
    commentsLoading.value = true
    try {
      const data = await getArtifactComments(id, {
        offset: commentsOffset.value,
        limit: commentsLimit.value,
        label: commentsLabel.value,
      })
      if (projectId.value !== id) return
      comments.value = Array.isArray(data?.items) ? data.items : []
      commentsTotal.value = Number(data?.total) || 0
      commentsError.value = ''
    } catch (err) {
      if (projectId.value === id) commentsError.value = err.message
    } finally {
      if (projectId.value === id) commentsLoading.value = false
    }
  }

  async function loadAnalysis() {
    const id = projectId.value
    if (!id) return
    analysisLoading.value = true
    try {
      const data = await getArtifactAnalysis(id)
      if (projectId.value !== id) return
      analysis.value = data || null
      analysisError.value = ''
    } catch (err) {
      if (projectId.value === id) analysisError.value = err.message
    } finally {
      if (projectId.value === id) analysisLoading.value = false
    }
  }

  /** 拉三张表 + 评论首页（折叠态也拉）+ 聚合分析。 */
  function loadAll() {
    tableOffset.value = 0
    commentsOffset.value = 0
    loadTable()
    loadComments()
    loadAnalysis()
  }

  function reload() {
    loadedFor.value = projectId.value
    loadAll()
  }

  // 切换项目：清空上一项的所有数据，避免闪现
  watch(projectId, () => {
    reset()
  })

  // 项目完成（或重新处理完成后）才加载表格与聚合图表
  watch(
    [projectId, projectStatus],
    ([id, status]) => {
      if (!id) return
      if (status !== 'completed') {
        loadedFor.value = '' // 重新处理时清标记，下次完成再拉一次
        return
      }
      if (loadedFor.value === id) return
      loadedFor.value = id
      loadAll()
    },
    { immediate: true },
  )

  // ---- 分页 / 切换 ----

  function switchStage(key) {
    if (stage.value === key) return
    stage.value = key
    tableOffset.value = 0
    loadTable()
  }

  function pageTo(page) {
    const target = Math.min(tablePages.value, Math.max(1, Number(page) || 1))
    const offset = (target - 1) * tableLimit.value
    if (offset === tableOffset.value) return
    tableOffset.value = offset
    loadTable()
  }

  function prevPage() {
    pageTo(tablePage.value - 1)
  }

  function nextPage() {
    pageTo(tablePage.value + 1)
  }

  function setTableLimit(limit) {
    const value = Number(limit) || DEFAULT_TABLE_LIMIT
    if (value === tableLimit.value) return
    tableLimit.value = value
    tableOffset.value = 0
    loadTable()
  }

  /** 展开/收起逐条评论；展开时若首页未加载则补拉。 */
  function toggleComments() {
    commentsOpen.value = !commentsOpen.value
    if (commentsOpen.value && !comments.value.length && !commentsLoading.value) loadComments()
  }

  function setCommentsLabel(label) {
    if (commentsLabel.value === label) return
    commentsLabel.value = label
    commentsOffset.value = 0
    loadComments()
  }

  function commentsPageTo(page) {
    const target = Math.min(commentsPages.value, Math.max(1, Number(page) || 1))
    const offset = (target - 1) * commentsLimit.value
    if (offset === commentsOffset.value) return
    commentsOffset.value = offset
    loadComments()
  }

  function commentsPrev() {
    commentsPageTo(commentsPage.value - 1)
  }

  function commentsNext() {
    commentsPageTo(commentsPage.value + 1)
  }

  // ---- 底部三个操作 ----

  /** 打开 CSV（服务端本机用默认程序打开）。 */
  async function openCsv(targetStage = stage.value) {
    const id = projectId.value
    if (!id) return
    opening.value = true
    clearActionMessage()
    try {
      await openArtifact(id, targetStage)
      actionMessage.value = '已在系统默认程序中打开'
      return data
    } catch (err) {
      actionError.value = err.message
      return null
    } finally {
      opening.value = false
    }
  }

  /**
   * 复制文件地址：只调**只读**的 `GET /api/artifacts/{id}/path?stage=`，
   * 绝不调用 `/open`（过去为了拿 path 会顺带弹一次 Excel，现已修正）。
   * 后端返回 `exists:false`（该阶段文件尚未生成）时给出提示，不复制不存在的路径。
   */
  async function copyFileAddress(targetStage = stage.value) {
    const id = projectId.value
    if (!id) return
    copying.value = true
    clearActionMessage()
    try {
      const data = await getArtifactPath(id, targetStage)
      const path = data?.path || ''
      if (data?.exists === false) {
        actionError.value = `该阶段文件不存在（${targetStage}.csv 尚未生成）`
        return ''
      }
      if (!path) throw new Error('后端未返回文件地址')
      const ok = await copyText(path)
      actionMessage.value = ok ? `已复制文件地址：${path}` : `文件地址：${path}（浏览器未授权剪贴板，请手动复制）`
      return path
    } catch (err) {
      actionError.value = err.message
      return ''
    } finally {
      copying.value = false
    }
  }

  return {
    // 三张表
    stage,
    tableLimit,
    tableOffset,
    columns,
    rows,
    totalRows,
    tableLoading,
    tableError,
    tablePage,
    tablePages,
    // 逐条评论
    commentsOpen,
    commentsLabel,
    commentsLimit,
    commentsOffset,
    commentsTotal,
    comments,
    commentsLoading,
    commentsError,
    commentsPage,
    commentsPages,
    // 聚合分析
    analysis,
    analysisLoading,
    analysisError,
    sentimentForChart,
    // 底部操作
    opening,
    copying,
    actionMessage,
    actionError,
    clearActionMessage,
    // 动作
    loadTable,
    loadComments,
    loadAnalysis,
    loadAll,
    reload,
    switchStage,
    pageTo,
    prevPage,
    nextPage,
    setTableLimit,
    toggleComments,
    setCommentsLabel,
    commentsPageTo,
    commentsPrev,
    commentsNext,
    openCsv,
    copyFileAddress,
  }
}

export default useArtifactDetail
