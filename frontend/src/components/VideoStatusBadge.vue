<script setup>
/**
 * components/VideoStatusBadge.vue — 状态徽章
 * 文案与色调完全来自 utils/videoFormat.js 的 STATUS_META（对应契约第一节 + 分步流水线等待态）。
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  status: { type: String, default: '' },
})

const meta = computed(() => statusMeta(props.status))

/**
 * 状态 → 图标：与 STATUS_META 的 tone 一一对应。
 * 原先用 6px 色点表意，色点只能靠颜色区分；换成图标后形状本身也能区分状态，
 * 且图标用 currentColor 继承徽章文字色，配色仍是契约里的那一套。
 * crawled（待清洗）用橡皮擦、cleaned（待分析）用心跳线，与两个等待态的动作语义对应。
 */
const STATUS_ICONS = {
  queued: 'time-line',
  crawling: 'download-cloud-2-line',
  processing: 'loader-4-line',
  crawled: 'eraser-line',
  cleaned: 'heart-pulse-line',
  completed: 'checkbox-circle-line',
  failed: 'error-warning-line',
}

const icon = computed(() => STATUS_ICONS[meta.value.tone] || 'question-line')
/** 只有「处理中」旋转，其余状态是静态图标 */
const spinning = computed(() => meta.value.tone === 'processing')
</script>

<template>
  <span class="status-badge" :class="`status-badge--${meta.tone}`">
    <Icon :name="icon" :size="13" :spin="spinning" />
    {{ meta.label }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12.5px;
  border: 1px solid var(--border);
  background: var(--surface-alt);
  color: var(--text-muted);
  white-space: nowrap;
}
/* 契约配色：排队中灰 / 爬取中蓝 / 处理中橙 / 待清洗琥珀 / 待分析紫 / 已完成绿 / 失败红 */
.status-badge--queued {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}
.status-badge--crawling {
  background: #eff6ff;
  border-color: #bfdbfe;
  color: #1d4ed8;
}
.status-badge--processing {
  background: #fff7ed;
  border-color: #fed7aa;
  color: #c2410c;
}
/* 分步流水线等待态：与「处理中」的橙色区分开，避免误以为仍在自动跑 */
.status-badge--crawled {
  background: #fffbeb;
  border-color: #fde68a;
  color: #b45309;
}
.status-badge--cleaned {
  background: #f5f3ff;
  border-color: #ddd6fe;
  color: #6d28d9;
}
.status-badge--completed {
  background: var(--positive-soft);
  border-color: #bbf7d0;
  color: var(--positive);
}
.status-badge--failed {
  background: var(--negative-soft);
  border-color: #fecaca;
  color: var(--negative);
}
</style>
