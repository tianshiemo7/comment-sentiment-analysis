<script setup>
/**
 * components/VideoSidebar.vue — 左侧视频列表（可收起为窄条）
 * 收起状态由父组件持有并写入 localStorage；窄条上每个圆点代表一个视频（颜色=状态、外圈=进度）。
 */
import { computed } from 'vue'
import VideoListItem from './VideoListItem.vue'
import { statusMeta } from '../utils/videoFormat.js'

const props = defineProps({
  videos: { type: Array, default: () => [] },
  selectedBvid: { type: String, default: '' },
  collapsed: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  polling: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'retry', 'remove', 'toggle'])

const activeCount = computed(() => props.videos.filter((item) => item.status !== 'completed' && item.status !== 'failed').length)

/** 窄条圆点的首字母：优先标题首字，否则 BV 号末两位 */
function initial(video) {
  if (video.title) return video.title.slice(0, 1)
  return video.bvid.slice(-2)
}

function dotStyle(video) {
  const tone = statusMeta(video.status).tone
  const colors = {
    queued: '#94a3b8',
    crawling: '#2563eb',
    processing: '#ea580c',
    completed: '#16a34a',
    failed: '#dc2626',
  }
  const progress = video.status === 'completed' ? 100 : Math.min(100, Math.max(0, Number(video.progress) || 0))
  const color = colors[tone] || '#94a3b8'
  return { background: `conic-gradient(${color} ${progress}%, #e2e8f0 0)` }
}
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': collapsed }">
    <div class="sidebar__head">
      <button
        type="button"
        class="sidebar__toggle"
        :title="collapsed ? '展开视频列表' : '收起视频列表'"
        :aria-label="collapsed ? '展开视频列表' : '收起视频列表'"
        :aria-expanded="collapsed ? 'false' : 'true'"
        @click="emit('toggle')"
      >
        {{ collapsed ? '»' : '«' }}
      </button>
      <template v-if="!collapsed">
        <h2 class="sidebar__title">视频列表</h2>
        <span class="sidebar__count">{{ videos.length }}</span>
      </template>
    </div>

    <!-- 收起态：窄条只保留状态/进度圆点 -->
    <div v-if="collapsed" class="sidebar__rail">
      <button
        v-for="video in videos"
        :key="video.bvid"
        type="button"
        class="rail-dot"
        :style="dotStyle(video)"
        :title="`${video.title || video.bvid} · ${statusMeta(video.status).label}`"
        :aria-label="`${video.title || video.bvid}，${statusMeta(video.status).label}`"
        :class="{ 'is-active': video.bvid === selectedBvid }"
        @click="emit('select', video.bvid)"
      >
        <span class="rail-dot__inner">{{ initial(video) }}</span>
      </button>
      <p v-if="!videos.length" class="rail-dot__empty">空</p>
    </div>

    <!-- 展开态 -->
    <div v-else class="sidebar__body">
      <p v-if="loading" class="hint">正在加载视频列表…</p>
      <p v-else-if="error" class="hint hint--error">{{ error }}</p>
      <p v-else-if="!videos.length" class="hint">还没有分析任务，在上方输入 BV 号开始分析。</p>

      <ul v-else class="video-list">
        <VideoListItem
          v-for="video in videos"
          :key="video.bvid"
          :video="video"
          :active="video.bvid === selectedBvid"
          @select="emit('select', $event)"
          @retry="emit('retry', $event)"
          @remove="emit('remove', $event)"
        />
      </ul>

      <p class="sidebar__foot">
        <span class="sidebar__pulse" :class="{ 'is-on': polling }"></span>
        {{ polling ? '每 3 秒自动刷新' : '轮询已停止' }}
        <template v-if="activeCount">· {{ activeCount }} 个进行中</template>
      </p>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
  min-height: 420px;
}
.sidebar--collapsed {
  min-height: 420px;
}
.sidebar__head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-alt);
}
.sidebar__toggle {
  appearance: none;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  width: 26px;
  height: 26px;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  flex: 0 0 auto;
}
.sidebar__toggle:hover {
  color: var(--primary);
  border-color: var(--primary);
}
.sidebar__title {
  font-size: 14px;
}
.sidebar__count {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 7px;
}
.sidebar__body {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 auto;
}
.video-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 62vh;
  overflow-y: auto;
}
.sidebar__foot {
  margin: auto 0 0;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sidebar__pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
}
.sidebar__pulse.is-on {
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
}
.sidebar__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
}
.rail-dot {
  appearance: none;
  border: 0;
  padding: 2px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.rail-dot.is-active {
  box-shadow: 0 0 0 2px var(--primary);
}
.rail-dot__inner {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #fff;
  color: var(--text);
  font-size: 11.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.rail-dot__empty {
  color: var(--text-muted);
  font-size: 12px;
}
</style>
