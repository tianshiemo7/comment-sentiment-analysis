<script setup>
/**
 * components/charts/TrendLine.vue — 情感趋势折线图
 * 横轴直接使用契约 trend[].bucket 字符串（按天或按小时都无需改前端）。
 */
import { computed } from 'vue'
import EChart from './EChart.vue'

const props = defineProps({
  trend: { type: Array, default: () => [] },
})

const hasData = computed(() => Array.isArray(props.trend) && props.trend.length > 0)

const option = computed(() => {
  const buckets = props.trend.map((item) => item.bucket)
  const pick = (key) => props.trend.map((item) => item[key] ?? 0)
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, icon: 'roundRect' },
    grid: { left: 48, right: 16, top: 16, bottom: 44, containLabel: false },
    xAxis: {
      type: 'category',
      data: buckets,
      boundaryGap: false,
      axisLabel: { fontSize: 11, formatter: (value) => String(value).slice(5) },
    },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      { name: '正面', type: 'line', smooth: true, symbolSize: 5, data: pick('positive'), itemStyle: { color: '#16a34a' }, areaStyle: { opacity: 0.08 } },
      { name: '负面', type: 'line', smooth: true, symbolSize: 5, data: pick('negative'), itemStyle: { color: '#dc2626' }, areaStyle: { opacity: 0.08 } },
      { name: '中性', type: 'line', smooth: true, symbolSize: 5, data: pick('neutral'), itemStyle: { color: '#94a3b8' } },
    ],
  }
})
</script>

<template>
  <div class="chart-card">
    <div class="chart-card__head">
      <h3>情感趋势</h3>
      <span class="card__hint">{{ hasData ? `按 ${trend[0].bucket.length > 10 ? '小时' : '天'} 聚合` : '暂无数据' }}</span>
    </div>
    <EChart v-if="hasData" :option="option" height="300px" aria-label="情感趋势折线图" />
    <p v-else class="chart-card__empty">暂无趋势数据</p>
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
