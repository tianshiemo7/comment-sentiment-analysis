<script setup>
import { computed, ref } from 'vue'
import { predictBatch } from '../api/client.js'
import Icon from '../components/Icon.vue'
import { formatPercent, labelClass, labelText, truncate } from '../utils/format.js'
import { backendHintFor } from '../utils/requestError.js'

const ALLOWED_EXT = ['.csv', '.xlsx']

const file = ref(null)
const fileError = ref('')
const loading = ref(false)
/** 结果只来自后端响应；尚未发起请求时保持为 null。 */
const result = ref(null)
const error = ref('')
/** idle | loading | ok | error */
const state = ref('idle')

/** 错误态附加的后端启动提示：仅连接类错误才有（判定逻辑见 utils/requestError.js）。 */
const backendHint = computed(() => backendHintFor(error.value))

const fileMeta = computed(() => {
  if (!file.value) return null
  const sizeKb = file.value.size / 1024
  const size = sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(2)} MB` : `${sizeKb.toFixed(1)} KB`
  return { name: file.value.name, size }
})

const rows = computed(() => (result.value && Array.isArray(result.value.results) ? result.value.results : []))
const exportUrl = computed(() => (result.value ? result.value.export_url : null))

function onFileChange(event) {
  const picked = event.target.files && event.target.files[0]
  fileError.value = ''
  result.value = null
  error.value = ''
  state.value = 'idle'

  if (!picked) {
    file.value = null
    return
  }
  const lower = picked.name.toLowerCase()
  if (!ALLOWED_EXT.some((ext) => lower.endsWith(ext))) {
    file.value = null
    fileError.value = `文件格式不支持：仅接受 ${ALLOWED_EXT.join(' / ')}（当前：${picked.name}）`
    event.target.value = ''
    return
  }
  file.value = picked
}

async function submit() {
  if (!file.value || loading.value) return
  loading.value = true
  result.value = null
  error.value = ''
  state.value = 'loading'
  try {
    result.value = await predictBatch(file.value)
    state.value = 'ok'
  } catch (err) {
    error.value = err.message
    state.value = 'error'
  } finally {
    loading.value = false
  }
}

function resetInput(event) {
  file.value = null
  fileError.value = ''
  result.value = null
  error.value = ''
  state.value = 'idle'
  const input = event && event.target && event.target.form
    ? event.target.form.querySelector('input[type="file"]')
    : null
  if (input) input.value = ''
}
</script>

<template>
  <section class="stack">
    <div class="page-head">
      <h1>批量预测</h1>
      <p>
        上传 <code>.csv</code> 或 <code>.xlsx</code> 文件，调用
        <code>POST /api/predict/batch</code> 批量输出情感极性，并导出结果文件。
      </p>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="upload-2-line" :size="16" /> 文件上传</h2>
        <span class="card__hint">multipart/form-data，字段名 <code>file</code></span>
      </div>

      <form @submit.prevent="submit">
        <label class="field">
          <span class="field__label">选择数据文件（.csv / .xlsx）</span>
          <input type="file" accept=".csv,.xlsx" :disabled="loading" @change="onFileChange" />
        </label>

        <p v-if="fileMeta" class="card__hint" style="margin-top: -6px">
          已选择：<span class="mono">{{ fileMeta.name }}</span>（{{ fileMeta.size }}）
        </p>
        <p v-else class="card__hint" style="margin-top: -6px">尚未选择文件。</p>

        <div v-if="fileError" class="hint hint--icon hint--error" style="margin-bottom: 12px">
          <Icon name="alert-line" :size="14" />
          <span>{{ fileError }}</span>
        </div>

        <div class="actions">
          <button type="submit" class="btn btn--primary" :disabled="!file || loading">
            <Icon name="send-plane-2-line" :size="14" />
            {{ loading ? '上传中…' : '开始批量预测' }}
          </button>
          <button type="button" class="btn" :disabled="loading" @click="resetInput">
            <Icon name="eraser-line" :size="14" />
            重置
          </button>
        </div>
      </form>

      <div class="card__foot">
        文件约定：文本列字段名为 <code>text</code>（见仓库根 README「数据格式约定」一节与
        <code>data/sample/</code> 样例）。
      </div>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="file-list-3-line" :size="16" /> 批量结果</h2>
        <span class="card__hint">
          <template v-if="result">共 {{ result.total }} 条</template>
          <template v-else>响应字段：total / results / export_url</template>
        </span>
      </div>

      <div v-if="state === 'error'" class="hint hint--icon hint--error">
        <Icon name="error-warning-line" :size="14" />
        <span>
          <strong>批量预测失败</strong>：{{ error }}
          <span v-if="backendHint" class="backend-hint">{{ backendHint }}</span>
        </span>
      </div>

      <div v-else-if="state === 'loading'" class="hint hint--icon">
        <Icon name="loader-4-line" :size="14" spin />
        <span>正在上传并请求后端，请稍候…</span>
      </div>

      <div v-else-if="state === 'idle'" class="hint hint--icon">
        <Icon name="question-line" :size="14" />
        <span>
          <strong>等待上传</strong>：选择 <code>.csv</code> / <code>.xlsx</code> 文件后点击「开始批量预测」，
          结果会显示在此处。
        </span>
      </div>

      <div v-else-if="state === 'ok' && rows.length === 0" class="hint hint--icon hint--warn">
        <Icon name="alert-line" :size="14" />
        <span><strong>结果为空</strong>：后端返回的 <code>results</code> 为空数组，请确认上传文件里包含评论文本。</span>
      </div>

      <!-- 结果表格与导出按钮始终渲染（无数据时为空态），保证 DOM 结构稳定 -->
      <div class="actions" style="margin: 12px 0">
        <a
          v-if="exportUrl"
          class="btn btn--primary"
          :href="exportUrl"
          download
          style="text-decoration: none"
        >
          <Icon name="download-2-line" :size="14" />
          导出结果文件
        </a>
        <button v-else type="button" class="btn" disabled>
          <Icon name="download-2-line" :size="14" />
          导出结果文件
        </button>
        <span v-if="!exportUrl" class="card__hint">
          <template v-if="state === 'ok'">
            响应未提供 <code>export_url</code>，暂无可下载文件。
          </template>
          <template v-else>完成批量预测后可下载结果文件。</template>
        </span>
        <span v-else class="card__hint mono">导出文件：{{ exportUrl }}</span>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 72px">序号</th>
              <th>评论文本</th>
              <th style="width: 92px">情感</th>
              <th style="width: 108px">置信度</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="rows.length === 0">
              <td class="empty-cell" colspan="4">
                <template v-if="state === 'loading'">正在加载…</template>
                <template v-else-if="state === 'error'">请求失败，详情见上方提示。</template>
                <template v-else-if="state === 'ok'">后端返回的 <code>results</code> 为空。</template>
                <template v-else>
                  尚未开始预测：选择文件后点击「开始批量预测」（<code>POST /api/predict/batch</code>）。
                </template>
              </td>
            </tr>
            <tr v-for="row in rows" :key="row.index">
              <td class="num">{{ row.index }}</td>
              <td><span class="truncate" :title="row.text">{{ truncate(row.text, 90) }}</span></td>
              <td><span :class="labelClass(row.label)">{{ labelText(row.label) }}</span></td>
              <td class="num">{{ formatPercent(row.confidence) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card__foot">
        契约响应：<code>{"total":int,"results":[{"index","text","label","confidence"}],"export_url":str}</code>；
        导出文件默认落在 <code>outputs/predictions/</code>。
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 连接类错误附带的「启动后端」提示另起一行，避免和错误原因挤成一段 */
.backend-hint {
  display: block;
  margin-top: 4px;
}
</style>
