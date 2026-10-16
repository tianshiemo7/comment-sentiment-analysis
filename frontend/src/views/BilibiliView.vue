<script setup>
/**
 * views/BilibiliView.vue — B 站视频评论分析主界面（新首页）
 *
 * 结构：顶部 BV 输入小框 + 左侧可收起视频列表 + 右侧详情区。
 * 数据、轮询与操作全部来自 useVideoAnalysis（列表 3 秒轮询、全部终态停止、后台暂停）。
 */
import { ref, watch } from 'vue'
import BvInput from '../components/BvInput.vue'
import VideoSidebar from '../components/VideoSidebar.vue'
import VideoDetail from '../components/VideoDetail.vue'
import { useVideoAnalysis } from '../composables/useVideoAnalysis.js'
import '../assets/video.css'

const COLLAPSE_KEY = 'csa.bilibili.sidebarCollapsed'

function readCollapsed() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

const {
  videos,
  listLoading,
  listError,
  submitting,
  actionError,
  clearActionError,
  selectedBvid,
  selectedVideo,
  selectedStatus,
  detail,
  detailLoading,
  detailError,
  isPolling,
  select,
  reloadDetail,
  submit,
  retry,
  remove,
  refreshNow,
} = useVideoAnalysis()

// 左栏收起状态：记忆到 localStorage
const collapsed = ref(readCollapsed())
watch(collapsed, (value) => {
  try {
    localStorage.setItem(COLLAPSE_KEY, value ? '1' : '0')
  } catch {
    /* 隐私模式下 localStorage 不可用，忽略即可 */
  }
})

const bvInput = ref(null)
const retrying = ref(false)

async function onSubmit(bvid) {
  clearActionError()
  const ok = await submit(bvid)
  if (ok) bvInput.value?.reset() // 提交成功才清空输入框
}

async function onRetry(bvid) {
  retrying.value = true
  try {
    await retry(bvid)
  } finally {
    retrying.value = false
  }
}

async function onRemove(bvid) {
  const target = videos.value.find((item) => item.bvid === bvid)
  const label = target?.title || bvid
  if (!window.confirm(`确定删除「${label}」的分析任务吗？`)) return
  await remove(bvid)
}
</script>

<template>
  <section class="bili">
    <header class="bili__top">
      <div class="bili__top-left">
        <BvInput
          ref="bvInput"
          :submitting="submitting"
          :server-error="actionError"
          @submit="onSubmit"
          @input="clearActionError"
        />
      </div>
      <div class="bili__top-right">
        <span class="bili__poll" :class="{ 'is-on': isPolling }">
          <span class="bili__poll-dot"></span>
          {{ isPolling ? '自动刷新中（3s）' : '轮询已停止' }}
        </span>
        <button type="button" class="btn" @click="refreshNow()">立即刷新</button>
      </div>
    </header>

    <div class="bili__body" :class="{ 'is-collapsed': collapsed }">
      <VideoSidebar
        :videos="videos"
        :selected-bvid="selectedBvid"
        :collapsed="collapsed"
        :loading="listLoading"
        :error="listError"
        :polling="isPolling"
        @toggle="collapsed = !collapsed"
        @select="select"
        @retry="onRetry"
        @remove="onRemove"
      />

      <main class="bili__detail">
        <VideoDetail
          :detail="detail"
          :video="selectedVideo"
          :has-selection="Boolean(selectedBvid)"
          :loading="detailLoading"
          :error="detailError"
          :retrying="retrying"
          @retry="onRetry"
          @reload="reloadDetail"
        />
      </main>
    </div>

    <p class="bili__statusbar">
      当前选中：<span class="mono">{{ selectedBvid || '—' }}</span>
      <template v-if="selectedStatus"> · 状态：{{ selectedStatus }}</template>
      · 共 {{ videos.length }} 个任务
    </p>
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
  flex: 1 1 460px;
  min-width: 280px;
}
.bili__top-right {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.bili__poll {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-muted);
  white-space: nowrap;
}
.bili__poll-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
}
.bili__poll.is-on .bili__poll-dot {
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18);
}
.bili__body {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
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
