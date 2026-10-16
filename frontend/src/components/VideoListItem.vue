<script setup>
/**
 * components/VideoListItem.vue — 左栏单个视频条目
 * 展示：标题（未爬到用 BV 号占位）/ UP 主 / 状态徽章 / 进度条 / 评论数 / 失败原因 + 重试 / 删除
 */
import { computed } from 'vue'
import VideoStatusBadge from './VideoStatusBadge.vue'
import { formatNumber, isTerminal, statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  video: { type: Object, required: true },
  active: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'retry', 'remove'])

const ACTIVE_STATUSES = ['queued', 'crawling', 'processing']

const title = computed(() => props.video.title || props.video.bvid)
const owner = computed(() => props.video.owner || 'UP 主未获取')
const showProgress = computed(() => ACTIVE_STATUSES.includes(props.video.status))
const isCompleted = computed(() => props.video.status === 'completed')
const isFailed = computed(() => props.video.status === 'failed')
const tone = computed(() => statusMeta(props.video.status).tone)
const progress = computed(() => Math.min(100, Math.max(0, Number(props.video.progress) || 0)))
const finished = computed(() => isTerminal(props.video.status))
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
        class="video-item__remove"
        title="删除该任务"
        aria-label="删除该任务"
        @click.stop="emit('remove', video.bvid)"
      >
        ×
      </button>
    </div>

    <div class="video-item__meta">
      <span class="video-item__owner">{{ owner }}</span>
      <span v-if="video.title" class="video-item__bvid mono">{{ video.bvid }}</span>
    </div>

    <div class="video-item__status">
      <VideoStatusBadge :status="video.status" />
      <span v-if="isCompleted" class="video-item__count">{{ formatNumber(video.comment_count) }} 条评论</span>
      <span v-else-if="showProgress" class="video-item__pct">{{ progress }}%</span>
    </div>

    <div v-if="showProgress" class="video-item__progress" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
      <div class="video-item__progress-fill" :style="{ width: `${progress}%` }"></div>
    </div>

    <p v-if="isFailed" class="video-item__error">
      {{ video.message || '分析失败' }}
      <button type="button" class="btn video-item__retry" @click.stop="emit('retry', video.bvid)">重试</button>
    </p>
    <p v-else-if="showProgress" class="video-item__msg">{{ video.message }}</p>
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
  flex: 0 0 auto;
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 18px;
  line-height: 1;
  padding: 0 4px;
  cursor: pointer;
  border-radius: 4px;
}
.video-item__remove:hover {
  background: var(--negative-soft);
  color: var(--negative);
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
