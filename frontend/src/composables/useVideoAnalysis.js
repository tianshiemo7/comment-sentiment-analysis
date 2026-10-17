/**
 * src/composables/useVideoAnalysis.js — B 站分析页的核心状态（列表 + 详情 + 操作）
 *
 * 轮询策略按 docs/API契约.md 第三节与 2026-10-17 分步流水线设计：
 *  - 列表每 3 秒 GET /api/videos；
 *  - 全部任务为「非进行中」时停止：completed / failed 是终态，
 *    crawled（待清洗）/ cleaned（待分析）是**等待操作态**，同样不自动轮询；
 *  - 提交（submit）/ 重试（retry）/ 继续（continueTask）后恢复轮询；
 *  - 详情在「被选中且详情未落到当前快照」时随轮询一起刷新。
 *
 *  ⚠️ 列表数据源可注入：B 站分析页左栏改成三段后，三段共用一次 GET /api/artifacts
 *  （useArtifacts），此时通过 `useVideoAnalysis({ videos })` 传入同一个 ref，
 *  本组合函数只保留「详情 + 操作 + 详情补拉」，不再自己轮询 /api/videos。
 *  不传参数时行为与改造前完全一致（列表 3 秒轮询 /api/videos）。
 *
 *  ⚠️ 详情刷新与列表轮询解耦（修复「完成瞬间详情卡在处理中」）：
 *  列表轮询的判断依据是「还有没有非终态任务」，而任务从非终态跃迁到终态的那一轮，
 *  列表已经显示 completed，若详情刷新仍以「列表项未完成」为前提，这一次刷新就会被跳过，
 *  而同一轮结束后列表轮询随即停止 —— 详情将永远停在上一次（未完成）的快照。
 *  因此这里做两层保证：
 *   1) 只要选中项的详情还没有落在终态数据上（detail.analysis.status 非 completed/failed），
 *      每一轮轮询都补拉详情，即使列表项刚变成 completed；
 *   2) 列表轮询停止后，若详情仍未到终态，则启动一个受限的「详情补拉」定时器，
 *      直到详情到终态（或达到尝试上限 / 选中项变化 / 任务消失）才停。
 */

import { computed, onBeforeUnmount, ref } from 'vue'
import {
  continueVideo,
  deleteVideo,
  getVideoDetail,
  listVideos,
  retryVideo,
  submitVideo,
} from '../api/videos.js'
import { isActive, isSettled } from '../utils/videoFormat.js'
import { usePolling } from './usePolling.js'

const POLL_INTERVAL = 3000
/** 详情补拉：列表轮询已停止但详情仍未到终态时的兜底节奏与上限（约 1 分钟） */
const DETAIL_CATCHUP_INTERVAL = 1500
const DETAIL_CATCHUP_MAX_ATTEMPTS = 40

/**
 * @param {{videos?: import('vue').Ref<Array>}} [options]
 *   `videos`：可选。传入外部 ref 时，列表轮询改由持有该 ref 的一方（B 站分析页的
 *   useArtifacts，单一 GET /api/artifacts 请求）负责，本组合函数不再自己请求 /api/videos，
 *   避免两个定时器互相覆盖同一份列表；详情补拉逻辑完全保留。
 *   不传该参数时行为与改造前 100% 一致（列表仍由本组合函数轮询 GET /api/videos）。
 */
