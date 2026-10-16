<script setup>
import { computed, ref, watch } from 'vue'
import { predict } from '../api/client.js'
import { saveSoloComment } from '../api/artifacts.js'
import Icon from '../components/Icon.vue'
import { formatPercent, labelClass, labelText, percentWidth } from '../utils/format.js'

const text = ref('')
const loading = ref(false)
/** 结果只来自后端响应；骨架阶段未接后端时保持为 null，不编造任何预测值。 */
const result = ref(null)
const error = ref('')
/** 请求状态：idle | loading | ok | error */
const state = ref('idle')

// ---------------------------------------------------------------------------
// 「保存到左栏」开关（默认开启）：开启时预测成功后把结果存到 /api/solo-comments，
// 左栏②段「单独评论」即可看到；关闭时不保存。该开关不改动本页既有的预测与展示逻辑。
// ---------------------------------------------------------------------------
const SAVE_PREF_KEY = 'csa.singlePredict.saveToSidebar'

function readSavePref() {
  try {
    return localStorage.getItem(SAVE_PREF_KEY) !== '0'
  } catch {
    return true
  }
}

const saveToList = ref(readSavePref())
watch(saveToList, (value) => {
  try {
    localStorage.setItem(SAVE_PREF_KEY, value ? '1' : '0')
  } catch {
    /* 隐私模式下 localStorage 不可用，忽略即可 */
  }
})

/** idle | saving | saved | error */
const saveState = ref('idle')
const saveMessage = ref('')

async function persistResult(payload) {
  if (!payload) return
  saveState.value = 'saving'
  saveMessage.value = ''
  try {
    await saveSoloComment({
      text: payload.text,
      label: payload.label,
      confidence: payload.confidence,
      probs: payload.probs || {},
      // method 以后端 /api/predict 响应为准（当前为 rule_placeholder，将来是模型名）；
      // 老后端若确实没有该字段，再回退到 rule_placeholder
      method: payload.method || 'rule_placeholder',
    })
    saveState.value = 'saved'
    saveMessage.value = `已保存到左栏「单独评论」（method: ${payload.method || 'rule_placeholder'}）`
  } catch (err) {
    saveState.value = 'error'
    saveMessage.value = `保存到左栏失败：${err.message}`
  }
}

const trimmedLength = computed(() => text.value.trim().length)
const canSubmit = computed(() => trimmedLength.value > 0 && !loading.value)

const probs = computed(() => {
  const p = result.value && result.value.probs
  if (!p) return []
  return [
    { key: 'positive', name: '正面', value: Number(p.positive) },
    { key: 'negative', name: '负面', value: Number(p.negative) },
  ]
})

function reset() {
  result.value = null
  error.value = ''
  state.value = 'idle'
  saveState.value = 'idle'
  saveMessage.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  loading.value = true
  result.value = null
  error.value = ''
  saveState.value = 'idle'
  saveMessage.value = ''
  state.value = 'loading'
  try {
    result.value = await predict(text.value.trim())
    state.value = 'ok'
    if (saveToList.value) await persistResult(result.value)
  } catch (err) {
    error.value = err.message
    state.value = 'error'
  } finally {
    loading.value = false
  }
}

function clearText() {
  text.value = ''
  reset()
}

/** 结果极性 → 表情图标（与 SentimentBadge 同一套图形语言） */
const RESULT_ICONS = { positive: 'emotion-happy-line', negative: 'emotion-unhappy-line', neutral: 'emotion-normal-line' }
const resultIcon = computed(() => RESULT_ICONS[String(result.value?.label || '').toLowerCase()] || 'question-line')
</script>

