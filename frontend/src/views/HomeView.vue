<script setup>
import { onMounted, ref } from 'vue'
import { API_CONTRACT, getHealth } from '../api/client.js'
import Icon from '../components/Icon.vue'

const emit = defineEmits(['navigate'])

/** 后端连通性：仅为骨架阶段的自检提示，不展示任何编造的指标数据。 */
const health = ref({ state: 'idle', message: '尚未检查后端连通性。' })

const entries = [
  { key: 'single', title: '单条预测', icon: 'chat-1-line', desc: '输入一条评论，输出情感极性与置信度。', api: 'POST /api/predict' },
  { key: 'batch', title: '批量预测', icon: 'file-list-3-line', desc: '上传 .csv / .xlsx，批量输出结果并导出。', api: 'POST /api/predict/batch' },
  { key: 'visualization', title: '可视化', icon: 'bar-chart-2-line', desc: '训练曲线、混淆矩阵、词云、Attention 权重。', api: '读取 outputs/figures/ 产物' },
  { key: 'models', title: '模型对比', icon: 'stack-line', desc: '对比各模型 Accuracy / Precision / Recall / F1。', api: 'GET /api/models' },
]

async function checkHealth() {
  health.value = { state: 'loading', message: '正在请求 GET /health …' }
  try {
    const data = await getHealth()
    health.value = { state: 'ok', message: `后端已连通：${JSON.stringify(data)}` }
  } catch (err) {
    health.value = { state: 'error', message: err.message }
  }
}

onMounted(() => {
  // 骨架阶段：仅探测一次，失败即提示「待接入后端」，不阻塞界面
  checkHealth()
})
</script>

<template>
  <section class="stack">
    <div class="page-head">
      <h1>网站用户评论情感分析系统</h1>
      <p>
        给定网站用户评论文本（电商评论、影评、App 评论等），自动判断情感极性（正面 / 负面）。
        当前前端为<strong>课程设计骨架</strong>：界面与接口封装已就绪，算法与模型由 Python 端实现。
      </p>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="heart-pulse-line" :size="16" /> 后端连通性自检</h2>
        <span class="card__hint">GET /health → http://localhost:8000</span>
      </div>
      <div
        class="hint hint--icon"
        :class="{
          'hint--ok': health.state === 'ok',
          'hint--error': health.state === 'error',
          'hint--warn': health.state === 'idle' || health.state === 'loading',
        }"
      >
        <Icon
          :name="health.state === 'ok' ? 'checkbox-circle-line' : health.state === 'error' ? 'error-warning-line' : 'loader-4-line'"
          :size="14"
          :spin="health.state === 'loading'"
        />
        <span>
          <template v-if="health.state === 'ok'">后端已连通：{{ health.message }}</template>
          <template v-else-if="health.state === 'error'">
            <strong>待接入后端</strong>：{{ health.message }}
          </template>
          <template v-else>{{ health.message }}</template>
        </span>
      </div>
      <div class="actions" style="margin-top: 12px">
        <button type="button" class="btn" :disabled="health.state === 'loading'" @click="checkHealth">
          <Icon name="refresh-line" :size="14" />
          重新检查
        </button>
      </div>
    </div>

    <div class="grid-2">
      <div v-for="entry in entries" :key="entry.key" class="card">
        <div class="card__head">
          <h3><Icon :name="entry.icon" :size="16" /> {{ entry.title }}</h3>
          <span class="badge mono">{{ entry.api }}</span>
        </div>
        <p class="card__hint" style="margin: 0">{{ entry.desc }}</p>
        <div class="actions" style="margin-top: 12px">
          <button type="button" class="btn btn--primary" @click="emit('navigate', entry.key)">
            <Icon name="arrow-right-s-line" :size="14" />
            打开{{ entry.title }}
          </button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="file-list-3-line" :size="16" /> 接口契约速查（已锁定，不得改名）</h2>
        <span class="card__hint">详见仓库根 README.md「前后端接口契约」一节</span>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 72px">方法</th>
              <th style="width: 190px">路径</th>
              <th>请求</th>
              <th>响应</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in API_CONTRACT" :key="row.method + row.path">
              <td><span class="badge">{{ row.method }}</span></td>
              <td class="mono">{{ row.path }}</td>
              <td class="mono">{{ row.request }}</td>
              <td class="mono">{{ row.response }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card__head">
        <h2><Icon name="database-2-line" :size="16" /> 环境与端口</h2>
      </div>
      <dl class="kv">
        <dt>前端开发服务</dt>
        <dd class="mono">http://localhost:3000（npm run dev，端口固定）</dd>
        <dt>后端推理服务</dt>
        <dd class="mono">http://localhost:8000（FastAPI，uvicorn）</dd>
        <dt>代理规则</dt>
        <dd class="mono">/api、/health → http://localhost:8000（vite.config.js）</dd>
        <dt>生产构建</dt>
        <dd class="mono">npm run build → frontend/dist/</dd>
      </dl>
    </div>
  </section>
</template>
