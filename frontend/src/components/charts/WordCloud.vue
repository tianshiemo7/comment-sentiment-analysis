<script setup>
/**
 * components/charts/WordCloud.vue — 关键词词云（echarts-wordcloud）
 * 字号由 count 控制（契约：词云直接用 count 或 weight 渲染，后端不生成图片）。
 */
import { computed } from 'vue'
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
})

const PALETTE = {
  positive: ['#15803d', '#16a34a', '#22c55e', '#4ade80', '#166534'],
  negative: ['#b91c1c', '#dc2626', '#ef4444', '#f87171', '#7f1d1d'],
}

const hasData = computed(() => Array.isArray(props.words) && props.words.length >= props.minWords)

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
      sizeRange: [13, 40],
      rotationRange: [0, 0], // 中文横向排布更易读
      gridSize: 6,
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
</script>

<template>
  <div class="chart-card">
    <div class="chart-card__head">
      <h3><Icon name="cloud-line" :size="16" /> {{ tone === 'negative' ? '负面关键词词云' : '正面关键词词云' }}</h3>
      <span class="card__hint">{{ words.length }} 个词</span>
    </div>
    <EChart
      v-if="hasData"
      :option="option"
      height="260px"
      :aria-label="tone === 'negative' ? '负面关键词词云' : '正面关键词词云'"
    />
    <p v-else class="chart-card__empty">关键词不足，暂不渲染词云</p>
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
</style>
