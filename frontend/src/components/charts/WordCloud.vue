<script setup>
/**
 * components/charts/WordCloud.vue — 关键词词云（echarts-wordcloud + 可切换 Python 渲染图）
 *
 * 契约（2026-11-04 导出与词云设计 4.2）：
 *   - 默认仍为前端 ECharts 渲染（既有行为不变）；
 *   - 传入 imageUrl（非空）时卡片头显示「前端渲染 | Python 图」切换；
 *     切到 Python 图后懒加载 <img>，加载失败展示中文原因并自动回退前端渲染；
 *   - Python 模式提供「下载图片」链接（imageDownloadUrl 带 &download=true）；
 *   - 词量提升到每侧 60 后字号自适应：>15 词 10–30px，否则 13–40px；gridSize 5，高度 320px。
 *
 * 字号由 count 控制（契约：词云直接用 count 或 weight 渲染，后端不生成图片）。
 */
import { computed, ref, watch } from 'vue'
import EChart from './EChart.vue'
import Icon from '../Icon.vue'
import { formatNumber } from '../../utils/videoFormat.js'

const props = defineProps({
  /** [{ word, count, weight }] */
  words: { type: Array, default: () => [] },
  /** positive | negative，仅用于配色 */
  tone: { type: String, default: 'positive' },
  /** 词云最少渲染词量，低于此值提示数据不足 */
  minWords: { type: Number, default: 3 },
  /** Python 渲染的 PNG 地址（含 ?tone=&v=）；为空时不显示渲染源切换 */
  imageUrl: { type: String, default: '' },
  /** Python 图下载地址（含 &download=true） */
  imageDownloadUrl: { type: String, default: '' },
})

const PALETTE = {
  positive: ['#15803d', '#16a34a', '#22c55e', '#4ade80', '#166534'],
  negative: ['#b91c1c', '#dc2626', '#ef4444', '#f87171', '#7f1d1d'],
}

/** 渲染源：frontend = ECharts 词云；python = 后端 wordcloud 库生成的 PNG */
const source = ref('frontend')
/** Python 图片加载状态：idle | loading | ready | error */
const imageState = ref('idle')
/** Python 图加载失败的中文原因（含后端 detail） */
const imageError = ref('')

const hasImage = computed(() => Boolean(props.imageUrl))
const hasData = computed(() => Array.isArray(props.words) && props.words.length >= props.minWords)

/** 词量越多字号越小：60 词时 10–30px，避免重叠；15 词以内保持大字号更醒目。 */
const sizeRange = computed(() => (props.words.length > 15 ? [10, 30] : [13, 40]))

const colorOf = (index) => {
  const palette = PALETTE[props.tone] || PALETTE.positive
  return palette[index % palette.length]
}

const option = computed(() => ({
  tooltip: { formatter: (p) => `${p.name}：${formatNumber(p.value)} 次` },
  series: [
    {
      type: 'wordCloud',
      shape: 'circle',
      left: 'center',
      top: 'center',
      width: '96%',
      height: '92%',
      sizeRange: sizeRange.value,
      rotationRange: [0, 0], // 中文横向排布更易读
      gridSize: 5,
      drawOutOfBound: false,
      textStyle: {
        fontFamily: 'system-ui, "Microsoft YaHei", sans-serif',
        fontWeight: 600,
        color: () => colorOf(Math.floor(Math.random() * 5)),
      },
      emphasis: { textStyle: { fontWeight: 'bold' } },
      data: props.words.map((item) => ({ name: item.word, value: item.count })),
    },
  ],
}))

/** 重新分析后关键词变化 → imageUrl 的 v 参数变化：回到前端渲染，避免展示旧图。 */
watch(
  () => props.imageUrl,
  () => {
    source.value = 'frontend'
    imageState.value = 'idle'
    imageError.value = ''
  },
)

function showFrontend() {
  source.value = 'frontend'
  imageState.value = 'idle'
  imageError.value = ''
}

/** 切到 Python 图：每次都重新挂载 <img>（v-if），失败后再次点击即重试。 */
function showPython() {
  if (!hasImage.value) return
  imageError.value = ''
  imageState.value = 'loading'
  source.value = 'python'
}

function onImageLoad() {
  if (source.value === 'python') imageState.value = 'ready'
}

/**
 * 图片加载失败：尽力从图片接口读取中文 detail（后端 404/503/500 都是 JSON detail），
 * 提示原因后自动回退到前端渲染，绝不让卡片变成破图。
 */
