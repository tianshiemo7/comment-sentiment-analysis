<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getVisualizations } from '../api/client.js'
import Icon from '../components/Icon.vue'

const selectedModel = ref('textcnn')
const state = ref('loading')
const experiment = ref(null)
const error = ref('')
const imageErrors = ref({})
const imageLoaded = ref({})
let requestVersion = 0

const groups = [
  { key: 'training', title: '训练过程', subtitle: '观察收敛速度，以及训练与验证表现之间的差距。' },
  { key: 'evaluation', title: '测试集表现', subtitle: '混淆矩阵中的行是真实类别，列是预测类别。' },
  { key: 'wordcloud', title: '训练语料词云', subtitle: '两种模型共用训练语料，因此词云不随模型变化。' },
  { key: 'attention', title: '注意力分析', subtitle: '可选实验，展示词语对模型预测的贡献。' },
]
const sections = computed(() => groups.map(group => ({ ...group,
  figures: (experiment.value?.figures || []).filter(figure => figure.category === group.key),
})).filter(group => group.figures.length))
const availableCount = computed(() => (experiment.value?.figures || []).filter(figure => figure.available).length)

function percentage(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—'
}
function formatDate(value) {
  const date = new Date(value)
  return !value || Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { hour12: false })
}
async function load() {
  const version = ++requestVersion
  state.value = 'loading'
  error.value = ''
  experiment.value = null
  imageErrors.value = {}
  imageLoaded.value = {}
  try {
    const data = await getVisualizations(selectedModel.value)
    if (version !== requestVersion) return
    experiment.value = data
    state.value = 'ok'
  } catch (err) {
    if (version !== requestVersion) return
    error.value = err.message
    state.value = 'error'
  }
}
function imageFailed(id) { imageErrors.value = { ...imageErrors.value, [id]: true } }
function imageReady(id) { imageLoaded.value = { ...imageLoaded.value, [id]: true } }
onMounted(load)
onBeforeUnmount(() => { requestVersion += 1 })
</script>

