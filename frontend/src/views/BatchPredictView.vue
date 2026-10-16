<script setup>
import { computed, ref } from 'vue'
import { predictBatch } from '../api/client.js'
import { formatPercent, labelClass, labelText, truncate } from '../utils/format.js'

const ALLOWED_EXT = ['.csv', '.xlsx']

const file = ref(null)
const fileError = ref('')
const loading = ref(false)
/** 结果只来自后端响应；未接后端时保持为 null。 */
const result = ref(null)
const error = ref('')
/** idle | loading | ok | error */
const state = ref('idle')

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
        <h2>文件上传</h2>
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

        <div v-if="fileError" class="hint hint--error" style="margin-bottom: 12px">{{ fileError }}</div>

        <div class="actions">
          <button type="submit" class="btn btn--primary" :disabled="!file || loading">
            {{ loading ? '上传中…' : '开始批量预测' }}
          </button>
          <button type="button" class="btn" :disabled="loading" @click="resetInput">重置</button>
        </div>
      </form>

      <div class="card__foot">
        文件约定：文本列字段名为 <code>text</code>（见仓库根 README「数据格式约定」一节与
        <code>data/sample/</code> 样例）。
      </div>
    </div>

    <div class="card">
      <div class="card__head">
        <h2>批量结果</h2>
        <span class="card__hint">
          <template v-if="result">共 {{ result.total }} 条</template>
          <template v-else>响应字段：total / results / export_url</template>
        </span>
      </div>

      <div v-if="state === 'error'" class="hint hint--error">
        <strong>待接入后端</strong>：{{ error }}
      </div>

      <div v-else-if="state === 'loading'" class="hint">正在上传并请求后端，请稍候…</div>

      <div v-else-if="state === 'idle'" class="hint hint--warn">
        <strong>待接入后端</strong>：尚未发起批量预测请求。启动 FastAPI 服务并上传文件后即可看到真实结果；
        骨架阶段不展示任何模拟数据。
      </div>

      <div v-else-if="state === 'ok' && rows.length === 0" class="hint hint--warn">
        <strong>待接入后端</strong>：后端返回结果为空（<code>results</code> 为空数组）。
      </div>

      <!-- 结果表格与导出按钮始终渲染（骨架阶段为空态），保证 DOM 结构稳定 -->
      <div class="actions" style="margin: 12px 0">
        <a
          v-if="exportUrl"
          class="btn btn--primary"
          :href="exportUrl"
          download
          style="text-decoration: none"
        >
          导出结果文件
        </a>
        <button v-else type="button" class="btn" disabled>导出结果文件</button>
        <span v-if="!exportUrl" class="card__hint">
          <strong>待接入后端</strong>：响应未提供 <code>export_url</code>，暂无可下载文件。
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
                <template v-else>
                  暂无数据 —— 待接入后端（<code>POST /api/predict/batch</code>）
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
