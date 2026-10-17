<script setup>
/**
 * components/CrawlLaunchDialog.vue — 「开始分析」前的爬虫参数确认弹窗
 *
 * 契约（2026-10-17 设置/分步流水线设计第 5 节）：
 *   - 由 BilibiliView 控制：BvInput 校验通过后先打开本弹窗，而不是直接 POST /api/videos；
 *   - 参数用设置页的采集参数预填，本次可逐次覆盖；
 *   - 展示当前账号模式与匿名可见量提示；
 *   - 确认后由父组件带着 settings 提交，取消不提交。
 *
 * 安全：任务请求只带 account.mode，**绝不携带 Cookie**（Cookie 永远取服务端设置）。
 * 本组件只收集输入并派发 confirm，请求与错误提示交给父组件（useVideoAnalysis.submit）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import { CRAWL_LIMITS, DEFAULT_SETTINGS } from '../api/settings.js'

const props = defineProps({
  /** 当前 BV 号（用于标题展示） */
  bvid: { type: String, default: '' },
  /** 已保存的设置对象（GET /api/settings）；为 null 时用内置默认值预填 */
  settings: { type: Object, default: null },
  /** 提交请求进行中 */
  submitting: { type: Boolean, default: false },
  /** 提交失败的中文错误（例如 409 已在队列中） */
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'confirm'])

function initialCrawl() {
  const source = props.settings?.crawl
  return { ...JSON.parse(JSON.stringify(DEFAULT_SETTINGS.crawl)), ...(source || {}) }
}

const form = ref(initialCrawl())
const errors = ref({})

const account = computed(() => props.settings?.account || DEFAULT_SETTINGS.account)
const isAccount = computed(() => account.value.mode === 'account')
const accountLabel = computed(() => {
  if (!isAccount.value) return '匿名（未登录）'
  return account.value.uname ? `已登录：${account.value.uname}` : '已登录账号'
})
const pipeline = computed(() => props.settings?.pipeline || DEFAULT_SETTINGS.pipeline)
const anonymousWarning = computed(() => !isAccount.value)

/** 预估本次采集规模（帮助理解 page_size / max_pages / max_items 的关系） */
const estimate = computed(() => {
  const { page_size: pageSize, max_pages: maxPages, max_items: maxItems } = form.value
  const pageCap = maxPages > 0 ? pageSize * maxPages : null
  const caps = [pageCap, maxItems > 0 ? maxItems : null].filter((value) => value !== null)
  if (!caps.length) return '本次不限制页数与条数（仍受 B 站接口限制）'
  return `本次最多约 ${Math.min(...caps)} 条一级评论${form.value.include_replies ? '（不含楼中楼）' : ''}`
})

function rangeText(limit) {
  return limit.max === null ? `需 ≥ ${limit.min}` : `${limit.min} ~ ${limit.max}`
}

function validate() {
  const next = {}
  const values = form.value

  for (const key of ['max_pages', 'max_sub_pages', 'page_size', 'max_items']) {
    const limit = CRAWL_LIMITS[key]
    const raw = values[key]
    const value = Number(raw)
    if (raw === '' || raw === null || !Number.isInteger(value) || value < limit.min || (limit.max !== null && value > limit.max)) {
      next[key] = `${limit.label}需为 ${rangeText(limit)} 的整数`
    }
  }

  const delayRaw = values.delay_seconds
  const delay = Number(delayRaw)
  const delayLimit = CRAWL_LIMITS.delay_seconds
  if (delayRaw === '' || delayRaw === null || !Number.isFinite(delay) || delay < delayLimit.min || delay > delayLimit.max) {
    next.delay_seconds = `${delayLimit.label}需在 ${delayLimit.min} ~ ${delayLimit.max} 秒之间`
  }

  const timeoutRaw = values.timeout_seconds
  const timeout = Number(timeoutRaw)
  const timeoutLimit = CRAWL_LIMITS.timeout_seconds
  // 契约：timeout_seconds 只要 > 0（允许 0.5 这类小数），前端限制 0.1 ~ 60
  if (timeoutRaw === '' || timeoutRaw === null || !Number.isFinite(timeout) || timeout < timeoutLimit.min || timeout > timeoutLimit.max) {
    next.timeout_seconds = `${timeoutLimit.label}需在 ${timeoutLimit.min} ~ ${timeoutLimit.max} 秒之间`
  }

  errors.value = next
  return Object.keys(next).length === 0
}