<template>
  <section class="stack experiment-view" aria-label="实验可视化">
    <div class="page-head experiment-head">
      <div>
        <p class="experiment-eyebrow">模型实验 · 训练与评估</p>
        <h1>实验可视化</h1>
        <p>查看真实训练曲线、测试集结果与语料分布，支持下载原始图片。</p>
      </div>
      <div class="experiment-controls">
        <label for="experiment-model">选择模型</label>
        <select id="experiment-model" v-model="selectedModel" @change="load">
          <option value="textcnn">TextCNN</option>
          <option value="bilstm_attention">BiLSTM-Attention</option>
        </select>
        <button type="button" class="btn" :disabled="state === 'loading'" @click="load">
          <Icon name="refresh-line" :size="15" /> 刷新图表
        </button>
      </div>
    </div>
    <p class="experiment-note">当前实验来自酒店与外卖公开标注语料，以下指标与词云不代表 B 站评论的实际评估效果。</p>

    <div v-if="state === 'error'" class="experiment-feedback" role="alert">
      <Icon name="error-warning-line" :size="24" />
      <div>
        <h2>暂时无法读取实验结果</h2>
        <p>{{ error }}</p>
        <button type="button" class="btn" @click="load">重试</button>
      </div>
    </div>
    <div v-else-if="state === 'loading'" class="experiment-loading" role="status" aria-live="polite" aria-busy="true">
      <span class="experiment-loading__label">正在读取实验结果…</span>
      <div class="experiment-loading__summary"></div>
      <div class="experiment-grid"><div v-for="index in 2" :key="index" class="experiment-loading__figure"></div></div>
    </div>
    <template v-else-if="experiment">
      <div class="experiment-summary" aria-label="所选模型实验摘要">
        <div class="experiment-summary__model">
          <span>当前模型</span><strong>{{ experiment.model_label }}</strong><small>{{ availableCount }} 张图表可查看</small>
        </div>
        <dl>
          <div><dt>测试准确率</dt><dd>{{ percentage(experiment.metrics?.accuracy) }}</dd></div>
          <div><dt>测试 F1</dt><dd>{{ percentage(experiment.metrics?.f1) }}</dd></div>
          <div><dt>最佳轮次</dt><dd>{{ experiment.best_epoch ?? '—' }}</dd></div>
        </dl>
        <p v-if="experiment.dataset_version" class="experiment-summary__version">数据版本 <span>{{ experiment.dataset_version }}</span></p>
      </div>
      <div v-if="availableCount === 0" class="experiment-empty-note" role="status">当前模型暂无可用图表。完成训练并生成实验图片后，点击「刷新图表」即可查看。</div>
      <section v-for="group in sections" :key="group.key" class="experiment-section" :aria-labelledby="`section-${group.key}`">
        <div class="experiment-section__head"><h2 :id="`section-${group.key}`">{{ group.title }}</h2><p>{{ group.subtitle }}</p></div>
        <div class="experiment-grid" :class="{ 'experiment-grid--single': group.figures.length === 1 }">
          <figure v-for="figure in group.figures" :key="figure.id" class="experiment-figure">
            <figcaption>
              <div><h3>{{ figure.title }}</h3><p>{{ figure.description }}</p></div>
              <a v-if="figure.available" class="experiment-download" :href="figure.download_url" :aria-label="`下载${figure.title}`"><Icon name="download-2-line" :size="16" /> 下载</a>
            </figcaption>
            <div v-if="!figure.available" class="experiment-figure__empty">
              <Icon name="image-line" :size="28" />
              <strong>{{ figure.category === 'attention' ? '尚未生成注意力图' : '尚未生成图表' }}</strong>
              <p>{{ figure.category === 'attention' ? '这是一项可选实验，不影响训练结果和情感预测。' : '生成该模型的实验图片后，点击上方刷新。' }}</p>
            </div>
            <div v-else-if="imageErrors[figure.id]" class="experiment-figure__empty" role="status">
              <Icon name="error-warning-line" :size="28" /><strong>图片暂时无法加载</strong>
              <p>文件可能已更新或移除，请刷新图表后重试。</p><button type="button" class="btn" @click="load">重新加载</button>
            </div>
            <a v-else class="experiment-image" :class="{ 'experiment-image--loading': !imageLoaded[figure.id] }"
               :href="figure.image_url" target="_blank" rel="noopener" :aria-label="`查看${experiment.model_label} ${figure.title}原图`">
              <span v-if="!imageLoaded[figure.id]" class="experiment-image__loading">图片加载中…</span>
              <img :src="figure.image_url" :alt="`${figure.scope === 'shared' ? '训练语料' : experiment.model_label}：${figure.title}`"
                   @load="imageReady(figure.id)" @error="imageFailed(figure.id)" />
            </a>
            <div v-if="figure.available" class="experiment-figure__foot"><span>{{ figure.scope === 'shared' ? '共用训练语料' : experiment.model_label }}</span><time :datetime="figure.updated_at">更新于 {{ formatDate(figure.updated_at) }}</time></div>
          </figure>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.experiment-view { gap: 24px; }
