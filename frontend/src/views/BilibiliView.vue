<script setup>
/**
 * views/BilibiliView.vue — 主界面（左栏三段 + 右侧三类详情）
 *
 * 结构：
 *   顶部：BV 输入小框（③段入口，行为不变）+ 右上角「上传爬取数据」/轮询状态/立即刷新
 *   左栏：数据项目 / 单独评论 / 视频任务 三段（各自可收起，状态记忆到 localStorage）
 *   右侧：按选中类型渲染 —— 视频详情（VideoDetail）/ 数据项目详情（ProjectDetail）/
 *         单独评论详情（SoloCommentDetail，无任何图表）
 *
 * 数据与轮询：
 *   - 三段列表统一来自 useArtifacts 的**一次** GET /api/artifacts（groups）；
 *   - 视频详情、失败重试、删除、详情补拉逻辑完全复用 useVideoAnalysis，
 *     只是把 videos 的 ref 交给 useArtifacts（避免两个定时器互相覆盖）；
 *   - 轮询节奏沿用 usePolling（3 秒、全部终态停止、页面隐藏暂停）。
 */
import { computed, ref, watch } from 'vue'
import BvInput from '../components/BvInput.vue'
import VideoSidebar from '../components/VideoSidebar.vue'
import VideoDetail from '../components/VideoDetail.vue'
import ProjectDetail from '../components/ProjectDetail.vue'
import SoloCommentDetail from '../components/SoloCommentDetail.vue'
import UploadArtifactsDialog from '../components/UploadArtifactsDialog.vue'
import Icon from '../components/Icon.vue'
import { useArtifacts } from '../composables/useArtifacts.js'
import { useVideoAnalysis } from '../composables/useVideoAnalysis.js'
import { isTerminal } from '../utils/videoFormat.js'
import '../assets/video.css'

const COLLAPSE_KEY = 'csa.bilibili.sidebarCollapsed'
const SECTION_KEYS = {
  projects: 'csa.bilibili.section.projects',
  solo: 'csa.bilibili.section.solo',
  videos: 'csa.bilibili.section.videos',
}

function readFlag(key, fallback = false) {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? fallback : raw === '1'
  } catch {
    return fallback
  }
}

function writeFlag(key, value) {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {
    /* 隐私模式下 localStorage 不可用，忽略即可 */
  }
}

// ---------------------------------------------------------------------------
// 左栏三段：唯一数据源（GET /api/artifacts，一个请求）
// ---------------------------------------------------------------------------
const {
  projects,
  solo,
  videos,
  labels,
  loading: listLoading,
  error: listError,
  actionError: artifactsActionError,
  uploading,
  clearActionError: clearArtifactsError,
  isPolling: artifactsPolling,
  refresh: refreshArtifacts,
  start: startArtifacts,
  upload,
  removeProject,
  reprocessProject,
  removeSolo,
  clearSolo,
} = useArtifacts()

// 视频详情 / 重试 / 删除 / 详情补拉：复用既有实现，列表 ref 由上面统一提供
const {
  submitting,
  actionError: videoActionError,
  clearActionError: clearVideoActionError,
  selectedBvid,
  selectedVideo,
  selectedStatus,
  detail,
  detailLoading,
  detailError,
  isPolling: videoPolling,
  select: selectVideo,
  reloadDetail,
  submit,
  retry,
  remove: removeVideo,
  refreshNow: refreshVideoNow,
  start: startVideoPoll,
} = useVideoAnalysis({ videos })

const isPolling = computed(() => artifactsPolling.value || videoPolling.value)

/**
 * ③段详情轮询的兜底启动：
 * useVideoAnalysis 挂载时外部 videos ref 还是空数组，它的 usePolling 会因「没有非终态任务」
 * 立刻停止；等 useArtifacts 的第一次 /api/artifacts 返回后，列表里才出现进行中的视频。
 * 这里在「第一次出现进行中的视频且轮询已停」时把它拉起来，保证详情仍按 3 秒刷新，
 * 并保留「非终态 → 终态」那一次补拉（task-6 记录的缺陷不得复活）。
 */
watch(
  () => videos.value.some((item) => !isTerminal(item.status)),
  (active) => {
    if (active && !videoPolling.value) startVideoPoll()
  },
)

// ---------------------------------------------------------------------------
// 选中态：{ kind: 'project' | 'solo' | 'video' | '', id }
// ---------------------------------------------------------------------------
const selection = ref({ kind: '', id: '' })
const uploadOpen = ref(false)
const retrying = ref(false)
const bvInput = ref(null)

