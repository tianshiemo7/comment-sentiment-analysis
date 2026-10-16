<script setup>
import { computed, onMounted, ref } from 'vue'
import { getModels } from '../api/client.js'
import { formatMetric } from '../utils/format.js'

/** idle | loading | ok | error */
const state = ref('idle')
const models = ref([])
const error = ref('')

const rows = computed(() => (Array.isArray(models.value) ? models.value : []))

/** 仅用于高亮真实响应中指标最佳的模型，无数据时不计算、不填充。 */
const bestAccuracy = computed(() => {
  const values = rows.value
    .map((m) => Number(m && m.metrics && m.metrics.accuracy))
    .filter((v) => Number.isFinite(v))
  return values.length ? Math.max(...values) : null
})

async function load() {
  state.value = 'loading'
  error.value = ''
  try {
    const data = await getModels()
    models.value = data && Array.isArray(data.models) ? data.models : []
    state.value = 'ok'
  } catch (err) {
    models.value = []
    error.value = err.message
    state.value = 'error'
  }
}

onMounted(load)
</script>

<template>
  <section class="stack">
    <div class="page-head">
      <h1>模型对比</h1>
      <p>
        调用 <code>GET /api/models</code> 读取已训练模型的评估指标，对比不同模型的
        Accuracy / Precision / Recall / F1。
      </p>
    </div>

    <div class="card">
      <div class="card__head">
        <h2>模型指标对比表</h2>
        <span class="card__hint">
          <template v-if="state === 'ok'">共 {{ rows.length }} 个模型</template>
          <template v-else>响应字段：models[].metrics</template>
        </span>
      </div>

      <div v-if="state === 'error'" class="hint hint--error" style="margin-bottom: 12px">
        <strong>待接入后端</strong>：{{ error }}
      </div>
      <div v-else-if="state === 'loading'" class="hint" style="margin-bottom: 12px">
        正在请求 GET /api/models …
      </div>
      <div v-else-if="state === 'ok' && rows.length === 0" class="hint hint--warn" style="margin-bottom: 12px">
        <strong>待接入后端</strong>：后端返回的 <code>models</code> 为空数组，模型训练完成后此处会自动出现数据。
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 200px">模型名</th>
              <th style="width: 150px">类型</th>
              <th style="width: 110px">Accuracy</th>
              <th style="width: 110px">Precision</th>
              <th style="width: 110px">Recall</th>
              <th style="width: 110px">F1</th>
              <th style="width: 100px">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="rows.length === 0">
              <td class="empty-cell" colspan="7">
                <template v-if="state === 'idle' || state === 'loading'">正在加载…</template>
                <template v-else>
                  暂无数据 —— 待接入后端（<code>GET /api/models</code>）
                </template>
              </td>
            </tr>
            <tr v-for="model in rows" :key="model.name">
              <td>
                {{ model.name }}
                <span
                  v-if="bestAccuracy !== null && Number(model.metrics && model.metrics.accuracy) === bestAccuracy"
                  class="badge badge--ready"
                  style="margin-left: 6px"
                >
                  最优 Accuracy
                </span>
              </td>
              <td>{{ model.type || '—' }}</td>
              <td class="num">{{ formatMetric(model.metrics && model.metrics.accuracy) }}</td>
              <td class="num">{{ formatMetric(model.metrics && model.metrics.precision) }}</td>
              <td class="num">{{ formatMetric(model.metrics && model.metrics.recall) }}</td>
              <td class="num">{{ formatMetric(model.metrics && model.metrics.f1) }}</td>
              <td>
                <span :class="model.ready ? 'badge badge--ready' : 'badge'">
                  {{ model.ready ? '已就绪' : '未训练' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="actions" style="margin-top: 14px">
        <button type="button" class="btn" :disabled="state === 'loading'" @click="load">刷新指标</button>
      </div>

      <div class="card__foot">
        契约响应：<code>{"models":[{"name","type","metrics":{"accuracy","precision","recall","f1"},"ready"}]}</code>；
        混淆矩阵等图表见「可视化」页。
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card__head">
          <h3>后续接入说明</h3>
        </div>
        <ul class="list-plain">
          <li>本页当前<strong>不展示任何模拟指标</strong>，未启动后端时显示「待接入后端」。</li>
          <li>指标由 Python 端评估模块写入（<code>outputs/stats/</code>）并由 FastAPI 暴露。</li>
          <li>指标对比条形图按课程安排后续用 ECharts 接入，本骨架不引入图表库。</li>
        </ul>
      </div>
      <div class="card">
        <div class="card__head">
          <h3>指标含义</h3>
        </div>
        <dl class="kv">
          <dt>Accuracy</dt>
          <dd>预测正确的样本占比</dd>
          <dt>Precision</dt>
          <dd>预测为正的样本中真正为正的比例</dd>
          <dt>Recall</dt>
          <dd>真正为正的样本中被召回的比例</dd>
          <dt>F1</dt>
          <dd>Precision 与 Recall 的调和平均</dd>
        </dl>
      </div>
    </div>
  </section>
</template>
