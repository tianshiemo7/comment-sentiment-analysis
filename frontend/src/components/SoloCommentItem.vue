<script setup>
/**
 * components/SoloCommentItem.vue — 左栏②段「单独评论」条目
 * 展示：截断后的原文 + 极性徽章 + 置信度；支持单条删除。
 */
import { computed } from 'vue'
import SentimentBadge from './SentimentBadge.vue'
import Icon from './Icon.vue'
import { formatPercent } from '../utils/format.js'
import { truncate } from '../utils/videoFormat.js'

const props = defineProps({
  item: { type: Object, required: true },
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'remove'])

const preview = computed(() => truncate(props.item.text || '', 42))
const confidence = computed(() => formatPercent(props.item.confidence))
</script>

<template>
  <li
    class="solo-item"
    :class="[`solo-item--${item.label || 'unknown'}`, { 'is-active': active }]"
    tabindex="0"
    role="button"
    :aria-pressed="active"
    @click="emit('select', item.id)"
    @keydown.enter.prevent="emit('select', item.id)"
    @keydown.space.prevent="emit('select', item.id)"
  >
    <div class="solo-item__top">
      <p class="solo-item__text" :title="item.text">{{ preview }}</p>
      <button
        type="button"
        class="icon-btn icon-btn--danger solo-item__remove"
        title="删除这条评论"
        aria-label="删除这条评论"
        @click.stop="emit('remove', item.id)"
      >
        <Icon name="delete-bin-6-line" :size="14" />
      </button>
    </div>
    <div class="solo-item__meta">
      <SentimentBadge :label="item.label" compact />
      <span class="solo-item__conf mono">{{ confidence }}</span>
    </div>
  </li>
</template>

<style scoped>
.solo-item {
  position: relative;
  list-style: none;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.solo-item:hover {
  background: var(--surface-alt);
}
.solo-item.is-active {
  background: var(--primary-soft);
  border-color: #bfdbfe;
  border-left-color: var(--primary);
}
.solo-item:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.35);
  outline-offset: 1px;
}
.solo-item--positive {
  border-left-color: #16a34a;
}
.solo-item--negative {
  border-left-color: #dc2626;
}
.solo-item--neutral {
  border-left-color: #94a3b8;
}
.solo-item__top {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.solo-item__text {
  flex: 1 1 auto;
  margin: 0;
  font-size: 13px;
  line-height: 1.45;
  word-break: break-word;
}
.solo-item__remove {
  /* 尺寸与悬停态由全局 .icon-btn / .icon-btn--danger 提供 */
  flex: 0 0 auto;
  margin: -2px -4px 0 0;
}
.solo-item__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.solo-item__conf {
  font-size: 11.5px;
  color: var(--text-muted);
}
</style>