const selectedProject = computed(() =>
  selection.value.kind === 'project' ? projects.value.find((item) => item.id === selection.value.id) || null : null,
)
const selectedSolo = computed(() =>
  selection.value.kind === 'solo' ? solo.value.find((item) => item.id === selection.value.id) || null : null,
)

/** 右侧实际渲染哪一类详情（选中的条目被删掉时回落到空态） */
const detailKind = computed(() => {
  if (selection.value.kind === 'project' && selectedProject.value) return 'project'
  if (selection.value.kind === 'solo' && selectedSolo.value) return 'solo'
  if (selection.value.kind === 'video') return 'video'
  return ''
})

const actionError = computed(() => videoActionError.value || artifactsActionError.value)

function clearActionError() {
  clearVideoActionError()
  clearArtifactsError()
}

// ---------------------------------------------------------------------------
// 左栏收起状态（整栏 + 三段各自）记忆到 localStorage
// ---------------------------------------------------------------------------
const collapsed = ref(readFlag(COLLAPSE_KEY, false))
watch(collapsed, (value) => writeFlag(COLLAPSE_KEY, value))

const sections = ref({
  projects: readFlag(SECTION_KEYS.projects),
  solo: readFlag(SECTION_KEYS.solo),
  videos: readFlag(SECTION_KEYS.videos),
})
watch(
  sections,
  (value) => {
    for (const key of Object.keys(SECTION_KEYS)) writeFlag(SECTION_KEYS[key], Boolean(value[key]))
  },
  { deep: true },
)

function toggleSection(key) {
  sections.value = { ...sections.value, [key]: !sections.value[key] }
}

// ---------------------------------------------------------------------------
// 选中 / 操作
// ---------------------------------------------------------------------------
function onSelectProject(id) {
  clearActionError()
  selection.value = { kind: 'project', id }
}

function onSelectSolo(id) {
  clearActionError()
  selection.value = { kind: 'solo', id }
}

function onSelectVideo(bvid) {
  clearActionError()
  selection.value = { kind: 'video', id: bvid }
  selectVideo(bvid)
}

async function onSubmit(bvid) {
  clearActionError()
  const ok = await submit(bvid)
  if (ok) {
    bvInput.value?.reset() // 提交成功才清空输入框
    selection.value = { kind: 'video', id: selectedBvid.value }
    await refreshArtifacts() // ③段列表来自 /api/artifacts
    startArtifacts()
  }
}

async function onRetryVideo(bvid) {
  retrying.value = true
  try {
    await retry(bvid)
    await refreshArtifacts()
    startArtifacts()
  } finally {
    retrying.value = false
  }
}

async function onRemoveVideo(bvid) {
  const target = videos.value.find((item) => item.bvid === bvid)
  const label = target?.title || bvid
  if (!window.confirm(`确定删除「${label}」的分析任务吗？`)) return
  const ok = await removeVideo(bvid)
  if (ok) {
    await refreshArtifacts()
    if (selection.value.kind === 'video' && selection.value.id === bvid) selection.value = { kind: '', id: '' }
  }
}

async function onRemoveProject(id) {
  const target = projects.value.find((item) => item.id === id)
  const label = target?.name || '未自定义数据'
  if (!window.confirm(`确定删除数据项目「${label}」吗？该项目的落盘目录也会被删除。`)) return
  const ok = await removeProject(id)
  if (ok && selection.value.kind === 'project' && selection.value.id === id) selection.value = { kind: '', id: '' }
}

async function onRemoveSolo(id) {
  const ok = await removeSolo(id)
  if (ok && selection.value.kind === 'solo' && selection.value.id === id) selection.value = { kind: '', id: '' }
}

async function onClearSolo() {
  if (!solo.value.length) return
  if (!window.confirm(`确定清空全部 ${solo.value.length} 条单独评论吗？`)) return
  const ok = await clearSolo()
  if (ok && selection.value.kind === 'solo') selection.value = { kind: '', id: '' }
}

async function onReprocessProject(id) {
  const project = await reprocessProject(id)
  if (project) startArtifacts()
}

async function onUpload({ files, name }) {
  const project = await upload(files, name)
  if (project) {
    uploadOpen.value = false
    selection.value = { kind: 'project', id: project.id } // 上传后自动选中新项目
    startArtifacts()
  }
}

async function refreshAll() {
  await refreshArtifacts()
  await refreshVideoNow()
}

function openUpload() {
  clearArtifactsError()
  uploadOpen.value = true
}

function closeUpload() {
  if (uploading.value) return
  clearArtifactsError()
  uploadOpen.value = false
}

const counts = computed(() => ({
  projects: projects.value.length,
  solo: solo.value.length,
  videos: videos.value.length,
}))
</script>

