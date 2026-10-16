<script setup>
/**
 * components/VideoStatusBadge.vue — 状态徽章
 * 文案与色调完全来自 utils/videoFormat.js 的 STATUS_META（对应契约第一节）。
 */
import { computed } from 'vue'
import { statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  status: { type: String, default: '' },
})

const meta = computed(() => statusMeta(props.status))
</script>

<template>
  <span class="status-badge" :class="`status-badge--${meta.tone}`">
    <span class="status-badge__dot"></span>{{ meta.label }}
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
.status-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
/* 契约配色：排队中灰 / 爬取中蓝 / 处理中橙 / 已完成绿 / 失败红 */
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