export function useVideoAnalysis(options = {}) {
  const externalVideos = options.videos || null
  const videos = externalVideos || ref([])
  const listLoading = ref(true)
  const listError = ref('')

  const submitting = ref(false)
  /** continue（待清洗 → 开始清洗 / 待分析 → 开始分析）请求进行中 */
  const continuing = ref(false)
  const actionError = ref('')

  const selectedBvid = ref('')
  const detail = ref(null)
  const detailLoading = ref(false)
  const detailError = ref('')

  const selectedVideo = computed(() => videos.value.find((item) => item.bvid === selectedBvid.value) || null)
  const selectedStatus = computed(() => selectedVideo.value?.status || detail.value?.analysis?.status || '')
  /**
   * 详情是否已经拿到当前状态的数据 —— 决定是否还需要补拉详情。
   * 用 isSettled 而不是 isTerminal：crawled / cleaned 是等待态，
   * 详情落到这两个状态后就不必再追（否则会因「非终态」而无限补拉）。
   */
  const detailSettled = computed(() => Boolean(detail.value) && isSettled(detail.value?.analysis?.status))

  const { isPolling, lastPolledAt, start, stop, refreshNow } = usePolling(pollTick, {
    interval: POLL_INTERVAL,
    // 只要有任意一个任务处于进行中（queued/crawling/processing）就继续轮询；
    // 终态与等待态（crawled/cleaned）都视为已停。
    shouldContinue: () => videos.value.some((item) => isActive(item.status)),
  })

  /** 拉列表；失败时保留上一份数据，仅设置错误提示。 */
  async function refreshList() {
    if (externalVideos) {
      // 列表由外部唯一数据源（GET /api/artifacts 的 groups.videos）提供：
      // 这里不再发请求，只保留调用点语义，避免与外部轮询互相覆盖。
      listLoading.value = false
      return
    }
    try {
      const data = await listVideos()
      videos.value = Array.isArray(data?.videos) ? data.videos : []
      listError.value = ''
    } catch (err) {
      listError.value = err.message
    } finally {
      listLoading.value = false
    }
  }

  /**
   * 拉详情。
   * @param {string} bvid
   * @param {{silent?: boolean}} [opts] silent=true 时不显示 loading（轮询刷新用）
   */
  async function loadDetail(bvid = selectedBvid.value, opts = {}) {
    if (!bvid) return
    if (!opts.silent) detailLoading.value = true
    try {
      const data = await getVideoDetail(bvid)
      if (selectedBvid.value !== bvid) return // 期间已切换选中项，丢弃过期响应
      detail.value = data
      detailError.value = ''
    } catch (err) {
      if (selectedBvid.value === bvid) detailError.value = err.message
      if (err?.status === 404) stopDetailCatchUp() // 任务已不存在，不必反复补拉
    } finally {
      if (!opts.silent) detailLoading.value = false
    }
  }

  // ------------------------------------------------------------------
  // 详情补拉（列表轮询停止后仍要把详情追到终态）
  // ------------------------------------------------------------------

  let catchUpTimer = null
  let catchUpAttempts = 0

  function stopDetailCatchUp() {
    if (catchUpTimer !== null) {
      clearInterval(catchUpTimer)
      catchUpTimer = null
    }
    catchUpAttempts = 0
  }

  /** 按需启动/停止详情补拉：仅当「列表全部已停 + 选中项存在 + 详情未同步到当前状态」时才运行。 */
  function ensureDetailCatchUp() {
    const listSettled = !videos.value.some((item) => isActive(item.status))
    const current = videos.value.find((item) => item.bvid === selectedBvid.value)
    const needsDetail = Boolean(current) && !detailSettled.value

    if (!listSettled || !needsDetail) {
      stopDetailCatchUp()
      return
    }
    if (catchUpTimer !== null) return

    catchUpTimer = setInterval(async () => {
      const target = selectedBvid.value
      const target2 = videos.value.find((item) => item.bvid === target)
      if (!target || !target2 || detailSettled.value || catchUpAttempts >= DETAIL_CATCHUP_MAX_ATTEMPTS) {
        stopDetailCatchUp()
        return
      }
      catchUpAttempts += 1
      await loadDetail(target, { silent: true })
      if (detailSettled.value) stopDetailCatchUp()
    }, DETAIL_CATCHUP_INTERVAL)
  }

  /**
   * 每次轮询：刷新列表；随后刷新详情。
   * 详情刷新条件 = 选中项存在 且（列表项仍在进行中 **或** 详情尚未同步到当前状态）。
   * 后半句覆盖了「进行中 → 等待态 / 终态」的跃迁，避免详情卡在上一个快照。
   */
  async function pollTick() {
    await refreshList()
    const current = videos.value.find((item) => item.bvid === selectedBvid.value)
    if (current && (isActive(current.status) || !detailSettled.value)) {
      await loadDetail(current.bvid, { silent: true })
    }
    ensureDetailCatchUp()
  }

  /** 选中某个视频（切换时先清空旧详情，避免闪现上一条的图表）。 */
  function select(bvid) {
    const changed = selectedBvid.value !== bvid
    if (changed) {
      selectedBvid.value = bvid
      detail.value = null
      detailError.value = ''
    }
    // 同一项再次点击、且详情还没到终态数据时也重新拉一次（例如首次加载失败）
    if (changed || !detailSettled.value) {
      loadDetail(bvid).then(ensureDetailCatchUp)
    }
  }

  /** 手动重新加载选中项的详情（详情区「重新加载」按钮）。 */
  function reloadDetail() {
    const bvid = selectedBvid.value
    if (!bvid) return Promise.resolve()
    detail.value = null
    detailError.value = ''
    return loadDetail(bvid).then(ensureDetailCatchUp)
  }

  /**
   * 提交 BV 号；成功返回 true。
   * @param {string} bvid
   * @param {object|null} [settings] 本次任务的爬虫/流水线参数（爬虫参数弹窗确认后传入）
   */
  async function submit(bvid, settings = null) {
    submitting.value = true
    actionError.value = ''
    try {
      const created = await submitVideo(bvid, settings)
      const target = created?.bvid || bvid
      await refreshList()
      selectedBvid.value = target
      detail.value = null
      detailError.value = ''
      await loadDetail(target, { silent: true })
      ensureDetailCatchUp()
      start() // 新任务入队 → 恢复轮询
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    } finally {
      submitting.value = false
    }
  }

  /** 重试失败任务。 */
  async function retry(bvid) {
    actionError.value = ''
    try {
      await retryVideo(bvid)
      await refreshList()
      if (selectedBvid.value === bvid) {
        // 状态已重置为 queued：清掉旧的终态详情，避免继续显示上一次的图表
        detail.value = null
        detailError.value = ''
        await loadDetail(bvid, { silent: true })
      }
      ensureDetailCatchUp()
      start()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    }
  }

  /**
   * 分步流水线：从「待清洗 / 待分析」继续（POST /api/videos/{bvid}/continue）。
   * 成功后任务回到 queued，因此要清掉旧快照、重新拉详情并恢复轮询。
   * 非等待态（例如别处刚点过）后端返回 409，错误提示原样展示给用户。
   */
  async function continueTask(bvid) {
    continuing.value = true
    actionError.value = ''
    try {
      await continueVideo(bvid)
      await refreshList()
      if (selectedBvid.value === bvid) {
        detail.value = null
        detailError.value = ''
        await loadDetail(bvid, { silent: true })
      }
      ensureDetailCatchUp()
      start()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    } finally {
      continuing.value = false
    }
  }

  /** 删除任务（可选功能）。 */
  async function remove(bvid) {
    actionError.value = ''
    try {
      await deleteVideo(bvid)
      if (selectedBvid.value === bvid) {
        selectedBvid.value = ''
        detail.value = null
        detailError.value = ''
      }
      await refreshList()
      ensureDetailCatchUp()
      return true
    } catch (err) {
      actionError.value = err.message
      return false
    }
  }

  function clearActionError() {
    actionError.value = ''
  }

  onBeforeUnmount(stopDetailCatchUp)

  return {
    videos,
    listLoading,
    listError,
    submitting,
    continuing,
    actionError,
    clearActionError,
    selectedBvid,
    selectedVideo,
    selectedStatus,
    detail,
    detailLoading,
    detailError,
    detailSettled,
    isPolling,
    lastPolledAt,
    select,
    reloadDetail,
    submit,
    retry,
    continueTask,
    remove,
    refreshNow,
    start,
    stop,
  }
}

export default useVideoAnalysis