<template>
  <section class="bili">
    <header class="bili__top">
      <div class="bili__top-left">
        <BvInput
          ref="bvInput"
          :submitting="submitting"
          :server-error="videoActionError"
          @submit="onSubmit"
          @input="clearActionError"
        />
      </div>
      <div class="bili__top-right">
        <span class="bili__poll" :class="{ 'is-on': isPolling }">
          <Icon name="pulse-line" :size="14" />
          {{ isPolling ? '自动刷新中（3s）' : '轮询已停止' }}
        </span>
        <button type="button" class="btn btn--primary" @click="openUpload">
          <Icon name="upload-cloud-2-line" :size="15" />
          上传爬取数据
        </button>
        <button type="button" class="btn" @click="refreshAll()">
          <Icon name="refresh-line" :size="15" />
          立即刷新
        </button>
      </div>
    </header>

    <p v-if="actionError" class="hint hint--error bili__action-error">
      <Icon name="alert-line" :size="14" />
      {{ actionError }}
      <button type="button" class="btn bili__action-close" @click="clearActionError">知道了</button>
    </p>

    <div class="bili__body" :class="{ 'is-collapsed': collapsed }">
      <VideoSidebar
        :projects="projects"
        :solo="solo"
        :videos="videos"
        :labels="labels"
        :selected="selection"
        :collapsed="collapsed"
        :sections="sections"
        :loading="listLoading"
        :error="listError"
        :polling="isPolling"
        @toggle="collapsed = !collapsed"
        @toggle-section="toggleSection"
        @select-project="onSelectProject"
        @select-solo="onSelectSolo"
        @select-video="onSelectVideo"
        @remove-project="onRemoveProject"
        @remove-solo="onRemoveSolo"
        @clear-solo="onClearSolo"
        @retry-video="onRetryVideo"
        @remove-video="onRemoveVideo"
      />

      <main class="bili__detail">
        <ProjectDetail
          v-if="detailKind === 'project'"
          :project="selectedProject"
          @reprocess="onReprocessProject"
        />
        <SoloCommentDetail v-else-if="detailKind === 'solo'" :item="selectedSolo" />
        <VideoDetail
          v-else-if="detailKind === 'video'"
          :detail="detail"
          :video="selectedVideo"
          :has-selection="true"
          :loading="detailLoading"
          :error="detailError"
          :retrying="retrying"
          @retry="onRetryVideo"
          @reload="reloadDetail"
        />
        <!-- 未选中：沿用既有引导空态，不渲染任何图表 -->
        <VideoDetail v-else :has-selection="false" />
      </main>
    </div>

    <p class="bili__statusbar">
      当前选中：
      <span class="mono">
        {{
          detailKind === 'project'
            ? selectedProject?.name || selectedProject?.id
            : detailKind === 'solo'
              ? selectedSolo?.id
              : detailKind === 'video'
                ? selectedBvid
                : '—'
        }}
      </span>
      <template v-if="detailKind === 'video' && selectedStatus"> · 状态：{{ selectedStatus }}</template>
      <template v-else-if="detailKind === 'project' && selectedProject"> · 状态：{{ selectedProject.status }}</template>
      <template v-else-if="detailKind === 'solo' && selectedSolo"> · 极性：{{ selectedSolo.label }}</template>
      · 数据项目 {{ counts.projects }} · 单独评论 {{ counts.solo }} · 视频任务 {{ counts.videos }}
    </p>

    <UploadArtifactsDialog
      v-if="uploadOpen"
      :uploading="uploading"
      :error="artifactsActionError"
      @close="closeUpload"
      @submit="onUpload"
    />
  </section>
</template>

<style scoped>
.bili {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.bili__top {
  position: sticky;
  top: 56px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 12px 16px;
}
.bili__top-left {
  flex: 1 1 420px;
  min-width: 280px;
}
.bili__top-right {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-wrap: wrap;
}
.bili__poll {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-muted);
  white-space: nowrap;
}
/* 轮询状态：原先是发光圆点，改用 pulse-line 图标，颜色即状态 */
.bili__poll .icon {
  color: #cbd5e1;
}
.bili__poll.is-on .icon {
  color: #22c55e;
}
.bili__action-error {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 0;
}
.bili__action-close {
  padding: 2px 10px;
  font-size: 12.5px;
}
.bili__body {
  display: grid;
  grid-template-columns: minmax(272px, 340px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.bili__body.is-collapsed {
  grid-template-columns: 64px minmax(0, 1fr);
}
.bili__detail {
  min-width: 0;
}
.bili__statusbar {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-muted);
}
@media (max-width: 900px) {
  .bili__body,
  .bili__body.is-collapsed {
    grid-template-columns: 1fr;
  }
}
</style>
