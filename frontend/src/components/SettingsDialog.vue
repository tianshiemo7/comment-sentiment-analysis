<script setup>
/**
 * components/SettingsDialog.vue — 「系统设置」弹窗（账号 / 采集参数 / 自动流程）
 *
 * 契约：docs/superpowers/specs/2026-10-17-settings-and-staged-pipeline-design.md
 *   - 初始加载 GET /api/settings，保存 PUT /api/settings（部分更新），失败提示中文；
 *   - Cookie 明文**只**由用户输入并直接提交给服务端，界面与共享状态只展示 masked_cookie；
 *   - 「检查登录状态」调用 POST /api/crawler/session/check（有临时输入的 Cookie 时优先用它）；
 *   - 「退出登录 / 清除凭据」立即以 mode=anonymous + cookie="" 保存，清空服务端凭据；
 *   - 「恢复默认」只回填表单，点击「保存」后生效。
 *
 * 组件只负责表单与请求编排，共享设置状态由 composables/useSettings.js 维护
 * （保存成功后 App.vue 的账号徽标、爬虫参数弹窗的预填值会同步更新）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import { useSettings } from '../composables/useSettings.js'
import {
  PLAYWRIGHT_INSTALL_HINT,
  cancelBrowserLogin,
  getBrowserLoginStatus,
  startBrowserLogin,
} from '../api/account.js'
import {
  CRAWL_LIMITS,
  DEFAULT_SETTINGS,
  checkLoginStatus,
  cloneSettings,
} from '../api/settings.js'

const emit = defineEmits(['close', 'saved'])

const { settings, loaded, error: loadError, load, save, refresh } = useSettings()

/** 人工登录窗口轮询间隔（契约 5.9：2 秒一次） */
const LOGIN_POLL_INTERVAL = 2000

/** 表单在「服务端设置」的副本上编辑，取消不会影响全局共享状态 */
const form = ref(cloneSettings(settings.value))
const cookieDraft = ref('')
const formLoading = ref(!loaded.value)
const saving = ref(false)
const checking = ref(false)
const errors = ref({})
const saveError = ref('')
const notice = ref('')
const sessionError = ref('')
/** 「高级：手动粘贴 Cookie」折叠项：保存校验失败时自动展开，否则用户看不到错误原因 */
const advancedOpen = ref(false)
/** 最近一次「检查登录状态」（手填 Cookie）的结果（仅展示，不写入共享状态） */
const session = ref(null)

/** 人工登录窗口状态机（POST start / GET status 轮询 / POST cancel） */
const loginState = ref(null)
const loginStarting = ref(false)
const loginCancelling = ref(false)
const loginError = ref('')
/** 503（Playwright 依赖缺失）安装指引 */
const loginInstallHint = ref('')
let loginTimer = null

/** 服务端账号只读信息（masked_cookie / uname / has_cookie） */
const accountState = computed(() => settings.value.account)
const hasSavedCookie = computed(() => Boolean(accountState.value.has_cookie))
const maskedCookie = computed(() => accountState.value.masked_cookie || '')
const savedUname = computed(() => accountState.value.uname || '')
const formMode = computed(() => form.value.account.mode)
const isAccountMode = computed(() => formMode.value === 'account')
const accountLabel = computed(() => {
  if (accountState.value.mode !== 'account') return '匿名'
  return savedUname.value || '已登录账号'
})

/** 人工登录窗口：opening / waiting 期间前端每 2 秒轮询一次 */
const loginRunning = computed(() => ['opening', 'waiting'].includes(loginState.value?.state || ''))
const loginSucceeded = computed(() => loginState.value?.state === 'success')
const loginTerminalMessage = computed(() => {
  const state = loginState.value?.state
  if (!state || ['idle', 'opening', 'waiting', 'success'].includes(state)) return ''
  return loginState.value?.message || '登录未完成'
})
/** 等待登录窗口期间禁用重复点击（契约 5.9） */
const browserLoginBusy = computed(() => loginStarting.value || loginRunning.value || loginCancelling.value)

function rangeText(limit) {
  return limit.max === null ? `需 ≥ ${limit.min}` : `${limit.min} ~ ${limit.max}`
}