function confirm() {
  if (props.submitting) return
  if (!validate()) return
  emit('confirm', {
    account: { mode: account.value.mode },
    crawl: {
      real_crawl: Boolean(form.value.real_crawl),
      max_pages: Number(form.value.max_pages),
      max_sub_pages: Number(form.value.max_sub_pages),
      page_size: Number(form.value.page_size),
      include_replies: Boolean(form.value.include_replies),
      sort_mode: form.value.sort_mode === 'hot' ? 'hot' : 'time',
      max_items: Number(form.value.max_items),
      delay_seconds: Number(form.value.delay_seconds),
      timeout_seconds: Number(form.value.timeout_seconds),
      require_login: Boolean(form.value.require_login),
      init_buvid: Boolean(form.value.init_buvid),
    },
    pipeline: {
      auto_clean: Boolean(pipeline.value.auto_clean),
      auto_analyze: Boolean(pipeline.value.auto_analyze),
    },
  })
}

function requestClose() {
  if (props.submitting) return
  emit('close')
}

function onKeydown(event) {
  if (event.key === 'Escape') requestClose()
}

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="crawl-launch-title" @click.self="requestClose">
      <div class="modal__panel">
        <header class="modal__head">
          <h2 id="crawl-launch-title"><Icon name="download-cloud-2-line" :size="17" /> 确认本次爬虫参数</h2>
          <button
            type="button"
            class="icon-btn modal__close"
            title="关闭"
            aria-label="关闭"
            :disabled="submitting"
            @click="requestClose"
          >
            <Icon name="close-line" :size="16" />
          </button>
        </header>

        <div class="modal__body">
          <p class="launch__bvid">
            目标视频：<span class="mono">{{ bvid }}</span>
          </p>

          <!-- 账号模式提示：匿名可见量有限，登录后可见更多分页/楼中楼 -->
          <div class="launch__account" :class="isAccount ? 'launch__account--account' : 'launch__account--anonymous'">
            <Icon :name="isAccount ? 'user-line' : 'shield-user-line'" :size="16" />
            <div>
              <strong>本次账号：{{ accountLabel }}</strong>
              <p v-if="anonymousWarning" class="launch__account-desc">
                匿名模式只能获取公开评论的前若干页，且更容易触发 B 站限流（HTTP 412）；
                需要更完整的数据请先在左上角「设置」中登录账号。
              </p>
              <p v-else class="launch__account-desc">
                使用服务端已保存的登录凭据，可获取楼中楼与更完整的分页数据。
              </p>
            </div>
          </div>

          <p v-if="form.require_login && anonymousWarning" class="hint hint--warn launch__warn">
            <Icon name="alert-line" :size="14" />
            已勾选「必须登录」但本次为匿名模式：任务会在采集阶段直接失败，请先登录账号或取消该选项。
          </p>

          <div class="form-section">
            <h3 class="form-section__title"><Icon name="download-cloud-2-line" :size="15" /> 采集参数</h3>
            <p class="form-section__desc">默认值来自「系统设置 → 采集参数」，本次修改只影响这一次任务。</p>

            <div class="form-grid">
              <label class="field">
                <span class="field__label">最大采集页数</span>
                <input v-model.number="form.max_pages" type="number" min="0" max="50" step="1" :disabled="submitting" />
                <span v-if="errors.max_pages" class="field__error">{{ errors.max_pages }}</span>
                <span v-else class="field__hint">0 = 不限（最多 50）</span>
              </label>

              <label class="field">
                <span class="field__label">最大楼中楼页数</span>
                <input v-model.number="form.max_sub_pages" type="number" min="0" max="20" step="1" :disabled="submitting" />
                <span v-if="errors.max_sub_pages" class="field__error">{{ errors.max_sub_pages }}</span>
                <span v-else class="field__hint">0 = 不限（最多 20）</span>
              </label>

              <label class="field">
                <span class="field__label">每页一级评论数</span>
                <input v-model.number="form.page_size" type="number" min="1" max="50" step="1" :disabled="submitting" />
                <span v-if="errors.page_size" class="field__error">{{ errors.page_size }}</span>
                <span v-else class="field__hint">1 ~ 50，默认 20</span>
              </label>

              <label class="field">
                <span class="field__label">最多新增评论数</span>
                <input v-model.number="form.max_items" type="number" min="0" step="1" :disabled="submitting" />
                <span v-if="errors.max_items" class="field__error">{{ errors.max_items }}</span>
                <span v-else class="field__hint">0 = 不限</span>
              </label>

              <label class="field">
                <span class="field__label">请求最小间隔（秒）</span>
                <input v-model.number="form.delay_seconds" type="number" min="2" max="60" step="0.5" :disabled="submitting" />
                <span v-if="errors.delay_seconds" class="field__error">{{ errors.delay_seconds }}</span>
                <span v-else class="field__hint">限速硬性要求，2.0 ~ 60</span>
              </label>

              <label class="field">
                <span class="field__label">单次请求超时（秒）</span>
                <input v-model.number="form.timeout_seconds" type="number" min="0.1" max="60" step="0.1" :disabled="submitting" />
                <span v-if="errors.timeout_seconds" class="field__error">{{ errors.timeout_seconds }}</span>
                <span v-else class="field__hint">大于 0（0.1 ~ 60），默认 15，可填小数</span>
              </label>

              <label class="field">
                <span class="field__label">排序方式</span>
                <select v-model="form.sort_mode" :disabled="submitting">
                  <option value="time">按时间（time）</option>
                  <option value="hot">按热度（hot）</option>
                </select>
              </label>

              <div class="field">
                <span class="field__label">采集选项</span>
                <div class="launch__checks">
                  <label class="check">
                    <input v-model="form.include_replies" type="checkbox" :disabled="submitting" />
                    <span class="check__text">包含楼中楼回复</span>
                  </label>
                  <label class="check">
                    <input v-model="form.require_login" type="checkbox" :disabled="submitting" />
                    <span class="check__text">必须登录（未登录直接失败）</span>
                  </label>
                  <label class="check">
                    <input v-model="form.init_buvid" type="checkbox" :disabled="submitting" />
                    <span class="check__text">初始化 buvid 设备指纹</span>
                  </label>
                </div>
              </div>
            </div>

            <div class="form-grid launch__switch-row">
              <label class="check">
                <input v-model="form.real_crawl" type="checkbox" :disabled="submitting" />
                <span class="check__text">
                  真实采集
                  <span class="check__desc">关闭后使用离线占位数据，不联网（适合演示）</span>
                </span>
              </label>
            </div>

            <p class="launch__estimate"><Icon name="database-2-line" :size="13" /> {{ estimate }}</p>
          </div>

          <div class="form-section">
            <h3 class="form-section__title"><Icon name="pulse-line" :size="15" /> 自动流程</h3>
            <p class="form-section__desc">
              采集完成{{ pipeline.auto_clean ? '后自动清洗' : '后暂停为「待清洗」' }}；
              清洗完成{{ pipeline.auto_analyze ? '后自动情感分析' : '后暂停为「待分析」' }}。
              <template v-if="!pipeline.auto_clean || !pipeline.auto_analyze">
                如需一次跑完，请到「系统设置 → 自动流程」开启对应开关。
              </template>
            </p>
          </div>

          <p v-if="error" class="hint hint--error launch__error">
            <Icon name="alert-line" :size="14" />
            {{ error }}
          </p>
        </div>

        <footer class="modal__foot">
          <span class="modal__total">{{ estimate }}</span>
          <div class="modal__actions">
            <button type="button" class="btn" :disabled="submitting" @click="requestClose">取消</button>
            <button type="button" class="btn btn--primary" :disabled="submitting" @click="confirm">
              <Icon name="play-line" :size="14" />
              {{ submitting ? '提交中…' : '确认并开始分析' }}
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
  width: min(680px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: var(--surface);
  border-radius: var(--radius);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
  border: 1px solid var(--border);
}
.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
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
.launch__bvid {
  margin: 0 0 12px;
  font-size: 13.5px;
  color: var(--text-muted);
}
.launch__account {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface-alt);
  font-size: 13.5px;
}
.launch__account .icon {
  margin-top: 2px;
}
.launch__account--anonymous {
  border-color: var(--warn-border);
  background: var(--warn-bg);
  color: var(--warn-text);
}
.launch__account--account {
  border-color: #bbf7d0;
  background: var(--positive-soft);
  color: var(--positive);
}
.launch__account-desc {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: inherit;
  opacity: 0.92;
}
.launch__warn {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 12px 0 0;
}
.launch__checks {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 2px;
}
.launch__switch-row {
  margin-top: 12px;
}
.launch__estimate {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 5px;
}
.launch__error {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 14px 0 0;
}
.modal__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 18px;
  border-top: 1px solid var(--border);
  background: var(--surface-alt);
}
.modal__total {
  color: var(--text-muted);
  font-size: 12.5px;
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
  .launch__checks {
    gap: 4px;
  }
}
</style>
