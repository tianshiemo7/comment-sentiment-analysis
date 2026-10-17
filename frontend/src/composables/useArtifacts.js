/**
 * src/composables/useArtifacts.js — 左栏三段的唯一数据源
 *
 * 契约（docs/API契约.md 第四点五节）：三段数据全部来自**一次** `GET /api/artifacts`
 * 的 `groups` 数组（projects / solo / videos），不为每段单独发请求。
 *
 * 轮询沿用 usePolling（3 秒、全部已停时停止、页面隐藏暂停）：
 *   - 终态判断与 utils/videoFormat.js 的 isTerminal 保持一致（completed / failed）；
 *   - 数据项目 status 为 queued / processing 时保持轮询，完成后自动停止；
 *   - 视频段（groups.videos）与 `useVideoAnalysis({ videos })` 共用同一个 ref，
 *     因此视频详情与补拉逻辑完全复用既有实现；
 *   - 视频新增的等待态 crawled / cleaned 视为已停（不自动轮询），
 *     与 useVideoAnalysis 的 isActive 判断保持一致。
 */

import { computed, ref } from 'vue'
import {
  analyzeArtifact,
  deleteArtifact,
  deleteSoloComment,
  deleteSoloComments,
  listArtifacts,
  uploadArtifacts,
} from '../api/artifacts.js'
import { isActive, isTerminal } from '../utils/videoFormat.js'
import { usePolling } from './usePolling.js'

const POLL_INTERVAL = 3000

/** groups[].key → items */
function itemsOf(groups, key) {
  const group = groups.find((item) => item && item.key === key)
  return Array.isArray(group?.items) ? group.items : []
}

export function useArtifacts() {
  const groups = ref([])
  const loading = ref(true)
  const error = ref('')

  const actionError = ref('')
  const uploading = ref(false)

  /** 三段各自的 items（与 groups 同步；videos 的 ref 对象会被交给 useVideoAnalysis） */
  const projects = ref([])
  const solo = ref([])
  const videos = ref([])

  const { isPolling, refreshNow, start } = usePolling(refresh, {
    interval: POLL_INTERVAL,
    shouldContinue: () =>
      projects.value.some((item) => !isTerminal(item.status)) || videos.value.some((item) => isActive(item.status)),
  })

  /** 拉一次三段数据；失败保留上一份，仅提示错误。 */
  async function refresh() {
    try {
      const data = await listArtifacts()
      const list = Array.isArray(data?.groups) ? data.groups : []
      groups.value = list
      projects.value = itemsOf(list, 'projects')
      solo.value = itemsOf(list, 'solo')
      videos.value = itemsOf(list, 'videos')
      error.value = ''
    } catch (err) {
      error.value = err.message
    } finally {
      loading.value = false
    }
  }

  /** 段标题优先用后端 label，缺失时回退到契约默认文案。 */
  const labels = computed(() => ({
    projects: groups.value.find((item) => item?.key === 'projects')?.label || '数据项目',
    solo: groups.value.find((item) => item?.key === 'solo')?.label || '单独评论',
    videos: groups.value.find((item) => item?.key === 'videos')?.label || '视频任务',
  }))

  const activeProjectCount = computed(() => projects.value.filter((item) => !isTerminal(item.status)).length)
  /** 三段里是否有进行中的任务（状态栏/轮询提示用；crawled/cleaned 等待态不算进行中） */
  const hasActiveTask = computed(() => activeProjectCount.value > 0 || videos.value.some((item) => isActive(item.status)))

  function clearActionError() {
    actionError.value = ''
  }

  /**
   * 上传爬取数据（多选文件 + 可选项目名）。
   * @returns {Promise<object|null>} 成功返回新项目对象
   */
  async function upload(files, name = '') {
    uploading.value = true
    actionError.value = ''
    try {
      const project = await uploadArtifacts(files, name)
      await refresh()
      start() // 新项目入队 → 恢复轮询
      return project
    } catch (err) {
      actionError.value = err.message
      return null
    } finally {
      uploading.value = false
    }
  }

  /** 删除数据项目。 */
  async function removeProject(id) {
    actionError.value = ''
    try {
      await deleteArtifact(id)
      await refresh()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    }
  }

  /** 重新处理数据项目。 */
  async function reprocessProject(id) {
    actionError.value = ''
    try {
      const project = await analyzeArtifact(id)
      await refresh()
      start()
      return project
    } catch (err) {
      actionError.value = err.message
      return null
    }
  }

  /** 删除单条评论。 */
  async function removeSolo(id) {
    actionError.value = ''
    try {
      await deleteSoloComment(id)
      await refresh()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    }
  }

  /** 清空全部单独评论。 */
  async function clearSolo() {
    actionError.value = ''
    const ids = solo.value.map((item) => item.id).filter(Boolean)
    if (!ids.length) return true
    try {
      await deleteSoloComments(ids)
      await refresh()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    }
  }

  return {
    groups,
    labels,
    projects,
    solo,
    videos,
    loading,
    error,
    actionError,
    uploading,
    clearActionError,
    isPolling,
    hasActiveTask,
    activeProjectCount,
    refresh,
    refreshNow,
    start,
    upload,
    removeProject,
    reprocessProject,
    removeSolo,
    clearSolo,
  }
}

export default useArtifacts