<template>
  <section class="stack">
    <div class="page-head">
      <h1>单条预测</h1>
      <p>输入一条评论文本，调用 <code>POST /api/predict</code> 返回情感极性与置信度。</p>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card__head">
          <h2><Icon name="chat-1-line" :size="16" /> 评论输入</h2>
          <span class="card__hint">POST /api/predict</span>
        </div>

        <form @submit.prevent="submit">
          <label class="field">
            <span class="field__label">评论文本</span>
            <textarea
              v-model="text"
              placeholder="例如：这个商品质量很好，物流也快，非常满意。"
              :disabled="loading"
            ></textarea>
            <span class="counter">{{ text.length }} 字（有效 {{ trimmedLength }} 字）</span>
          </label>

          <div class="actions">
            <button type="submit" class="btn btn--primary" :disabled="!canSubmit">
              <Icon name="send-plane-2-line" :size="14" />
              {{ loading ? '请求中…' : '开始预测' }}
            </button>
            <button type="button" class="btn" :disabled="loading" @click="clearText">
              <Icon name="eraser-line" :size="14" />
              清空
            </button>
            <label class="switch" title="开启后预测成功会把结果保存到左栏「单独评论」">
              <input v-model="saveToList" type="checkbox" :disabled="loading" />
              <span class="switch__label">保存到左栏</span>
              <span class="switch__state">{{ saveToList ? '开' : '关' }}</span>
            </label>
            <span class="card__hint">请求体：<code>{ "text": "…" }</code></span>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card__head">
          <h2><Icon name="bar-chart-2-line" :size="16" /> 预测结果</h2>
          <span class="card__hint">响应字段：text / label / confidence / probs</span>
        </div>

        <div v-if="state === 'error'" class="hint hint--icon hint--error">
          <Icon name="error-warning-line" :size="14" />
          <strong>待接入后端</strong>：{{ error }}
        </div>

        <div v-else-if="state === 'idle'" class="hint hint--icon hint--warn">
          <Icon name="alert-line" :size="14" />
          <span>
            <strong>待接入后端</strong>：尚未发起预测请求。请先启动 FastAPI 服务
            （<code>python -m uvicorn src.inference.app:app --port 8000</code>），再点击「开始预测」。
            骨架阶段界面不会展示任何模拟结果。
          </span>
        </div>

        <div v-else-if="state === 'loading'" class="hint hint--icon">
          <Icon name="loader-4-line" :size="14" spin />
          正在请求后端，请稍候…
        </div>

        <div v-else-if="result" class="result">
          <div class="result__headline">
            <Icon :name="resultIcon" :size="19" />
            <span :class="labelClass(result.label)">{{ labelText(result.label) }}</span>
            <span class="card__hint">置信度 {{ formatPercent(result.confidence) }}</span>
          </div>
          <div class="result__text">{{ result.text }}</div>
          <div v-for="p in probs" :key="p.key" class="prob-row">
            <span>{{ p.name }}</span>
            <span class="prob-row__bar">
              <span
                class="prob-row__fill"
                :class="`prob-row__fill--${p.key}`"
                :style="{ width: percentWidth(p.value) }"
              ></span>
            </span>
            <span class="prob-row__value">{{ formatPercent(p.value) }}</span>
          </div>
        </div>

        <div v-else class="hint hint--icon hint--warn">
          <Icon name="alert-line" :size="14" />
          <span><strong>待接入后端</strong>：后端已响应但未返回可展示字段，请核对接口契约。</span>
        </div>

        <p v-if="saveState === 'saving'" class="hint hint--icon">
          <Icon name="loader-4-line" :size="13" spin />
          正在保存到左栏「单独评论」…
        </p>
        <p v-else-if="saveMessage" class="hint hint--icon" :class="saveState === 'error' ? 'hint--error' : 'hint--ok'">
          <Icon :name="saveState === 'error' ? 'error-warning-line' : 'checkbox-circle-line'" :size="13" />
          <span>
            {{ saveMessage }}
            <span v-if="saveState === 'saved'" class="save-tip">返回「B站分析」页即可在左栏②段看到它。</span>
          </span>
        </p>

        <div class="card__foot">
          契约：请求 <code>{"text": str}</code> → 响应
          <code>{"text","label","confidence","probs":{"positive","negative"}}</code>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  color: var(--text);
  user-select: none;
  cursor: pointer;
}
.switch input {
  width: auto;
  margin: 0;
  accent-color: var(--primary);
}
.switch__label {
  font-weight: 600;
}
.switch__state {
  font-size: 12px;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 7px;
  background: var(--surface-alt);
}
.save-tip {
  color: var(--text-muted);
  font-size: 12.5px;
  margin-left: 6px;
}
.result__headline .icon {
  color: var(--text-muted);
}
</style>
