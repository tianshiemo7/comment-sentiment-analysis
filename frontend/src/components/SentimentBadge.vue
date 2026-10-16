<script setup>
/**
 * components/SentimentBadge.vue — 情感极性徽章（正面/负面/中性）
 * 契约字段：label（positive | negative | neutral）。
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { sentimentText, sentimentTone } from '../utils/sentimentLabels.js'

const props = defineProps({
  label: { type: String, default: '' },
  /** 紧凑模式（列表内使用） */
  compact: { type: Boolean, default: false },
})

const tone = computed(() => sentimentTone(props.label))
const text = computed(() => sentimentText(props.label))

/** 极性 → 表情图标：正/负/中性/未知四种，颜色仍由徽章的 tone 类决定 */
const SENTIMENT_ICONS = {
  positive: 'emotion-happy-line',
  negative: 'emotion-unhappy-line',
  neutral: 'emotion-normal-line',
  unknown: 'question-line',
}

const icon = computed(() => SENTIMENT_ICONS[tone.value] || SENTIMENT_ICONS.unknown)
</script>

<template>
  <span class="s-badge" :class="[`s-badge--${tone}`, { 's-badge--compact': compact }]">
    <Icon :name="icon" :size="compact ? 12 : 13" />
    {{ text }}
  </span>
</template>

<style scoped>
.s-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 9px;
  border-radius: 999px;
  font-size: 12.5px;
  border: 1px solid var(--border);
  background: var(--surface-alt);
  color: var(--text-muted);
  white-space: nowrap;
}
.s-badge--compact {
  font-size: 11.5px;
  padding: 0 7px;
}
.s-badge--positive {
  background: var(--positive-soft);
  border-color: #bbf7d0;
  color: var(--positive);
}
.s-badge--negative {
  background: var(--negative-soft);
  border-color: #fecaca;
  color: var(--negative);
}
.s-badge--neutral {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}
</style>
