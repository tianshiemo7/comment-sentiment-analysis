<script setup>
/**
 * components/VideoDetail.vue — 右侧详情区（按契约状态分派渲染）
 *
 * 关键约束（契约第一节 + 分步流水线设计第 3.1 节）：
 *   未完成（queued / crawling / processing）→ 只渲染 VideoProgress 大状态块，绝不渲染图表；
 *   crawled / cleaned（等待操作）→ 渲染等待块：「开始清洗 / 开始分析」按钮 + phases 进度；
 *   failed      → error 原因 + 重试按钮；
 *   completed   → 饼图 / 折线 / 两张词云 / 关键词排行 / 代表性评论 / 模型指标；
 *   未选中      → 引导空态。
 */
import { computed } from 'vue'
import VideoHeader from './VideoHeader.vue'
import VideoProgress from './VideoProgress.vue'
import CommentList from './CommentList.vue'
import ModelMetrics from './ModelMetrics.vue'
import Icon from './Icon.vue'
import SentimentPie from './charts/SentimentPie.vue'
import TrendLine from './charts/TrendLine.vue'
import WordCloud from './charts/WordCloud.vue'
import KeywordBar from './charts/KeywordBar.vue'

const props = defineProps({
  detail: { type: Object, default: null },
  /** 左栏中被选中的列表项（详情未返回时可先用它渲染头部与状态） */
  video: { type: Object, default: null },
  hasSelection: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  retrying: { type: Boolean, default: false },
  /** 「开始清洗 / 开始分析」（continue）请求进行中 */
  continuing: { type: Boolean, default: false },
})

const emit = defineEmits(['retry', 'reload', 'continue'])

const effectiveStatus = computed(() => props.detail?.analysis?.status || props.video?.status || '')
const isProcessing = computed(() => ['queued', 'crawling', 'processing'].includes(effectiveStatus.value))
const isCompleted = computed(() => effectiveStatus.value === 'completed')
const isFailed = computed(() => effectiveStatus.value === 'failed')
const isWaiting = computed(() => ['crawled', 'cleaned'].includes(effectiveStatus.value))

/**
 * 下一步动作：优先用详情返回的 pipeline.next_action（契约 3.4），
 * 老任务 / 详情未到时按状态兜底推导，保证按钮一定出现。
 */
const nextAction = computed(() => {
  const action = props.detail?.pipeline?.next_action
  if (action === 'clean' || action === 'analyze') return action
  if (effectiveStatus.value === 'crawled') return 'clean'
  if (effectiveStatus.value === 'cleaned') return 'analyze'
  return null
})
const isCrawlStage = computed(() => nextAction.value === 'clean')
const waitingTitle = computed(() => (isCrawlStage.value ? '采集完成，等待清洗' : '清洗完成，等待情感分析'))
const waitingMessage = computed(
  () =>
    props.detail?.analysis?.message ||
    props.video?.message ||
    (isCrawlStage.value ? '采集完成，等待清洗' : '清洗完成，等待情感分析'),
)
const phases = computed(() => props.detail?.analysis?.phases || props.video?.phases || [])
const autoHint = computed(() => {
  const pipeline = props.detail?.pipeline
  if (!pipeline) return '自动连续已关闭：任务会停在阶段边界等待你手动继续'
  const auto = isCrawlStage.value ? pipeline.auto_clean : pipeline.auto_analyze
  return auto
    ? '自动连续已开启：本阶段会继续自动执行（若仍停留在此，通常表示任务来自旧版本或另一客户端）'
    : '自动连续已关闭：任务会停在阶段边界，等待你手动继续'
})

/** 详情里的 video 字段最全；详情未到时用列表项兜底（comment_count → comment_total）。 */
const headerVideo = computed(() => {
  if (props.detail?.video) return props.detail.video
  const video = props.video
  if (!video) return null
  return {
    bvid: video.bvid,
    title: video.title,
    owner: video.owner,
    comment_total: video.comment_count,
    pubdate: null,
    crawled_at: null,
  }
})

const sentiment = computed(() => props.detail?.sentiment || null)
const trend = computed(() => props.detail?.trend || [])
const keywords = computed(() => props.detail?.keywords || { positive: [], negative: [] })
const topComments = computed(() => props.detail?.top_comments || { positive: [], negative: [] })
const model = computed(() => props.detail?.model || null)
const lengthStats = computed(() => props.detail?.analysis?.length_stats || null)

const waitingBvid = computed(() => props.video?.bvid || props.detail?.video?.bvid || '')
const failedBvid = computed(() => props.video?.bvid || props.detail?.video?.bvid || '')

