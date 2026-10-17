<script setup>
/**
 * components/VideoListItem.vue — 左栏单个视频条目
 * 展示：标题（未爬到用 BV 号占位）/ UP 主 / 状态徽章 / 进度条 / 评论数 / 失败原因 + 重试 / 删除
 * 分步流水线：crawled / cleaned 显示「待清洗 / 待分析」并提示到右侧详情点继续按钮。
 */
import { computed } from 'vue'
import VideoStatusBadge from './VideoStatusBadge.vue'
import Icon from './Icon.vue'
import { formatNumber, isActive, isTerminal, isWaiting, statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  video: { type: Object, required: true },
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'retry', 'remove'])

const title = computed(() => props.video.title || props.video.bvid)
const owner = computed(() => props.video.owner || 'UP 主未获取')
const showProgress = computed(() => isActive(props.video.status))
const isCompleted = computed(() => props.video.status === 'completed')
const isFailed = computed(() => props.video.status === 'failed')
const isPaused = computed(() => isWaiting(props.video.status))
const tone = computed(() => statusMeta(props.video.status).tone)
const progress = computed(() => Math.min(100, Math.max(0, Number(props.video.progress) || 0)))
const finished = computed(() => isTerminal(props.video.status))
const waitingHint = computed(() =>
  props.video.status === 'crawled' ? '等待清洗：在右侧点「开始清洗」继续' : '等待分析：在右侧点「开始分析」继续',
)
</script>

<template>
  <li
    class="video-item"
    :class="[`video-item--${tone}`, { 'is-active': active, 'is-done': finished }]"
    tabindex="0"
    role="button"
    :aria-pressed="active"
    @click="emit('select', video.bvid)"
    @keydown.enter.prevent="emit('select', video.bvid)"
    @keydown.space.prevent="emit('select', video.bvid)"
  >
    <div class="video-item__top">
      <div class="video-item__title" :title="video.title || video.bvid">
        <span v-if="!video.title" class="video-item__placeholder">{{ video.bvid }}</span>
        <template v-else>{{ video.title }}</template>
      </div>
      <button
        type="button"
        class="icon-btn icon-btn--danger video-item__remove"
        title="删除该任务"
        aria-label="删除该任务"
        @click.stop="emit('remove', video.bvid)"
      >
        <Icon name="delete-bin-6-line" :size="14" />
      </button>
    </div>

    <div class="video-item__meta">
      <span class="video-item__owner">{{ owner }}</span>
      <span v-if="video.title" class="video-item__bvid mono">{{ video.bvid }}</span>
    </div>

    <div class="video-item__status">
      <VideoStatusBadge :status="video.status" />
      <span v-if="isCompleted" class="video-item__count">{{ formatNumber(video.comment_count) }} 条评论</span>
      <span v-else-if="isPaused" class="video-item__count">{{ formatNumber(video.comment_count) }} 条已入库</span>
      <span v-else-if="showProgress" class="video-item__pct">{{ progress }}%</span>
    </div>

    <div v-if="showProgress" class="video-item__progress" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
      <div class="video-item__progress-fill" :style="{ width: `${progress}%` }"></div>
    </div>

    <p v-if="isFailed" class="video-item__error">
      {{ video.message || '分析失败' }}
      <button type="button" class="btn video-item__retry" @click.stop="emit('retry', video.bvid)">
        <Icon name="refresh-line" :size="13" />
        重试
      </button>
    </p>
    <p v-else-if="showProgress" class="video-item__msg">{{ video.message }}</p>
    <p v-else-if="isPaused" class="video-item__msg video-item__msg--waiting">
      <Icon :name="video.status === 'crawled' ? 'eraser-line' : 'heart-pulse-line'" :size="12" />
      {{ waitingHint }}
    </p>
  </li>
</template>

<style scoped>
.video-item {
  position: relative;
  list-style: none;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.video-item:hover {
  background: var(--surface-alt);
}
.video-item.is-active {
  background: var(--primary-soft);
  border-color: #bfdbfe;
  border-left-color: var(--primary);
}
.video-item:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.35);
  outline-offset: 1px;
}
.video-item--queued {
  border-left-color: #94a3b8;
}
.video-item--crawling {
  border-left-color: #2563eb;
}
.video-item--processing {
  border-left-color: #ea580c;
}
/* 分步流水线等待态：待清洗琥珀 / 待分析紫，与「处理中」的橙区分 */
.video-item--crawled {
  border-left-color: #f59e0b;
}
.video-item--cleaned {
  border-left-color: #7c3aed;
}
.video-item--completed {
  border-left-color: #16a34a;
}
.video-item--failed {
  border-left-color: #dc2626;
}
.video-item__top {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.video-item__title {
  flex: 1 1 auto;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
.video-item__placeholder {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}
.video-item__remove {
  /* 尺寸与悬停态由全局 .icon-btn / .icon-btn--danger 提供，此处只保证不被标题压扁 */
  flex: 0 0 auto;
  margin: -2px -4px 0 0;
}
.video-item__meta {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
  color: var(--text-muted);
  font-size: 12.5px;
  margin-top: 2px;
}
.video-item__owner {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.video-item__bvid {
  flex: 0 0 auto;
  font-size: 11.5px;
  opacity: 0.85;
}
.video-item__status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--text-muted);
}
.video-item__count {
  color: var(--positive);
  font-weight: 600;
}
.video-item__pct {
  font-family: var(--mono);
}
.video-item__progress {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  overflow: hidden;
  margin-top: 6px;
}
.video-item__progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #2563eb);
  transition: width 0.4s ease;
}
.video-item--processing .video-item__progress-fill {
  background: linear-gradient(90deg, #fb923c, #ea580c);
}
.video-item__msg {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}
/* 等待态提示：图标 + 中文指引，颜色比普通 message 更醒目 */
.video-item__msg--waiting {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #b45309;
  font-weight: 600;
}
.video-item--cleaned .video-item__msg--waiting {
  color: #6d28d9;
}
.video-item__error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--negative);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.video-item__retry {
  padding: 2px 10px;
  font-size: 12px;
}
</style>
