<script setup>
/**
 * components/charts/KeywordBar.vue — 关键词排行 Top N（横向条形图）
 * 正/负关键词合并后按 count 排序，用颜色区分极性。
 */
import { computed } from 'vue'
import EChart from './EChart.vue'
import Icon from '../Icon.vue'
import { formatNumber } from '../../utils/videoFormat.js'

const props = defineProps({
  /** { positive: [{word,count,weight}], negative: [...] } */
  keywords: { type: Object, default: () => ({ positive: [], negative: [] }) },
  limit: { type: Number, default: 15 },
})

const rows = computed(() => {
  const merged = [
    ...(props.keywords?.positive || []).map((item) => ({ ...item, tone: 'positive' })),
    ...(props.keywords?.negative || []).map((item) => ({ ...item, tone: 'negative' })),
  ]
  return merged.sort((a, b) => b.count - a.count).slice(0, props.limit)
})

const hasData = computed(() => rows.value.length > 0)

const option = computed(() => {
  // 横向条形图：yAxis 逆序，让最大值显示在最上方
  const ordered = [...rows.value].reverse()
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const item = params[0]
        const tone = ordered[item.dataIndex]?.tone === 'negative' ? '负面' : '正面'
        return `${item.name}<br/>${tone}：${formatNumber(item.value)} 次`
      },
    },
    grid: { left: 8, right: 44, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef2f7' } } },
    yAxis: {
      type: 'category',
      data: ordered.map((item) => item.word),
      axisTick: { show: false },
      axisLabel: { fontSize: 12 },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 14,
        label: { show: true, position: 'right', fontSize: 11, color: '#64748b' },
        data: ordered.map((item) => ({
          value: item.count,
          itemStyle: { color: item.tone === 'negative' ? '#ef4444' : '#22c55e', borderRadius: [0, 4, 4, 0] },
        })),
      },
    ],
  }
})
</script>

<template>
  <div class="chart-card chart-card--tall">
    <div class="chart-card__head">
      <h3><Icon name="bar-chart-horizontal-line" :size="16" /> 关键词排行 Top {{ limit }}</h3>
      <span class="card__hint">正/负合并按出现次数排序</span>
    </div>
    <EChart v-if="hasData" :option="option" height="380px" aria-label="关键词排行榜" />
    <p v-else class="chart-card__empty">暂无关键词数据</p>
  </div>
</template>

<style scoped>
.chart-card__empty {
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  padding: 40px 0;
}
</style>
