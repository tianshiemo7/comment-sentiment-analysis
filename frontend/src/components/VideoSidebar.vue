<script setup>
/**
 * components/VideoSidebar.vue — 左侧栏（三段：数据项目 / 单独评论 / 视频任务）
 *
 * 数据全部来自父组件（useArtifacts 的**一次** GET /api/artifacts → groups），
 * 本组件只渲染与派发事件：
 *   - 三段各自可收起（状态由父组件记忆到 localStorage），整栏也可收起为窄条；
 *   - ③段沿用 VideoListItem，状态徽章/进度条/评论数/失败重试/删除行为原样保留；
 *   - 窄条上每个圆点代表一个条目：外圈=进度（项目/视频）、颜色=状态或情感极性。
 */
import { computed } from 'vue'
import SidebarSection from './SidebarSection.vue'
import ProjectListItem from './ProjectListItem.vue'
import SoloCommentItem from './SoloCommentItem.vue'
import VideoListItem from './VideoListItem.vue'
import Icon from './Icon.vue'
import { isTerminal, statusMeta } from '../utils/videoFormat.js'
import { SENTIMENT_COLORS } from '../utils/sentimentLabels.js'

const props = defineProps({
  projects: { type: Array, default: () => [] },
  solo: { type: Array, default: () => [] },
  videos: { type: Array, default: () => [] },
  /** 当前选中：{ kind: 'project'|'solo'|'video'|'', id: '' } */
  selected: { type: Object, default: () => ({ kind: '', id: '' }) },
  labels: { type: Object, default: () => ({}) },
  collapsed: { type: Boolean, default: false },
  /** 三段各自的收起状态 */
  sections: { type: Object, default: () => ({ projects: false, solo: false, videos: false }) },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  polling: { type: Boolean, default: false },
})

const emit = defineEmits([
  'toggle',
  'toggle-section',
  'select-project',
  'select-solo',
  'select-video',
  'remove-project',
  'remove-solo',
  'clear-solo',
  'retry-video',
  'remove-video',
])

const titleOf = (key, fallback) => props.labels?.[key] || fallback

const activeProjectCount = computed(() => props.projects.filter((item) => !isTerminal(item.status)).length)
const activeVideoCount = computed(() => props.videos.filter((item) => !isTerminal(item.status)).length)

function isActive(kind, id) {
  return props.selected?.kind === kind && props.selected?.id === id
}

/** 窄条圆点颜色：项目/视频按状态，单独评论按情感极性。 */
const STATUS_COLORS = {
  queued: '#94a3b8',
  crawling: '#2563eb',
  processing: '#ea580c',
  completed: '#16a34a',
  failed: '#dc2626',
}

function ringStyle(progress, color) {
  const value = Math.min(100, Math.max(0, Number(progress) || 0))
  return { background: `conic-gradient(${color} ${value}%, #e2e8f0 0)` }
}

function projectDotStyle(project) {
  const tone = statusMeta(project.status).tone
  const color = STATUS_COLORS[tone] || '#94a3b8'
  return ringStyle(project.status === 'completed' ? 100 : project.progress, color)
}

function videoDotStyle(video) {
  const tone = statusMeta(video.status).tone
  const color = STATUS_COLORS[tone] || '#94a3b8'
  return ringStyle(video.status === 'completed' ? 100 : video.progress, color)
}

function soloDotStyle(item) {
  return ringStyle(100, SENTIMENT_COLORS[item.label] || SENTIMENT_COLORS.unknown)
}