function validate() {
  const next = {}
  const values = form.value

  for (const key of ['max_pages', 'max_sub_pages', 'page_size', 'max_items']) {
    const limit = CRAWL_LIMITS[key]
    const raw = values.crawl[key]
    const value = Number(raw)
    if (raw === '' || raw === null || !Number.isInteger(value) || value < limit.min || (limit.max !== null && value > limit.max)) {
      next[key] = `${limit.label}需为 ${rangeText(limit)} 的整数`
    }
  }

  const delayRaw = values.crawl.delay_seconds
  const delay = Number(delayRaw)
  const delayLimit = CRAWL_LIMITS.delay_seconds
  if (delayRaw === '' || delayRaw === null || !Number.isFinite(delay) || delay < delayLimit.min || delay > delayLimit.max) {
    next.delay_seconds = `${delayLimit.label}需在 ${delayLimit.min} ~ ${delayLimit.max} 秒之间`
  }

  const timeoutRaw = values.crawl.timeout_seconds
  const timeout = Number(timeoutRaw)
  const timeoutLimit = CRAWL_LIMITS.timeout_seconds
  // 契约：timeout_seconds 只要 > 0（允许 0.5 这类小数），前端限制 0.1 ~ 60
  if (timeoutRaw === '' || timeoutRaw === null || !Number.isFinite(timeout) || timeout < timeoutLimit.min || timeout > timeoutLimit.max) {
    next.timeout_seconds = `${timeoutLimit.label}需在 ${timeoutLimit.min} ~ ${timeoutLimit.max} 秒之间`
  }

  if (isAccountMode.value && !hasSavedCookie.value && !cookieDraft.value.trim()) {
    next.cookie = '选择「登录账号」需要填写 Cookie，或先点「检查登录状态」确认凭据可用'
    advancedOpen.value = true // 展开兜底输入区，确保用户能看到错误提示
  }

  errors.value = next
  return Object.keys(next).length === 0
}

function buildPayload() {
  const values = form.value
  const payload = {
    account: { mode: values.account.mode },
    crawl: {
      real_crawl: Boolean(values.crawl.real_crawl),
      max_pages: Number(values.crawl.max_pages),
      max_sub_pages: Number(values.crawl.max_sub_pages),
      page_size: Number(values.crawl.page_size),
      include_replies: Boolean(values.crawl.include_replies),
      sort_mode: values.crawl.sort_mode === 'hot' ? 'hot' : 'time',
      max_items: Number(values.crawl.max_items),
      delay_seconds: Number(values.crawl.delay_seconds),
      timeout_seconds: Number(values.crawl.timeout_seconds),
      require_login: Boolean(values.crawl.require_login),
      init_buvid: Boolean(values.crawl.init_buvid),
    },
    pipeline: {
      auto_clean: Boolean(values.pipeline.auto_clean),
      auto_analyze: Boolean(values.pipeline.auto_analyze),
    },
  }
  const cookie = cookieDraft.value.trim()
  if (cookie) payload.account.cookie = cookie
  return payload
}

async function onSave() {
  if (saving.value) return
  saveError.value = ''
  notice.value = ''
  sessionError.value = ''
  if (!validate()) return
  saving.value = true
  try {
    const updated = await save(buildPayload())
    form.value = cloneSettings(updated)
    cookieDraft.value = ''
    notice.value = '设置已保存'
    emit('saved', updated)
  } catch (err) {
    saveError.value = err.message
  } finally {
    saving.value = false
  }
}

async function onCheckLogin() {
  if (checking.value) return
  checking.value = true
  session.value = null
  sessionError.value = ''
  notice.value = ''
  saveError.value = ''
  try {
    const draft = cookieDraft.value.trim()
    const result = await checkLoginStatus(draft || undefined)
    session.value = result
    if (result.is_login) {
      form.value.account.mode = 'account'
      notice.value = `登录成功：${result.uname || '已登录'}。点击「保存」启用登录账号模式。`
      // 登录成功后服务端已缓存 uname / mid：刷新共享设置（只更新账号只读字段）
      await refresh()
    } else {
      notice.value = result.message || '未登录：凭据无效或已过期，请重新获取 Cookie。'
    }
  } catch (err) {
    sessionError.value = err.message
  } finally {
    checking.value = false
  }
}

