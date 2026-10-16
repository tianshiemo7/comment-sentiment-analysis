<script setup>
/**
 * components/ProbBars.vue — 概率条（正面/负面/中性）
 * 契约字段：probs 为对象（如 {"positive":0.8084,"negative":0.1916}），键由后端给出，前端按对象动态渲染。
 */
import { computed } from 'vue'
import { SENTIMENT_COLORS, sentimentText } from '../utils/sentimentLabels.js'
import { formatPercent, percentWidth } from '../utils/format.js'

const props = defineProps({
  probs: { type: Object, default: null },
  compact: { type: Boolean, default: false },
})

const ORDER = ['positive', 'negative', 'neutral']

const items = computed(() => {
  const probs = props.probs || {}
  return Object.keys(probs)
    .sort((a, b) => {
      const ia = ORDER.indexOf(a)
      const ib = ORDER.indexOf(b)
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
    })
    .map((key) => ({
      key,
      name: sentimentText(key),
      value: Number(probs[key]) || 0,
      color: SENTIMENT_COLORS[key] || '#94a3b8',
    }))
})
</script>

<template>
  <div class="prob-bars" :class="{ 'prob-bars--compact': compact }">
    <p v-if="!items.length" class="prob-bars__empty">无概率数据</p>
    <div v-for="item in items" :key="item.key" class="prob-bars__row">
      <span class="prob-bars__name">{{ item.name }}</span>
      <span class="prob-bars__bar">
        <span class="prob-bars__fill" :style="{ width: percentWidth(item.value), background: item.color }"></span>
      </span>
      <span class="prob-bars__value mono">{{ formatPercent(item.value) }}</span>
    </div>
  </div>
</template>

<style scoped>
.prob-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.prob-bars__row {
  display: grid;
  grid-template-columns: 52px 1fr 62px;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.prob-bars--compact .prob-bars__row {
  grid-template-columns: 44px 1fr 56px;
  font-size: 12px;
}
.prob-bars__name {
  color: var(--text-muted);
}
.prob-bars__bar {
  height: 9px;
  border-radius: 999px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  overflow: hidden;
}
.prob-bars__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}
.prob-bars__value {
  text-align: right;
  color: var(--text-muted);
}
.prob-bars__empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-muted);
}
</style>