.experiment-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.experiment-head p { margin: 8px 0 0; max-width: 60ch; }
.experiment-head .experiment-eyebrow { margin: 0 0 8px; color: var(--primary); font-size: 12px; font-weight: 600; letter-spacing: .06em; }
.experiment-controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.experiment-controls label { width: auto; color: var(--text-muted); font-size: 13px; }
.experiment-controls select { width: auto; min-width: 180px; min-height: 40px; padding: 8px 30px 8px 12px; color: var(--text); background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-sm); font: inherit; font-size: 14px; }
.experiment-note { margin: 0; padding: 12px 16px; border-left: 3px solid var(--primary); background: var(--primary-soft); color: var(--text-muted); font-size: 13px; }
.experiment-summary { display: grid; grid-template-columns: minmax(180px, 1fr) 2fr; gap: 20px 32px; padding: 24px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.experiment-summary__model { display: grid; gap: 3px; }
.experiment-summary__model > span, .experiment-summary dt, .experiment-summary small { color: var(--text-muted); font-size: 12px; }
.experiment-summary__model strong { font-size: 22px; font-weight: 600; }
.experiment-summary dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin: 0; align-self: center; }
.experiment-summary dd { margin: 5px 0 0; font-size: clamp(22px, 3vw, 30px); font-weight: 600; line-height: 1.25; font-variant-numeric: tabular-nums; }
.experiment-summary__version { grid-column: 1 / -1; margin: 0; padding-top: 12px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 12px; }
.experiment-summary__version span { margin-left: 8px; font-family: var(--mono); overflow-wrap: anywhere; }
.experiment-section { display: grid; gap: 16px; }
.experiment-section__head { display: flex; align-items: baseline; gap: 12px 20px; flex-wrap: wrap; }
.experiment-section__head h2 { font-size: 19px; }
.experiment-section__head p { margin: 0; color: var(--text-muted); font-size: 13px; }
.experiment-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.experiment-grid--single { grid-template-columns: minmax(0, 1fr); }
.experiment-figure { min-width: 0; margin: 0; overflow: hidden; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.experiment-figure figcaption { display: flex; justify-content: space-between; gap: 16px; padding: 18px 20px; align-items: baseline; }
.experiment-figure h3 { font-size: 16px; }
.experiment-figure figcaption p { margin: 5px 0 0; color: var(--text-muted); font-size: 12px; }
.experiment-download { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; font-size: 13px; text-decoration: none; padding: 6px 8px; border-radius: var(--radius-sm); transition: background-color .2s ease; }
.experiment-download:hover { background: var(--primary-soft); }
.experiment-download:active { transform: translateY(1px); }
.experiment-image { display: grid; place-items: center; position: relative; min-height: 160px; padding: 8px; background: #fff; }
.experiment-image img { display: block; width: 100%; max-width: 100%; height: auto; object-fit: contain; }
.experiment-grid--single .experiment-image img { width: auto; max-height: 540px; }
.experiment-image--loading { min-height: 280px; }
.experiment-image__loading { position: absolute; color: var(--text-muted); font-size: 13px; }
.experiment-figure__foot { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; padding: 12px 20px; border-top: 1px solid var(--border); color: var(--text-muted); font-size: 11px; }
.experiment-figure__empty { display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 10px; min-height: 200px; padding: 32px 24px; text-align: center; background: var(--surface-alt); color: var(--text-muted); }
.experiment-figure__empty strong { font-size: 14px; font-weight: 500; }
.experiment-figure__empty p { margin: 0; max-width: 40ch; font-size: 13px; }
.experiment-feedback { display: flex; align-items: flex-start; gap: 16px; padding: 28px; border-radius: var(--radius); border: 1px solid var(--warn-border); background: var(--surface); overflow-wrap: anywhere; }
.experiment-feedback h2 { font-size: 18px; }
.experiment-feedback p { margin: 10px 0 16px; color: var(--text-muted); font-size: 13px; }
.experiment-empty-note { padding: 16px; color: var(--text-muted); background: var(--surface-alt); border: 1px dashed var(--border-strong); border-radius: var(--radius-sm); }
.experiment-loading { display: grid; gap: 20px; }
.experiment-loading__label { font-size: 13px; color: var(--text-muted); }
.experiment-loading__summary, .experiment-loading__figure { border-radius: var(--radius); background: var(--border); animation: experiment-pulse 1.5s ease-in-out infinite alternate; }
.experiment-loading__summary { height: 150px; }
.experiment-loading__figure { height: 330px; }
.experiment-controls select:focus-visible, .experiment-download:focus-visible, .experiment-image:focus-visible { outline: 3px solid var(--primary); outline-offset: 3px; }
@keyframes experiment-pulse { to { opacity: .45; } }
@media (prefers-reduced-motion: reduce) { .experiment-loading__summary, .experiment-loading__figure { animation: none; } .experiment-download { transition: none; } }
@media (max-width: 760px) { .experiment-grid, .experiment-summary { grid-template-columns: minmax(0, 1fr); } .experiment-summary dl { gap: 14px; } .experiment-controls { width: 100%; } .experiment-controls select { flex: 1; min-width: 0; } .experiment-controls label { width: 100%; } .experiment-head { align-items: flex-start; } .experiment-figure figcaption { padding: 16px; } .experiment-figure__foot { padding: 12px 16px; } .experiment-grid--single .experiment-image img { width: 100%; max-height: none; } }
</style>