const PHASE_TEXT = { done: '已完成', active: '进行中', pending: '待执行', failed: '失败' }
const PHASE_ICONS = {
  done: 'checkbox-circle-line',
  active: 'loader-4-line',
  pending: 'time-line',
  failed: 'error-warning-line',
}
const phaseIcon = (status) => PHASE_ICONS[status] || 'time-line'
</script>

<template>
  <section class="detail">
    <!-- 未选中任何视频 -->
    <div v-if="!hasSelection" class="detail__empty">
      <div class="detail__empty-icon"><Icon name="play-circle-line" :size="22" /></div>
      <h2>选择一个视频查看分析结果</h2>
      <p>
        在上方输入框提交 B 站视频 <strong>BV 号</strong>（形如 <code class="mono">BV1xx411c7mD</code>），
        左侧列表会实时显示爬取与分析进度。
      </p>
      <ul class="detail__empty-list">
        <li>任务状态：<span class="status-demo status-demo--queued">排队中</span> → <span class="status-demo status-demo--crawling">爬取中</span> → <span class="status-demo status-demo--crawled">待清洗</span> → <span class="status-demo status-demo--processing">处理中</span> → <span class="status-demo status-demo--cleaned">待分析</span> → <span class="status-demo status-demo--completed">已完成</span></li>
        <li>采集 / 清洗完成后默认暂停，在详情区点「开始清洗 / 开始分析」继续（可在系统设置中开启自动连续）</li>
        <li>完成前只显示进度与子步骤，不会出现空图表</li>
        <li>完成后展示情感占比、情感趋势、关键词词云、代表性评论与模型指标</li>
      </ul>
    </div>

    <template v-else>
      <p v-if="error" class="hint hint--error">
        <Icon name="alert-line" :size="14" />
        {{ error }}
        <button type="button" class="btn detail__reload" @click="emit('reload')">
          <Icon name="refresh-line" :size="13" />
          重新加载
        </button>
      </p>

      <p v-if="loading && !detail" class="hint">正在加载视频详情…</p>

      <VideoHeader v-if="headerVideo" :video="headerVideo" :status="effectiveStatus" />

      <!-- 未完成：只有「处理中」状态块，没有任何图表 -->
      <VideoProgress v-if="isProcessing" :video="video" :analysis="detail?.analysis" />

      <!-- 分步流水线等待态：crawled / cleaned 显示继续按钮，仍不渲染任何图表 -->
      <div v-else-if="isWaiting" class="detail__waiting" :class="isCrawlStage ? 'detail__waiting--crawled' : 'detail__waiting--cleaned'">
        <h2 class="detail__waiting-title">
          <Icon :name="isCrawlStage ? 'eraser-line' : 'heart-pulse-line'" :size="19" />
          {{ waitingTitle }}
        </h2>
        <p class="detail__waiting-message">{{ waitingMessage }}</p>

        <ol v-if="phases.length" class="waiting-phases">
          <li v-for="phase in phases" :key="phase.name" class="waiting-phase" :class="`waiting-phase--${phase.status}`">
            <Icon
              class="waiting-phase__mark"
              :name="phaseIcon(phase.status)"
              :size="15"
              :spin="phase.status === 'active'"
            />
            <span class="waiting-phase__name">{{ phase.name }}</span>
            <span class="waiting-phase__state">{{ PHASE_TEXT[phase.status] || phase.status }}</span>
          </li>
        </ol>

        <p class="detail__waiting-hint">
          <Icon name="alert-line" :size="14" />
          {{ autoHint }}
        </p>

        <div class="detail__waiting-actions">
          <button
            type="button"
            class="btn btn--primary"
            :disabled="continuing"
            @click="emit('continue', waitingBvid)"
          >
            <Icon :name="isCrawlStage ? 'eraser-line' : 'heart-pulse-line'" :size="14" :spin="continuing" />
            {{ continuing ? '正在继续…' : isCrawlStage ? '开始清洗' : '开始分析' }}
          </button>
          <span class="detail__waiting-note">继续后任务回到队列，完成后会再次暂停或直接完成（取决于自动流程设置）。</span>
        </div>
      </div>

      <!-- 失败 -->
      <div v-else-if="isFailed" class="detail__failed">
        <h2><Icon name="error-warning-line" :size="18" /> 分析失败</h2>
        <p class="detail__failed-reason">{{ detail?.error || video?.message || '未知错误' }}</p>
        <button type="button" class="btn btn--primary" :disabled="retrying" @click="emit('retry', failedBvid)">
          <Icon name="refresh-line" :size="14" />
          {{ retrying ? '重试中…' : '重试该任务' }}
        </button>
      </div>

      <!-- 已完成：完整图表区 -->
      <template v-else-if="isCompleted">
        <div class="detail__grid">
          <SentimentPie :sentiment="sentiment" />
          <TrendLine :trend="trend" />
        </div>

        <div class="detail__grid">
          <WordCloud :words="keywords.positive" tone="positive" />
          <WordCloud :words="keywords.negative" tone="negative" />
        </div>

        <div class="detail__grid">
          <KeywordBar :keywords="keywords" :limit="15" />
          <div class="detail__side">
            <ModelMetrics :model="model" />
            <div class="chart-card">
              <div class="chart-card__head">
                <h3><Icon name="file-3-line" :size="16" /> 文本长度统计</h3>
                <span class="card__hint">清洗后的评论</span>
              </div>
              <dl class="kv">
                <dt>平均字数</dt>
                <dd class="mono">{{ lengthStats?.avg_chars ?? '—' }}</dd>
                <dt>最长字数</dt>
                <dd class="mono">{{ lengthStats?.max_chars ?? '—' }}</dd>
              </dl>
            </div>
          </div>
        </div>

        <div class="detail__grid">
          <CommentList title="代表性正面评论" tone="positive" :comments="topComments.positive" />
          <CommentList title="代表性负面评论" tone="negative" :comments="topComments.negative" />
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.detail__empty {
  background: var(--surface);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  padding: 32px 28px;
  color: var(--text-muted);
}
.detail__empty h2 {
  color: var(--text);
  font-size: 18px;
  margin-bottom: 8px;
}
.detail__empty-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--primary-soft);
  color: var(--primary);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}
