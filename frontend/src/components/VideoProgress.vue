<script setup>
/**
 * components/VideoProgress.vue — 「处理中」大状态块
 * 严格对应契约第一节：未完成时右侧只显示本组件（环形进度 + progress% + message + phases 三态），不渲染任何图表。
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  /** 左栏列表项（含 status/progress/message/phases） */
  video: { type: Object, default: null },
  /** 详情返回的 analysis（status/progress/message/phases） */
  analysis: { type: Object, default: null },
})

const status = computed(() => props.analysis?.status || props.video?.status || 'queued')
const progress = computed(() => {
  const value = Number(props.analysis?.progress ?? props.video?.progress ?? 0)
  return Math.min(100, Math.max(0, Number.isFinite(value) ? Math.round(value) : 0))
})
const phases = computed(() => props.analysis?.phases || props.video?.phases || [])

/** 契约文案：queued「排队中…」/ crawling「正在爬取评论… N%」/ processing「正在清洗与情感分析… N%」 */
const headline = computed(() => {
  if (status.value === 'crawling') return `正在爬取评论… ${progress.value}%`
  if (status.value === 'processing') return `正在清洗与情感分析… ${progress.value}%`
  return '排队中…'
})

const detailMessage = computed(() => props.analysis?.message || props.video?.message || '')
const label = computed(() => statusMeta(status.value).label)

const RADIUS = 52
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const dashOffset = computed(() => CIRCUMFERENCE * (1 - progress.value / 100))

const PHASE_TEXT = { done: '已完成', active: '进行中', pending: '待执行', failed: '失败' }

/** 子步骤三态 → 图标；原先用 CSS 画的圆点只能靠颜色区分，换成图标后形状本身也能区分 */
const PHASE_ICONS = {
  done: 'checkbox-circle-line',
  active: 'loader-4-line',
  pending: 'time-line',
  failed: 'error-warning-line',
}
const phaseIcon = (status) => PHASE_ICONS[status] || 'time-line'

/** 大标题左侧的状态图标（爬取/处理阶段旋转） */
const HEADLINE_ICONS = { queued: 'time-line', crawling: 'download-cloud-2-line', processing: 'loader-4-line' }
const headlineIcon = computed(() => HEADLINE_ICONS[status.value] || 'time-line')
const headlineSpinning = computed(() => status.value === 'crawling' || status.value === 'processing')
</script>

<template>
  <section class="progress-block" :class="`progress-block--${status}`">
    <div class="progress-block__ring">
      <svg viewBox="0 0 120 120" width="120" height="120" role="img" aria-label="任务进度">
        <circle cx="60" cy="60" :r="RADIUS" fill="none" stroke="#e2e8f0" stroke-width="10" />
        <circle
          cx="60"
          cy="60"
          :r="RADIUS"
          fill="none"
          :stroke="status === 'processing' ? '#ea580c' : '#2563eb'"
          stroke-width="10"
          stroke-linecap="round"
          :stroke-dasharray="CIRCUMFERENCE"
          :stroke-dashoffset="dashOffset"
          transform="rotate(-90 60 60)"
        />
      </svg>
      <div class="progress-block__ring-text">
        <strong>{{ progress }}%</strong>
        <span>{{ label }}</span>
      </div>
    </div>

    <div class="progress-block__main">
      <h2 class="progress-block__headline">
        <Icon :name="headlineIcon" :size="19" :spin="headlineSpinning" />
        {{ headline }}
      </h2>
      <p v-if="detailMessage" class="progress-block__message">{{ detailMessage }}</p>

      <ol class="phase-list">
        <li v-for="phase in phases" :key="phase.name" class="phase" :class="`phase--${phase.status}`">
          <Icon
            class="phase__mark"
            :name="phaseIcon(phase.status)"
            :size="16"
            :spin="phase.status === 'active'"
          />
          <span class="phase__name">{{ phase.name }}</span>
          <span class="phase__state">{{ PHASE_TEXT[phase.status] || phase.status }}</span>
        </li>
      </ol>
      <p class="progress-block__hint">分析完成后将在此处展示情感占比、情感趋势、关键词词云与代表性评论。</p>
    </div>
  </section>
</template>

<style scoped>
.progress-block {
  display: flex;
  gap: 28px;
  align-items: center;
  flex-wrap: wrap;
  padding: 26px 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid #2563eb;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.progress-block--processing {
  border-left-color: #ea580c;
}
.progress-block--queued {
  border-left-color: #94a3b8;
}
.progress-block__ring {
  position: relative;
  flex: 0 0 auto;
}
.progress-block__ring-text {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}
.progress-block__ring-text strong {
  font-size: 22px;
  font-family: var(--mono);
}
.progress-block__ring-text span {
  font-size: 12px;
  color: var(--text-muted);
}
.progress-block__main {
  flex: 1 1 320px;
  min-width: 260px;
}
.progress-block__headline {
  font-size: 19px;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.progress-block__message {
  color: var(--text-muted);
  font-size: 13.5px;
  margin: 0 0 14px;
}
.phase-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.phase {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: var(--text-muted);
}
.phase__mark {
  /* 由 Icon.vue 渲染，颜色即三态；原先的 CSS 圆点已移除 */
  color: #cbd5e1;
  flex: 0 0 auto;
}
.phase__name {
  flex: 1 1 auto;
}
.phase__state {
  font-size: 12px;
}
.phase--active {
  color: #c2410c;
  font-weight: 600;
}
.phase--active .phase__mark {
  color: #ea580c;
}
.phase--done {
  color: var(--positive);
}
.phase--done .phase__mark {
  color: #16a34a;
}
.phase--failed {
  color: var(--negative);
  font-weight: 600;
}
.phase--failed .phase__mark {
  color: #dc2626;
}
.progress-block__hint {
  margin: 14px 0 0;
  font-size: 12.5px;
  color: var(--text-muted);
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
</style>
