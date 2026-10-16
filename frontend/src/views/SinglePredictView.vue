<script setup>
import { computed, ref } from 'vue'
import { predict } from '../api/client.js'
import { formatPercent, labelClass, labelText, percentWidth } from '../utils/format.js'

const text = ref('')
const loading = ref(false)
/** 结果只来自后端响应；骨架阶段未接后端时保持为 null，不编造任何预测值。 */
const result = ref(null)
const error = ref('')
/** 请求状态：idle | loading | ok | error */
const state = ref('idle')

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
}

async function submit() {
  if (!canSubmit.value) return
  loading.value = true
  result.value = null
  error.value = ''
  state.value = 'loading'
  try {
    result.value = await predict(text.value.trim())
    state.value = 'ok'
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
          <h2>评论输入</h2>
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
              {{ loading ? '请求中…' : '开始预测' }}
            </button>
            <button type="button" class="btn" :disabled="loading" @click="clearText">清空</button>
            <span class="card__hint">请求体：<code>{ "text": "…" }</code></span>
          </div>
        </form>
      </div>

      <div class="card">
        <div class="card__head">
          <h2>预测结果</h2>
          <span class="card__hint">响应字段：text / label / confidence / probs</span>
        </div>

        <div v-if="state === 'error'" class="hint hint--error">
          <strong>待接入后端</strong>：{{ error }}
        </div>

        <div v-else-if="state === 'idle'" class="hint hint--warn">
          <strong>待接入后端</strong>：尚未发起预测请求。请先启动 FastAPI 服务
          （<code>python -m uvicorn src.inference.app:app --port 8000</code>），再点击「开始预测」。
          骨架阶段界面不会展示任何模拟结果。
        </div>

        <div v-else-if="state === 'loading'" class="hint">正在请求后端，请稍候…</div>

        <div v-else-if="result" class="result">
          <div class="result__headline">
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

        <div v-else class="hint hint--warn">
          <strong>待接入后端</strong>：后端已响应但未返回可展示字段，请核对接口契约。
        </div>

        <div class="card__foot">
          契约：请求 <code>{"text": str}</code> → 响应
          <code>{"text","label","confidence","probs":{"positive","negative"}}</code>
        </div>
      </div>
    </div>
  </section>
</template>