/* ==========================================================================
 * 人工登录窗口（spec 2.4 / 前端 5.9）
 * 前端点按钮 → 后端在运行服务的那台机器上弹出真实浏览器 → 由人扫码/输入账号完成登录
 * （不自动破解验证码）→ 后端捕获 Cookie 写入设置，前端轮询到 success 后自动切换账号模式。
 * ========================================================================== */

function stopLoginPolling() {
  if (loginTimer !== null) {
    clearInterval(loginTimer)
    loginTimer = null
  }
}

/** 登录成功：自动切换登录账号模式 + 刷新 GET /api/settings（徽标 / masked_cookie 同步） */
async function applyBrowserLoginSuccess(status) {
  loginState.value = status
  stopLoginPolling()
  loginError.value = ''
  loginInstallHint.value = ''
  form.value.account.mode = 'account'
  session.value = null
  notice.value = `已登录：${status.uname || '已登录'}。已自动切换为「登录账号」模式。`
  await refresh()
  // 只同步账号只读段，保留用户尚未保存的采集参数编辑
  form.value.account = { ...settings.value.account }
}

async function pollLoginStatus() {
  try {
    const status = await getBrowserLoginStatus()
    if (status?.state === 'success') {
      await applyBrowserLoginSuccess(status)
      return
    }
    loginState.value = status
    if (!['opening', 'waiting'].includes(status?.state)) {
      stopLoginPolling()
      if (status?.state === 'failed') loginError.value = status.message || '登录失败'
    }
  } catch (err) {
    if (err?.status === 503) {
      loginInstallHint.value = `${PLAYWRIGHT_INSTALL_HINT}${err.detail ? `\n后端提示：${err.detail}` : ''}`
    } else {
      loginError.value = err.message
    }
    stopLoginPolling()
  }
}

async function onBrowserLogin() {
  if (browserLoginBusy.value || saving.value) return
  loginStarting.value = true
  loginError.value = ''
  loginInstallHint.value = ''
  loginState.value = null
  sessionError.value = ''
  notice.value = ''
  saveError.value = ''
  try {
    const status = await startBrowserLogin()
    loginState.value = status
    if (status?.state === 'success') {
      await applyBrowserLoginSuccess(status)
      return
    }
    if (['opening', 'waiting'].includes(status?.state)) {
      stopLoginPolling()
      loginTimer = setInterval(pollLoginStatus, LOGIN_POLL_INTERVAL)
      await pollLoginStatus() // 立即拉一次，尽快展示「已打开登录窗口」文案
    }
  } catch (err) {
    if (err?.status === 503) {
      loginInstallHint.value = `${PLAYWRIGHT_INSTALL_HINT}${err.detail ? `\n后端提示：${err.detail}` : ''}`
    } else {
      loginError.value = err.message
    }
  } finally {
    loginStarting.value = false
  }
}

async function onCancelBrowserLogin() {
  if (!loginRunning.value || loginCancelling.value) return
  loginCancelling.value = true
  loginError.value = ''
  try {
    const status = await cancelBrowserLogin()
    loginState.value = status
    stopLoginPolling()
  } catch (err) {
    loginError.value = err.message
    stopLoginPolling()
  } finally {
    loginCancelling.value = false
  }
}

async function onLogout() {
  if (saving.value || checking.value || loginRunning.value) return
  const confirmed = window.confirm('确定退出登录并清除服务端保存的 Cookie 吗？清除后将以匿名模式爬取。')
  if (!confirmed) return
  saving.value = true
  saveError.value = ''
  notice.value = ''
  session.value = null
  loginState.value = null
  try {
    const updated = await save({ account: { mode: 'anonymous', cookie: '' } })
    // 只同步账号段，保留其它尚未保存的采集参数编辑
    form.value.account = { ...updated.account }
    cookieDraft.value = ''
    notice.value = '已退出登录并清除服务端保存的凭据'
    emit('saved', updated)
  } catch (err) {
    saveError.value = err.message
  } finally {
    saving.value = false
  }
}

function onResetDefaults() {
  form.value = cloneSettings(DEFAULT_SETTINGS)
  cookieDraft.value = ''
  errors.value = {}
  saveError.value = ''
  sessionError.value = ''
  session.value = null
  notice.value = hasSavedCookie.value
    ? '已填入默认值（匿名模式）。点击「保存」后将同时清除已保存的 Cookie。'
    : '已填入默认值，点击「保存」后生效'
}

