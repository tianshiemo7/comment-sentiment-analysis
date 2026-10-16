<script setup>
/**
 * components/ProjectListItem.vue — 左栏①段「数据项目」条目
 * 展示：名称（未命名时后端返回「未自定义数据」）/ 初始行数 → 筛选后结果行数 / 状态徽章 / 进度条 / 删除
 */
import { computed } from 'vue'
import VideoStatusBadge from './VideoStatusBadge.vue'
import Icon from './Icon.vue'
import { formatNumber, isTerminal } from '../utils/videoFormat.js'

const props = defineProps({
  project: { type: Object, required: true },
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'remove'])

const ACTIVE_STATUSES = ['queued', 'processing']

const name = computed(() => props.project.name || '未自定义数据')
const showProgress = computed(() => ACTIVE_STATUSES.includes(props.project.status))
const isFailed = computed(() => props.project.status === 'failed')
const finished = computed(() => isTerminal(props.project.status))
const progress = computed(() => Math.min(100, Math.max(0, Number(props.project.progress) || 0)))
const tone = computed(() => props.project.status || 'queued')

function stageRows(key) {
  const stage = (props.project.stages || []).find((item) => item.key === key)
  return stage ? Number(stage.rows) || 0 : null
}

const initialRows = computed(() => stageRows('initial'))
const cleanRows = computed(() => stageRows('clean'))

/** 契约要求展示「初始 N 行 → 筛选后 M 行」；处理中阶段行数未知（0）时只显示已有值 */
const rowsText = computed(() => {
  const a = initialRows.value
  const b = cleanRows.value
  if (a === null && b === null) return '—'
  return `${formatNumber(a ?? 0)} 行 → ${formatNumber(b ?? 0)} 行`
})
</script>

<template>
  <li
    class="project-item"
    :class="[`project-item--${tone}`, { 'is-active': active, 'is-done': finished }]"
    tabindex="0"
    role="button"
    :aria-pressed="active"
    @click="emit('select', project.id)"
    @keydown.enter.prevent="emit('select', project.id)"
    @keydown.space.prevent="emit('select', project.id)"
  >
    <div class="project-item__top">
      <div class="project-item__name" :title="name">{{ name }}</div>
      <button
        type="button"
        class="icon-btn icon-btn--danger project-item__remove"
        title="删除该项目"
        aria-label="删除该项目"
        @click.stop="emit('remove', project.id)"
      >
        <Icon name="delete-bin-6-line" :size="14" />
      </button>
    </div>

    <div class="project-item__rows mono">{{ rowsText }}</div>

    <div class="project-item__status">
      <VideoStatusBadge :status="project.status" />
      <span v-if="showProgress" class="project-item__pct">{{ progress }}%</span>
      <span v-else-if="project.status === 'completed'" class="project-item__done">处理完成</span>
    </div>

    <div
      v-if="showProgress"
      class="project-item__progress"
      role="progressbar"
      :aria-valuenow="progress"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="project-item__progress-fill" :style="{ width: `${progress}%` }"></div>
    </div>

    <p v-if="isFailed" class="project-item__error">{{ project.error || project.message || '处理失败' }}</p>
    <p v-else-if="showProgress" class="project-item__msg">{{ project.message }}</p>
  </li>
</template>

<style scoped>
.project-item {
  position: relative;
  list-style: none;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.project-item:hover {
  background: var(--surface-alt);
}
.project-item.is-active {
  background: var(--primary-soft);
  border-color: #bfdbfe;
  border-left-color: var(--primary);
}
.project-item:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.35);
  outline-offset: 1px;
}
.project-item--queued {
  border-left-color: #94a3b8;
}
.project-item--processing {
  border-left-color: #ea580c;
}
.project-item--completed {
  border-left-color: #16a34a;
}
.project-item--failed {
  border-left-color: #dc2626;
}
.project-item__top {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.project-item__name {
  flex: 1 1 auto;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
.project-item__remove {
  /* 尺寸与悬停态由全局 .icon-btn / .icon-btn--danger 提供，此处只保证不被标题压扁 */
  flex: 0 0 auto;
  margin: -2px -4px 0 0;
}
.project-item__rows {
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--text-muted);
}
.project-item__status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
.project-item__pct {
  font-family: var(--mono);
}
.project-item__done {
  color: var(--positive);
  font-weight: 600;
}
.project-item__progress {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  overflow: hidden;
  margin-top: 6px;
}
.project-item__progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #fb923c, #ea580c);
  transition: width 0.4s ease;
}
.project-item__msg,
.project-item__error {
  margin: 6px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
}
.project-item__error {
  color: var(--negative);
}
</style>