/** 窄条圆点的首字母 */
function projectInitial(project) {
  return (project.name || '未').slice(0, 1)
}
function soloInitial(item) {
  return (item.text || '评').slice(0, 1)
}
function videoInitial(video) {
  if (video.title) return video.title.slice(0, 1)
  return String(video.bvid || '').slice(-2)
}
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': collapsed }">
    <div class="sidebar__head">
      <button
        type="button"
        class="icon-btn sidebar__toggle"
        :title="collapsed ? '展开数据与任务栏' : '收起数据与任务栏'"
        :aria-label="collapsed ? '展开数据与任务栏' : '收起数据与任务栏'"
        :aria-expanded="collapsed ? 'false' : 'true'"
        @click="emit('toggle')"
      >
        <Icon :name="collapsed ? 'arrow-right-s-line' : 'arrow-left-s-line'" :size="16" />
      </button>
      <template v-if="!collapsed">
        <h2 class="sidebar__title">数据与任务</h2>
        <span class="sidebar__count">{{ projects.length + solo.length + videos.length }}</span>
      </template>
    </div>

    <!-- 收起态：窄条只保留圆点（外圈=进度，颜色=状态/极性） -->
    <div v-if="collapsed" class="sidebar__rail">
      <template v-if="projects.length">
        <button
          v-for="project in projects"
          :key="project.id"
          type="button"
          class="rail-dot"
          :style="projectDotStyle(project)"
          :title="`${project.name || '未自定义数据'} · ${statusMeta(project.status).label}`"
          :class="{ 'is-active': isActive('project', project.id) }"
          @click="emit('select-project', project.id)"
        >
          <span class="rail-dot__inner">{{ projectInitial(project) }}</span>
        </button>
        <span class="rail-sep"></span>
      </template>

      <template v-if="solo.length">
        <button
          v-for="item in solo"
          :key="item.id"
          type="button"
          class="rail-dot"
          :style="soloDotStyle(item)"
          :title="item.text"
          :class="{ 'is-active': isActive('solo', item.id) }"
          @click="emit('select-solo', item.id)"
        >
          <span class="rail-dot__inner">{{ soloInitial(item) }}</span>
        </button>
        <span class="rail-sep"></span>
      </template>

      <button
        v-for="video in videos"
        :key="video.bvid"
        type="button"
        class="rail-dot"
        :style="videoDotStyle(video)"
        :title="`${video.title || video.bvid} · ${statusMeta(video.status).label}`"
        :aria-label="`${video.title || video.bvid}，${statusMeta(video.status).label}`"
        :class="{ 'is-active': isActive('video', video.bvid) }"
        @click="emit('select-video', video.bvid)"
      >
        <span class="rail-dot__inner">{{ videoInitial(video) }}</span>
      </button>
      <p v-if="!projects.length && !solo.length && !videos.length" class="rail-dot__empty">空</p>
    </div>

    <!-- 展开态：三段 -->
    <div v-else class="sidebar__body">
      <p v-if="loading && !projects.length && !solo.length && !videos.length" class="hint">正在加载数据…</p>
      <p v-else-if="error" class="hint hint--error">{{ error }}</p>

      <!-- ① 数据项目 -->
      <SidebarSection
        :title="titleOf('projects', '数据项目')"
        icon="folder-3-line"
        :count="projects.length"
        :active-count="activeProjectCount"
        :collapsed="Boolean(sections.projects)"
        :is-empty="!projects.length"
        empty="还没有数据项目，点右上角「上传爬取数据」导入 CSV/XLSX。"
        @toggle="emit('toggle-section', 'projects')"
      >
        <ul class="item-list">
          <ProjectListItem
            v-for="project in projects"
            :key="project.id"
            :project="project"
            :active="isActive('project', project.id)"
            @select="emit('select-project', $event)"
            @remove="emit('remove-project', $event)"
          />
        </ul>
      </SidebarSection>

      <!-- ② 单独评论 -->
      <SidebarSection
        :title="titleOf('solo', '单独评论')"
        icon="chat-quote-line"
        :count="solo.length"
        :collapsed="Boolean(sections.solo)"
        :is-empty="!solo.length"
        empty="还没有单独评论。到「单条预测」页开启「保存到左栏」即可收录。"
        @toggle="emit('toggle-section', 'solo')"
      >
        <template #actions>
          <button
            v-if="solo.length"
            type="button"
            class="icon-btn icon-btn--danger"
            title="清空全部单独评论"
            aria-label="清空全部单独评论"
            @click.stop="emit('clear-solo')"
          >
            <Icon name="delete-bin-6-line" :size="14" />
          </button>
        </template>
        <ul class="item-list">
          <SoloCommentItem
            v-for="item in solo"
            :key="item.id"
            :item="item"
            :active="isActive('solo', item.id)"
            @select="emit('select-solo', $event)"
            @remove="emit('remove-solo', $event)"
          />
        </ul>
      </SidebarSection>

      <!-- ③ 视频任务（原有行为保持不变） -->
      <SidebarSection
        :title="titleOf('videos', '视频任务')"
        icon="film-line"
        :count="videos.length"
        :active-count="activeVideoCount"
        :collapsed="Boolean(sections.videos)"
        :is-empty="!videos.length"
        empty="还没有分析任务，在上方输入 BV 号开始分析。"
        @toggle="emit('toggle-section', 'videos')"
      >
        <ul class="item-list">
          <VideoListItem
            v-for="video in videos"
            :key="video.bvid"
            :video="video"
            :active="isActive('video', video.bvid)"
            @select="emit('select-video', $event)"
            @retry="emit('retry-video', $event)"
            @remove="emit('remove-video', $event)"
          />
        </ul>
      </SidebarSection>

      <p class="sidebar__foot">
        <Icon class="sidebar__pulse" :class="{ 'is-on': polling }" name="pulse-line" :size="14" />
        {{ polling ? '每 3 秒自动刷新' : '轮询已停止' }}
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
  /* 左侧箭头图标替代原先的 « / » 字符；命中区与悬停态来自全局 .icon-btn */
  border-color: var(--border-strong);
  background: var(--surface);
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
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1 1 auto;
}
.item-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 46vh;
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
/* 轮询状态：原先是发光圆点，改用 pulse-line 图标，颜色即状态 */
.sidebar__pulse {
  color: #cbd5e1;
}
.sidebar__pulse.is-on {
  color: #22c55e;
}
.sidebar__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  overflow-y: auto;
}
.rail-sep {
  width: 22px;
  height: 1px;
  background: var(--border);
  margin: 2px 0;
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
  flex: 0 0 auto;
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