function requestClose() {
  if (saving.value) return
  emit('close')
}

function onKeydown(event) {
  if (event.key === 'Escape') requestClose()
}

onMounted(async () => {
  if (!loaded.value) await load()
  form.value = cloneSettings(settings.value)
  formLoading.value = false
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  stopLoginPolling()
})
</script>

<template>
  <Teleport to="body">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" @click.self="requestClose">
      <div class="modal__panel">
        <header class="modal__head">
          <h2 id="settings-title"><Icon name="settings-3-line" :size="17" /> 系统设置</h2>
          <button
            type="button"
            class="icon-btn modal__close"
            title="关闭"
            aria-label="关闭"
            :disabled="saving"
            @click="requestClose"
          >
            <Icon name="close-line" :size="16" />
          </button>
        </header>

        <div class="modal__body">
          <p v-if="formLoading" class="hint">正在加载设置…</p>
          <p v-else-if="loadError" class="hint hint--warn">
            <Icon name="alert-line" :size="14" />
            加载服务端设置失败：{{ loadError }}
            <br />
            已回退为内置默认值，可先查看界面；后端启动后保存即可写回。
          </p>

          <!-- ========================= 账号 ========================= -->
          <section class="form-section">
            <h3 class="form-section__title"><Icon name="user-line" :size="15" /> 账号</h3>
            <p class="form-section__desc">
              默认匿名爬取；登录后可获取楼中楼与更完整的分页数据。推荐用「弹出登录窗口」由本人在
              后端所在电脑上扫码 / 输入账号完成登录（不自动破解验证码）；Cookie 只保存在服务端本地文件，
              接口永不回传明文（这里只显示脱敏值）。
            </p>

            <div class="account-mode">
              <span class="account-mode__label">爬取身份</span>
              <div class="seg" role="group" aria-label="爬取身份">
                <button
                  type="button"
                  class="seg__item"
                  :class="{ 'is-active': formMode === 'anonymous' }"
                  :aria-pressed="formMode === 'anonymous'"
                  :disabled="saving"
                  @click="form.account.mode = 'anonymous'"
                >
                  <Icon name="shield-user-line" :size="13" /> 匿名
                </button>
                <button
                  type="button"
                  class="seg__item"
                  :class="{ 'is-active': formMode === 'account' }"
                  :aria-pressed="formMode === 'account'"
                  :disabled="saving"
                  @click="form.account.mode = 'account'"
                >
                  <Icon name="user-line" :size="13" /> 登录账号
                </button>
              </div>
              <span class="account-mode__current" :class="isAccountMode ? 'is-account' : ''">
                当前生效：{{ accountLabel }}
                <template v-if="isAccountMode && session?.is_login">（本次检查：{{ session.uname }}）</template>
              </span>
            </div>

            <!-- 推荐方式：后端在运行服务的那台机器上弹出真实浏览器窗口，由人完成登录 -->
            <div class="browser-login">
              <div class="browser-login__row">
                <button
                  type="button"
                  class="btn btn--primary browser-login__start"
                  :disabled="saving || checking || browserLoginBusy"
                  @click="onBrowserLogin"
                >
                  <Icon name="login-box-line" :size="15" :spin="loginStarting || loginRunning" />
                  {{ loginRunning ? '等待登录窗口完成…' : '登录 B 站账号（弹出登录窗口）' }}
                </button>
                <button
                  v-if="loginRunning"
                  type="button"
                  class="btn browser-login__cancel"
                  :disabled="loginCancelling"
                  @click="onCancelBrowserLogin"
                >
                  <Icon name="close-line" :size="14" />
                  {{ loginCancelling ? '取消中…' : '取消登录' }}
                </button>
              </div>

              <p v-if="loginRunning" class="hint hint--warn browser-login__hint">
                <Icon name="pulse-line" :size="14" />
                <span>
                  已打开登录窗口，请在<strong>运行后端的这台电脑</strong>上完成扫码 / 账号登录
                  （每 2 秒自动检查一次）。登录成功后窗口会自动关闭，本页会自动切换为「登录账号」。
                </span>
              </p>
              <p v-else-if="loginSucceeded" class="hint hint--ok browser-login__hint">
                <Icon name="checkbox-circle-line" :size="14" />
                已登录：{{ loginState.uname || '已登录' }}（凭据已保存到服务端）
              </p>
              <p v-else-if="loginTerminalMessage" class="hint hint--warn browser-login__hint">
                <Icon name="alert-line" :size="14" />
                {{ loginTerminalMessage }}
              </p>
              <p v-if="loginError" class="hint hint--error browser-login__hint">
                <Icon name="alert-line" :size="14" />
                {{ loginError }}
              </p>
              <div v-if="loginInstallHint" class="hint hint--error browser-login__install">
                <Icon name="alert-line" :size="14" />
                <div>
                  <p class="browser-login__install-title">浏览器依赖缺失（HTTP 503），请在运行后端的电脑上安装：</p>
                  <pre class="browser-login__cmds">{{ loginInstallHint }}</pre>
                </div>
              </div>
            </div>

            <div class="cookie-state">
              <span class="cookie-state__badge" :class="hasSavedCookie ? 'is-saved' : ''">
                <Icon :name="hasSavedCookie ? 'checkbox-circle-line' : 'lock-line'" :size="13" />
                {{ hasSavedCookie ? '已保存凭据' : '未保存凭据' }}
              </span>
              <span v-if="hasSavedCookie" class="cookie-state__masked mono">{{ maskedCookie || 'SESSD…' }}</span>
              <button
                v-if="hasSavedCookie"
                type="button"
                class="btn btn--danger-outline cookie-state__logout"
                :disabled="saving || checking || loginRunning"
                @click="onLogout"
              >
                <Icon name="logout-box-r-line" :size="14" />
                退出登录 / 清除凭据
              </button>
            </div>

            <!-- 兜底方式：手动粘贴 Cookie（折叠，默认收起） -->
            <details class="advanced-cookie" :open="advancedOpen" @toggle="advancedOpen = $event.target.open">
              <summary class="advanced-cookie__summary">
                <Icon name="lock-line" :size="13" /> 高级：手动粘贴 Cookie（兜底方式）
              </summary>
              <div class="advanced-cookie__body">
                <label class="field">
                  <span class="field__label">B 站 Cookie（SESSDATA 或完整 Cookie 串）</span>
                  <div class="cookie-row">
                    <input
                      v-model="cookieDraft"
                      type="password"
                      name="crawl-cookie"
                      autocomplete="off"
                      spellcheck="false"
                      placeholder="粘贴 SESSDATA=...; bili_jct=...（保存后不回显）"
                      :disabled="saving || checking"
                    />
                    <button
                      type="button"
                      class="btn cookie-row__check"
                      :disabled="saving || checking"
                      @click="onCheckLogin"
                    >
                      <Icon name="login-box-line" :size="14" :spin="checking" />
                      {{ checking ? '检查中…' : '检查登录状态' }}
                    </button>
                  </div>
                  <span v-if="errors.cookie" class="field__error">{{ errors.cookie }}</span>
                  <span v-else class="field__hint">
                    {{
                      hasSavedCookie
                        ? '已保存凭据，留空表示继续使用；输入新 Cookie 会覆盖旧凭据。'
                        : '尚未保存凭据。输入后点「检查登录状态」验证，再点「保存」写入服务端。'
                    }}
                  </span>
                </label>
                <p class="advanced-cookie__note">
                  适用场景：登录窗口无法打开（如无图形界面环境）时，从已登录 B 站的浏览器复制 Cookie 粘贴到这里。
                </p>
              </div>
            </details>

            <p v-if="sessionError" class="hint hint--error account-hint">
              <Icon name="alert-line" :size="14" />
              {{ sessionError }}
            </p>
            <p v-if="notice" class="hint hint--ok account-hint">
              <Icon name="checkbox-circle-line" :size="14" />
              {{ notice }}
            </p>
          </section>

          <!-- ======================= 采集参数 ======================= -->
          <section class="form-section">
            <h3 class="form-section__title"><Icon name="download-cloud-2-line" :size="15" /> 采集参数</h3>
            <p class="form-section__desc">这里是默认值；每次「开始分析」前还会弹窗确认，可逐次覆盖。</p>

            <div class="form-grid">
              <label class="field">
                <span class="field__label">最大采集页数</span>
                <input v-model.number="form.crawl.max_pages" type="number" min="0" max="50" step="1" :disabled="saving" />
                <span v-if="errors.max_pages" class="field__error">{{ errors.max_pages }}</span>
                <span v-else class="field__hint">0 = 不限（最多 50）</span>
              </label>

              <label class="field">
                <span class="field__label">最大楼中楼页数</span>
                <input v-model.number="form.crawl.max_sub_pages" type="number" min="0" max="20" step="1" :disabled="saving" />
                <span v-if="errors.max_sub_pages" class="field__error">{{ errors.max_sub_pages }}</span>
                <span v-else class="field__hint">0 = 不限（最多 20）</span>
              </label>

              <label class="field">
                <span class="field__label">每页一级评论数</span>
                <input v-model.number="form.crawl.page_size" type="number" min="1" max="50" step="1" :disabled="saving" />
                <span v-if="errors.page_size" class="field__error">{{ errors.page_size }}</span>
                <span v-else class="field__hint">1 ~ 50，默认 20</span>
              </label>

              <label class="field">
                <span class="field__label">最多新增评论数</span>
                <input v-model.number="form.crawl.max_items" type="number" min="0" step="1" :disabled="saving" />
                <span v-if="errors.max_items" class="field__error">{{ errors.max_items }}</span>
                <span v-else class="field__hint">0 = 不限</span>
              </label>

              <label class="field">
                <span class="field__label">请求最小间隔（秒）</span>
                <input v-model.number="form.crawl.delay_seconds" type="number" min="2" max="60" step="0.5" :disabled="saving" />
                <span v-if="errors.delay_seconds" class="field__error">{{ errors.delay_seconds }}</span>
                <span v-else class="field__hint">限速硬性要求，2.0 ~ 60</span>
              </label>

              <label class="field">
                <span class="field__label">单次请求超时（秒）</span>
                <input v-model.number="form.crawl.timeout_seconds" type="number" min="0.1" max="60" step="0.1" :disabled="saving" />
                <span v-if="errors.timeout_seconds" class="field__error">{{ errors.timeout_seconds }}</span>
                <span v-else class="field__hint">大于 0（0.1 ~ 60），默认 15，可填小数</span>
              </label>

              <label class="field">
                <span class="field__label">排序方式</span>
                <select v-model="form.crawl.sort_mode" :disabled="saving">
                  <option value="time">按时间（time）</option>
                  <option value="hot">按热度（hot）</option>
                </select>
              </label>

              <div class="field">
                <span class="field__label">采集选项</span>
                <div class="settings-checks">
                  <label class="check">
                    <input v-model="form.crawl.include_replies" type="checkbox" :disabled="saving" />
                    <span class="check__text">包含楼中楼回复</span>
                  </label>
                  <label class="check">
                    <input v-model="form.crawl.require_login" type="checkbox" :disabled="saving" />
                    <span class="check__text">
                      必须登录
                      <span class="check__desc">未登录时任务直接失败（建议登录账号后开启）</span>
                    </span>
                  </label>
                  <label class="check">
                    <input v-model="form.crawl.init_buvid" type="checkbox" :disabled="saving" />
                    <span class="check__text">初始化 buvid 设备指纹</span>
                  </label>
                </div>
              </div>
            </div>

            <div class="settings-switch-row">
              <label class="check">
                <input v-model="form.crawl.real_crawl" type="checkbox" :disabled="saving" />
                <span class="check__text">
                  真实采集
                  <span class="check__desc">关闭后使用离线占位数据，不联网（适合课堂演示 / 无网环境）</span>
                </span>
              </label>
            </div>
          </section>

          <!-- ======================= 自动流程 ======================= -->
          <section class="form-section">
            <h3 class="form-section__title"><Icon name="pulse-line" :size="15" /> 自动流程</h3>
            <p class="form-section__desc">
              默认关闭：采集完成后暂停为「待清洗」、清洗完成后暂停为「待分析」，由详情区的按钮继续。
              开启后对应阶段会自动连续执行。
            </p>

            <div class="settings-checks">
              <label class="check">
                <input v-model="form.pipeline.auto_clean" type="checkbox" :disabled="saving" />
                <span class="check__text">
                  自动清洗
                  <span class="check__desc">采集完成后自动进入清洗，不停在「待清洗」</span>
                </span>
              </label>
              <label class="check">
                <input v-model="form.pipeline.auto_analyze" type="checkbox" :disabled="saving" />
                <span class="check__text">
                  自动分析
                  <span class="check__desc">清洗完成后自动进行情感分析与统计，不停在「待分析」</span>
                </span>
              </label>
            </div>
          </section>

          <p v-if="saveError" class="hint hint--error settings-error">
            <Icon name="alert-line" :size="14" />
            {{ saveError }}
          </p>
        </div>

        <footer class="modal__foot">
          <button type="button" class="btn" :disabled="saving" @click="onResetDefaults">
            <Icon name="restart-line" :size="14" />
            恢复默认
          </button>
          <div class="modal__actions">
            <button type="button" class="btn" :disabled="saving" @click="requestClose">取消</button>
            <button type="button" class="btn btn--primary" :disabled="saving || formLoading" @click="onSave">
              <Icon name="checkbox-circle-line" :size="14" />
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(15, 23, 42, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal__panel {
  width: min(720px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
  border: 1px solid var(--border);
}
.modal__head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
.modal__head h2 {
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.modal__close {
  color: var(--text-muted);
}
.modal__close:hover:not(:disabled) {
  background: var(--negative-soft);
  border-color: #fecaca;
  color: var(--negative);
}
.modal__body {
  padding: 16px 18px;
}
.account-mode {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.account-mode__label {
  font-size: 14px;
  font-weight: 600;
}
.account-mode__current {
  font-size: 12.5px;
  color: var(--text-muted);
}
.account-mode__current.is-account {
  color: var(--positive);
  font-weight: 600;
}
/* 人工登录窗口（推荐方式） */
.browser-login {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 12px;
}
.browser-login__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.browser-login__hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  margin: 0;
}
.browser-login__hint .icon {
  margin-top: 2px;
}
.browser-login__install {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  margin: 0;
}
.browser-login__install .icon {
  margin-top: 2px;
}
.browser-login__install-title {
  margin: 0 0 4px;
  font-weight: 600;
}
.browser-login__cmds {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.cookie-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cookie-row input {
  flex: 1 1 260px;
  min-width: 200px;
}
.cookie-row__check {
  flex: 0 0 auto;
  white-space: nowrap;
}
.cookie-state {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 2px;
}
.cookie-state__badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  color: var(--text-muted);
  border: 1px solid var(--border);
  background: var(--surface-alt);
  border-radius: 999px;
  padding: 1px 9px;
}
.cookie-state__badge.is-saved {
  color: var(--positive);
  border-color: #bbf7d0;
  background: var(--positive-soft);
}
.cookie-state__masked {
  font-size: 12.5px;
  color: var(--text);
  background: var(--surface-alt);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 1px 8px;
}
.cookie-state__logout {
  margin-left: auto;
  padding: 3px 10px;
  font-size: 12.5px;
}
/* 手动 Cookie 兜底：折叠项 */
.advanced-cookie {
  margin-top: 12px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface-alt);
  padding: 8px 12px;
}
.advanced-cookie__summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.advanced-cookie__summary:hover {
  color: var(--text);
}
.advanced-cookie__body {
  padding-top: 10px;
}
.advanced-cookie__note {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}
/* 危险操作描边按钮：清除凭据（不是整页删除，弱化为描边） */
.btn--danger-outline {
  color: var(--negative);
  border-color: #fecaca;
  background: var(--surface);
}
.btn--danger-outline:hover:not(:disabled) {
  background: var(--negative-soft);
}
.account-hint {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 12px 0 0;
}
.settings-checks {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 2px;
}
.settings-switch-row {
  margin-top: 12px;
}
.settings-error {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 16px 0 0;
}
.modal__foot {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
  background: var(--surface-alt);
}
.modal__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
@media (max-width: 560px) {
  .modal__panel {
    max-height: 94vh;
  }
  .cookie-state__logout {
    margin-left: 0;
  }
}
</style>
