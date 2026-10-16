<script setup>
/**
 * components/VideoHeader.vue — 详情区视频信息头
 * 字段来自契约 detail.video（title/owner/bvid/comment_total/pubdate/crawled_at）；未爬到的一律显示「—」。
 */
import { computed } from 'vue'
import VideoStatusBadge from './VideoStatusBadge.vue'
import { formatDateTime, formatNumber } from '../utils/videoFormat.js'

const props = defineProps({
  video: { type: Object, default: null },
  status: { type: String, default: '' },
})

const rows = computed(() => [
  { label: 'BV 号', value: props.video?.bvid || '—', mono: true },
  { label: 'UP 主', value: props.video?.owner || '—' },
  { label: '评论总数', value: props.video?.comment_total == null ? '—' : `${formatNumber(props.video.comment_total)} 条` },
  { label: '发布时间', value: formatDateTime(props.video?.pubdate) },
  { label: '爬取时间', value: formatDateTime(props.video?.crawled_at) },
])
</script>

<template>
  <header class="video-head">
    <div class="video-head__title-row">
      <h1 class="video-head__title">{{ video?.title || video?.bvid || '—' }}</h1>
      <VideoStatusBadge v-if="status" :status="status" />
    </div>
    <dl class="video-head__meta">
      <div v-for="row in rows" :key="row.label" class="video-head__cell">
        <dt>{{ row.label }}</dt>
        <dd :class="{ mono: row.mono }">{{ row.value }}</dd>
      </div>
    </dl>
  </header>
</template>

<style scoped>
.video-head {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 16px 20px;
}
.video-head__title-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  flex-wrap: wrap;
}
.video-head__title {
  font-size: 18px;
  flex: 1 1 auto;
  word-break: break-word;
}
.video-head__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.video-head__cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.video-head__cell dt {
  color: var(--text-muted);
  font-size: 12.5px;
}
.video-head__cell dd {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}
</style>
