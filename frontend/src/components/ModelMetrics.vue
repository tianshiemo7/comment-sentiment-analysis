<script setup>
/**
 * components/ModelMetrics.vue — 模型指标卡片
 * 字段来自契约 detail.model：{ name, accuracy, precision, recall, f1 }
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { formatMetric } from '../utils/videoFormat.js'

const props = defineProps({
  model: { type: Object, default: null },
})

const metrics = computed(() => {
  const model = props.model || {}
  return [
    { key: 'accuracy', label: 'Accuracy', value: formatMetric(model.accuracy) },
    { key: 'precision', label: 'Precision', value: formatMetric(model.precision) },
    { key: 'recall', label: 'Recall', value: formatMetric(model.recall) },
    { key: 'f1', label: 'F1', value: formatMetric(model.f1) },
  ]
})
</script>

<template>
  <div class="model-card">
    <div class="chart-card__head">
      <h3><Icon name="cpu-line" :size="16" /> 模型指标</h3>
      <span class="card__hint">当前生效模型</span>
    </div>
    <p class="model-card__name">
      <span class="badge mono">{{ model?.name || '未提供' }}</span>
    </p>
    <div class="model-card__grid">
      <div v-for="metric in metrics" :key="metric.key" class="metric">
        <span class="metric__label">{{ metric.label }}</span>
        <strong class="metric__value">{{ metric.value }}</strong>
      </div>
    </div>
  </div>
</template>

<style scoped>
.model-card__name {
  margin: 0 0 10px;
}
.model-card__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 10px;
}
.metric {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-alt);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.metric__label {
  font-size: 12px;
  color: var(--text-muted);
}
.metric__value {
  font-family: var(--mono);
  font-size: 17px;
  color: var(--primary-dark);
}
</style>
