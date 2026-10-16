/**
 * src/composables/usePolling.js — 通用轮询器（契约第三节）
 *
 * 行为：
 *  - 固定间隔轮询（B 站页面用 3 秒）；
 *  - 满足 shouldContinue() 为 false 时自动停止（如所有任务均为 completed/failed）；
 *  - 页面隐藏（visibilitychange）暂停，回到前台立即拉一次并恢复；
 *  - 组件卸载时清理定时器与监听。
 *
 * 注意：无头浏览器/后台标签页里 document.hidden 可能为 true，
 * 因此首次 start() 无论如何都会立即执行一次 poll()，只是不排定时器。
 */

import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * @param {() => Promise<void>} poll 单次轮询动作
 * @param {{interval?: number, shouldContinue?: () => boolean}} [options]
 */
export function usePolling(poll, options = {}) {
  const interval = options.interval ?? 3000
  const shouldContinue = options.shouldContinue

  const isPolling = ref(false)
  const lastPolledAt = ref(null)

  let desired = false // 业务上「需要轮询」（区别于被页面隐藏临时暂停）
  let timer = null
  let busy = false

  function clearTimer() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  async function tick() {
    if (busy) return
    busy = true
    try {
      await poll()
      lastPolledAt.value = new Date()
    } catch (err) {
      // poll 内部应自行捕获并展示错误；这里兜底，避免未处理的 promise rejection
      console.warn('[usePolling] 轮询任务抛出异常：', err?.message || err)
    } finally {
      busy = false
    }
    if (desired && !document.hidden && shouldContinue && !shouldContinue()) stop()
  }

  /** 开始/恢复轮询：立即拉一次，若页面可见再排定时器。 */
  function start() {
    desired = true
    clearTimer()
    isPolling.value = !document.hidden
    tick()
    if (!document.hidden) timer = setInterval(tick, interval)
  }

  /** 停止轮询（业务上不再需要，如全部终态）。 */
  function stop() {
    desired = false
    isPolling.value = false
    clearTimer()
  }

  /** 立即刷新一次（不改变轮询状态）。 */
  function refreshNow() {
    return tick()
  }

  function onVisibilityChange() {
    if (document.hidden) {
      clearTimer()
      isPolling.value = false
    } else if (desired) {
      start() // 回到前台立即拉一次并恢复节奏
    }
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    start()
  })

  onBeforeUnmount(() => {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    stop()
  })

  return { isPolling, lastPolledAt, start, stop, refreshNow }
}

export default usePolling