.detail__empty-list {
  margin: 12px 0 0;
  padding-left: 18px;
  font-size: 13.5px;
}
.detail__empty-list li {
  margin-bottom: 4px;
}
.status-demo {
  display: inline-block;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--surface-alt);
}
.status-demo--queued {
  color: #475569;
}
.status-demo--crawling {
  color: #1d4ed8;
  background: #eff6ff;
}
.status-demo--processing {
  color: #c2410c;
  background: #fff7ed;
}
.status-demo--crawled {
  color: #b45309;
  background: #fffbeb;
}
.status-demo--cleaned {
  color: #6d28d9;
  background: #f5f3ff;
}
.status-demo--completed {
  color: var(--positive);
  background: var(--positive-soft);
}
.detail__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
  align-items: start;
}
.detail__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.detail__failed {
  background: var(--surface);
  border: 1px solid #fecaca;
  border-left: 4px solid var(--negative);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px 24px;
}
/* 分步流水线等待块：风格与「处理中」大状态块一致，只是少了环形进度 */
.detail__waiting {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid #f59e0b;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}
.detail__waiting--crawled {
  border-left-color: #f59e0b;
}
.detail__waiting--cleaned {
  border-left-color: #7c3aed;
}
.detail__waiting--cleaned .detail__waiting-title {
  color: #6d28d9;
}
.detail__waiting-title {
  font-size: 19px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #b45309;
}
.detail__waiting-message {
  margin: 0;
  color: var(--text-muted);
  font-size: 13.5px;
}
.waiting-phases {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}
.waiting-phase {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: var(--text-muted);
}
.waiting-phase__mark {
  color: #cbd5e1;
  flex: 0 0 auto;
}
.waiting-phase__name {
  flex: 1 1 auto;
}
.waiting-phase__state {
  font-size: 12px;
}
.waiting-phase--done {
  color: var(--positive);
}
.waiting-phase--done .waiting-phase__mark {
  color: #16a34a;
}
.waiting-phase--active {
  color: #c2410c;
  font-weight: 600;
}
.waiting-phase--active .waiting-phase__mark {
  color: #ea580c;
}
.waiting-phase--failed {
  color: var(--negative);
  font-weight: 600;
}
.waiting-phase--failed .waiting-phase__mark {
  color: #dc2626;
}
.detail__waiting-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 8px 10px;
  width: 100%;
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  border: 1px solid var(--warn-border);
  color: var(--warn-text);
  font-size: 12.5px;
}
.detail__waiting-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.detail__waiting-note {
  color: var(--text-muted);
  font-size: 12.5px;
}
.detail__failed h2 {
  font-size: 18px;
  color: var(--negative);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.detail__failed-reason {
  color: var(--text);
  font-size: 14px;
  margin-bottom: 14px;
}
.detail__reload {
  margin-left: 10px;
  padding: 2px 10px;
  font-size: 12.5px;
}
/* 错误提示条：图标 + 文案 + 「重新加载」按钮并排 */
.detail .hint--error {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
