<script setup>
/**
 * components/charts/SentimentPie.vue — 情感占比饼图
 * 数据来源：契约 detail.sentiment（count 与 *_ratio 均为后端/mock 提供，前端只做展示）。
 */
import { computed } from 'vue'
import EChart from './EChart.vue'
import { formatNumber, formatRatio } from '../../utils/videoFormat.js'

const props = defineProps({
  sentiment: { type: Object, default: null },
})

const hasData = computed(() => Number(props.sentiment?.total) > 0)

const option = computed(() => {
  const s = props.sentiment || {}
  const data = [
    { name: '正面', value: s.positive ?? 0, itemStyle: { color: '#16a34a' } },
    { name: '负面', value: s.negative ?? 0, itemStyle: { color: '#dc2626' } },
    { name: '中性', value: s.neutral ?? 0, itemStyle: { color: '#94a3b8' } },
  ]
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p) => `${p.name}：${formatNumber(p.value)} 条（${p.percent}%）`,
    },
    // 环心写总条数，外圈颜色对应图例，具体比例由下方比例行给出
    title: {
      text: formatNumber(s.total),
      subtext: '条评论',
      left: '50%',
      top: '34%',
      textAlign: 'center',
      textStyle: { fontSize: 16, fontWeight: 600, color: '#1f2937' },
      subtextStyle: { fontSize: 11, color: '#64748b' },
    },
    legend: { bottom: 0, icon: 'circle' },
    series: [
      {
        type: 'pie',
        radius: ['44%', '70%'],
        center: ['50%', '46%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        // 不在扇区上写字：详情区宽度随左栏收起/展开变化，窄栏里文字标签会被裁切；
        // 名称由图例给出、数值由下方比例行给出，任何宽度下都不会截断。
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: true, scaleSize: 4 },
        data,
      },
    ],
  }
})

/** 图例下方的比例文字（也方便无图表环境下核对数据） */
const ratios = computed(() => {
  const s = props.sentiment || {}
  return [
    { key: '正面', value: formatRatio(s.positive_ratio), color: '#16a34a' },
    { key: '负面', value: formatRatio(s.negative_ratio), color: '#dc2626' },
    { key: '中性', value: formatRatio(s.neutral_ratio), color: '#94a3b8' },
  ]
})
</script>

<template>
  <div class="chart-card">
    <div class="chart-card__head">
      <h3>情感占比</h3>
      <span class="card__hint">共 {{ formatNumber(sentiment?.total) }} 条</span>
    </div>
    <EChart v-if="hasData" :option="option" height="280px" aria-label="情感占比饼图" />
    <p v-else class="chart-card__empty">暂无情感数据</p>
    <ul v-if="hasData" class="sentiment-ratios">
      <li v-for="item in ratios" :key="item.key">
        <span class="dot" :style="{ background: item.color }"></span>{{ item.key }}
        <strong>{{ item.value }}</strong>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.chart-card__empty {
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  padding: 40px 0;
}
.sentiment-ratios {
  display: flex;
  gap: 16px;
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  font-size: 13px;
  color: var(--text-muted);
}
.sentiment-ratios .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}
.sentiment-ratios strong {
  color: var(--text);
  margin-left: 4px;
}
</style>
