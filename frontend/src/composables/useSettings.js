/**
 * src/composables/useSettings.js — 全站共享的设置状态（单例）
 *
 * 为什么是单例：
 *   - App.vue 顶部要显示账号模式徽标（匿名 / 已登录用户名）；
 *   - BilibiliView 的「爬虫参数」弹窗要用当前采集参数做预填；
 *   - SettingsDialog 保存后，上面两处必须**立刻**同步。
 *   因此设置状态放在模块级（离开组件生命周期），只请求一次 GET /api/settings，多个组件共享同一份 ref。
 *
 * 失败策略（契约 5.2）：GET 失败时回退内置默认值并提示后端未启动，不阻塞界面演示。
 */

import { computed, ref } from 'vue'
import {
  DEFAULT_SETTINGS,
  cloneSettings,
  fetchSettings,
  normalizeSettings,
  saveSettings,
} from '../api/settings.js'

/** 模块级共享状态：整个页面生命周期内只有这一份 */
const settings = ref(cloneSettings(DEFAULT_SETTINGS))
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
/** 同一个 GET 只发一次（App / BilibiliView / 弹窗可能同时触发加载） */
let loadPromise = null

const accountMode = computed(() => settings.value.account.mode)
const isAccountMode = computed(() => accountMode.value === 'account')
const hasCookie = computed(() => Boolean(settings.value.account.has_cookie))
const maskedCookie = computed(() => settings.value.account.masked_cookie || '')
const accountUname = computed(() => settings.value.account.uname || '')
/** 顶栏徽标文案：匿名 / 已登录用户名（未缓存昵称时用「已登录账号」） */
const accountLabel = computed(() => {
  if (!isAccountMode.value) return '匿名'
  return accountUname.value || '已登录账号'
})

/**
 * 加载设置（去重 + 失败回退默认值）。
 * @param {boolean} [force] true 时忽略已加载缓存重新请求（检查登录态后刷新 uname/mid 用）
 */
function load(force = false) {
  if (loadPromise) return loadPromise
  if (loaded.value && !force) return Promise.resolve(settings.value)

  loading.value = true
  error.value = ''
  loadPromise = (async () => {
    try {
      const data = await fetchSettings()
      settings.value = normalizeSettings(data)
      loaded.value = true
      return settings.value
    } catch (err) {
      error.value = err.message
      // 后端未启动：回退内置默认值，界面仍可打开设置弹窗做本地预览
      if (!loaded.value) settings.value = cloneSettings(DEFAULT_SETTINGS)
      loaded.value = true
      return settings.value
    } finally {
      loading.value = false
      loadPromise = null
    }
  })()
  return loadPromise
}

/**
 * PUT /api/settings（部分更新）。成功后用服务端返回的最新设置覆盖共享状态。
 * 失败时把中文错误抛给调用方（弹窗展示）。
 * @param {{account?: object, crawl?: object, pipeline?: object}} patch
 */
async function save(patch) {
  const data = await saveSettings(patch)
  settings.value = normalizeSettings(data)
  loaded.value = true
  error.value = ''
  return settings.value
}

/** 检查登录态成功后，服务端会缓存 uname/mid；重新拉一次设置同步徽标。 */
function refresh() {
  return load(true)
}

function clearError() {
  error.value = ''
}

export function useSettings() {
  return {
    settings,
    loading,
    loaded,
    error,
    accountMode,
    isAccountMode,
    hasCookie,
    maskedCookie,
    accountUname,
    accountLabel,
    load,
    save,
    refresh,
    clearError,
  }
}

export default useSettings