async function onImageError() {
  imageState.value = 'error'
  const url = props.imageUrl
  let reason = ''
  if (url) {
    try {
      const response = await fetch(url)
      if (!response.ok) {
        try {
          const data = await response.json()
          if (data && data.detail !== undefined) {
            reason = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
          }
        } catch {
          reason = ''
        }
        if (!reason) reason = `图片接口返回 HTTP ${response.status}`
      }
    } catch (err) {
      reason = err?.message || ''
    }
  }
  // 等待期间用户可能已切回前端渲染 / 换了一张图，此时不再覆盖状态
  if (props.imageUrl !== url) return
  imageError.value = reason || '图片接口不可用（网络错误或该情感暂无足够关键词）'
  source.value = 'frontend'
  imageState.value = 'idle'
}
</script>

<template>
  <div class="chart-card">
    <div class="chart-card__head">
      <h3><Icon name="cloud-line" :size="16" /> {{ tone === 'negative' ? '负面关键词词云' : '正面关键词词云' }}</h3>
      <div class="chart-card__head-right">
        <span class="card__hint">{{ words.length }} 个词</span>
        <div v-if="hasImage" class="source-switch" role="group" aria-label="词云渲染方式">
          <button
            type="button"
            class="source-switch__item"
            :class="{ 'is-active': source === 'frontend' }"
            :aria-pressed="source === 'frontend'"
            @click="showFrontend"
          >
            前端渲染
          </button>
          <button
            type="button"
            class="source-switch__item"
            :class="{ 'is-active': source === 'python' }"
            :aria-pressed="source === 'python'"
            title="由 Python wordcloud 库按真实词频渲染 PNG"
            @click="showPython"
          >
            Python 图
          </button>
        </div>
      </div>
    </div>

    <!-- Python 渲染图：懒加载，加载失败自动回退到下面的 ECharts -->
    <template v-if="source === 'python' && hasImage">
      <div class="wordcloud-image">
        <img
          v-if="imageState !== 'error'"
          :src="imageUrl"
          :alt="tone === 'negative' ? '负面关键词 Python 词云图' : '正面关键词 Python 词云图'"
          class="wordcloud-image__img"
          loading="lazy"
          @load="onImageLoad"
          @error="onImageError"
        />
        <div v-if="imageState === 'loading'" class="wordcloud-image__state">
          <Icon name="loader-4-line" :size="17" spin />
          正在加载 Python 渲染的图片…
        </div>
        <p v-else-if="imageState === 'error'" class="wordcloud-image__state">Python 图加载失败，正在回退前端渲染…</p>
      </div>
      <div class="wordcloud-image__foot">
        <a v-if="imageDownloadUrl" class="btn wordcloud-image__download" :href="imageDownloadUrl" download>
          <Icon name="download-2-line" :size="14" />
          下载图片
        </a>
        <span class="card__hint">PNG 由 Python wordcloud 库按真实词频生成</span>
      </div>
    </template>

    <EChart
      v-else-if="hasData"
      :option="option"
      height="320px"
      :aria-label="tone === 'negative' ? '负面关键词词云' : '正面关键词词云'"
    />
    <p v-else class="chart-card__empty">关键词不足，暂不渲染词云</p>

    <p v-if="imageError" class="hint hint--warn wordcloud-image__error">
      Python 图不可用：{{ imageError }}（已回退前端渲染）
    </p>
  </div>
</template>

<style scoped>
.card__hint {
  color: var(--text-muted);
  font-size: 13px;
}
.chart-card__empty {
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  padding: 40px 0;
}
.chart-card__head-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
/* 「前端渲染 | Python 图」分段切换（延续现有胶囊按钮风格） */
.source-switch {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: var(--surface-alt);
}
.source-switch__item {
  appearance: none;
  font: inherit;
  font-size: 12.5px;
  padding: 2px 10px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
}
.source-switch__item:hover {
  color: var(--primary);
}
.source-switch__item.is-active {
  background: var(--surface);
  color: var(--primary-dark);
  font-weight: 600;
  box-shadow: var(--shadow);
}
/* Python 图容器固定高度，与前端渲染的 320px 对齐，避免切换时页面跳动 */
.wordcloud-image {
  position: relative;
  height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--surface-alt);
  border: 1px dashed var(--border);
}
.wordcloud-image__img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.wordcloud-image__state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  color: var(--text-muted);
  font-size: 13px;
  background: var(--surface-alt);
}
.wordcloud-image__foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.wordcloud-image__download {
  padding: 3px 10px;
  font-size: 12.5px;
  text-decoration: none;
}
.wordcloud-image__error {
  margin: 10px 0 0;
  font-size: 12.5px;
}
</style>
