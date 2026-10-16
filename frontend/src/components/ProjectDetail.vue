<script setup>
/**
 * components/ProjectDetail.vue — 「数据项目」详情视图（契约 4.5.1）
 *
 * 结构（自上而下）：
 *   1. 头部：名称 / `初始 N 行 → 结果 M 行` / 来源文件名 / method 标识（rule_placeholder → 醒目「规则占位」）
 *   2. 三张表切换（初始数据 / 筛选后结果 / 排除记录）+ 分页（默认 100 行/页，列名按后端 columns 动态渲染）
 *   3. 可展开的逐条评论情感（折叠态只加载第一页；展开后分页 + positive/negative 筛选）
 *   4. 聚合情感图表：占比饼图 + 正/负词云 + 长度分布（复用既有 charts 组件）
 *   5. **页面最底部**：打开 CSV / 复制文件地址 / 重新处理
 */
import { computed, ref } from 'vue'
import EChart from './charts/EChart.vue'
import SentimentPie from './charts/SentimentPie.vue'
import WordCloud from './charts/WordCloud.vue'
import MethodBadge from './MethodBadge.vue'
import SentimentBadge from './SentimentBadge.vue'
import ProbBars from './ProbBars.vue'
import VideoStatusBadge from './VideoStatusBadge.vue'
import Icon from './Icon.vue'
import { TABLE_STAGES, useArtifactDetail } from '../composables/useArtifactDetail.js'
import { formatDateTime, formatNumber } from '../utils/videoFormat.js'
import { formatPercent } from '../utils/format.js'

const props = defineProps({
  /** 选中项目的项目对象（来自 GET /api/artifacts 的 groups.projects） */
  project: { type: Object, default: null },
})

const emit = defineEmits(['reprocess'])

const projectId = computed(() => props.project?.id || '')
const projectStatus = computed(() => props.project?.status || '')

const {
  stage,
  tableLimit,
  columns,
  rows,
  totalRows,
  tableLoading,
  tableError,
  tablePage,
  tablePages,
  commentsOpen,
  commentsLabel,
  commentsTotal,
  comments,
  commentsLoading,
  commentsError,
  commentsPage,
  commentsPages,
  analysis,
  analysisLoading,
  analysisError,
  sentimentForChart,
  opening,
  copying,
  actionMessage,
  actionError,
  switchStage,
  pageTo,
  prevPage,
  nextPage,
  setTableLimit,
  toggleComments,
  setCommentsLabel,
  commentsPageTo,
  commentsPrev,
  commentsNext,
  openCsv,
  copyFileAddress,
  reload,
} = useArtifactDetail(projectId, projectStatus)

const jumpPage = ref('')

const name = computed(() => props.project?.name || '未自定义数据')
const isCompleted = computed(() => projectStatus.value === 'completed')
const isFailed = computed(() => projectStatus.value === 'failed')
const isProcessing = computed(() => ['queued', 'processing'].includes(projectStatus.value))
const progress = computed(() => Math.min(100, Math.max(0, Number(props.project?.progress) || 0)))
const sources = computed(() => (Array.isArray(props.project?.sources) ? props.project.sources : []))

function stageRows(key) {
  const item = (props.project?.stages || []).find((entry) => entry.key === key)
  return item ? Number(item.rows) || 0 : null
}
const initialRows = computed(() => stageRows('initial'))
const cleanRows = computed(() => stageRows('clean'))

/** 三张表的标题用后端 stages 里的 label，回退到契约默认文案 */
const stageTabs = computed(() =>
  TABLE_STAGES.map((item) => {
    const stageInfo = (props.project?.stages || []).find((entry) => entry.key === item.key)
    return {
      key: item.key,
      label: stageInfo?.label || item.label,
      rows: stageInfo ? Number(stageInfo.rows) || 0 : null,
    }
  }),
)

const stageLabel = computed(() => stageTabs.value.find((item) => item.key === stage.value)?.label || stage.value)

/** 长度分布柱图（契约 analysis.length：mean / median / p90 / max） */
const lengthOption = computed(() => {
  const data = analysis.value?.length || {}
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 44, right: 16, top: 16, bottom: 30, containLabel: false },
    xAxis: { type: 'category', data: ['平均', '中位数', 'P90', '最长'] },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      {
        type: 'bar',
        barMaxWidth: 36,
        itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', fontSize: 11, color: '#475569' },
        data: [data.mean ?? 0, data.median ?? 0, data.p90 ?? 0, data.max ?? 0],
      },
    ],
  }
})

const lengthRows = computed(() => {
  const data = analysis.value?.length || {}
  return [
    { label: '平均字数', value: data.mean ?? '—' },
    { label: '中位数', value: data.median ?? '—' },
    { label: 'P90', value: data.p90 ?? '—' },
    { label: '最长', value: data.max ?? '—' },
  ]
})

const commentsLabelOptions = [
  { key: '', label: '全部', icon: '' },
  { key: 'positive', label: '正面', icon: 'emotion-happy-line' },
  { key: 'negative', label: '负面', icon: 'emotion-unhappy-line' },
]

function cellText(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function isLabelColumn(column) {
  return /label|sentiment|polarity|极性|情感/i.test(String(column))
}

function isSentimentValue(value) {
  return ['positive', 'negative', 'neutral'].includes(String(value).toLowerCase())
}

function submitJump() {
  const page = Number(jumpPage.value)
  if (!Number.isFinite(page)) return
  pageTo(page)
  jumpPage.value = ''
}
</script>

<template>
  <section class="artifact">
    <header class="artifact__head">
      <div class="artifact__title-row">
        <h1 class="artifact__title"><Icon name="folder-3-line" :size="19" /> {{ name }}</h1>
        <VideoStatusBadge v-if="project?.status" :status="project.status" />
        <MethodBadge :method="project?.method" size="large" />
      </div>

      <dl class="artifact__meta">
        <div class="artifact__cell">
          <dt>行数</dt>
          <dd class="mono">
            {{ formatNumber(initialRows ?? 0) }} 行
            <Icon class="artifact__arrow" name="arrow-right-s-line" :size="14" />
            {{ formatNumber(cleanRows ?? 0) }} 行
          </dd>
        </div>
        <div class="artifact__cell">
          <dt>项目 ID</dt>
          <dd class="mono">{{ project?.id || '—' }}</dd>
        </div>
        <div class="artifact__cell">
          <dt>上传时间</dt>
          <dd>{{ formatDateTime(project?.created_at) }}</dd>
        </div>
        <div class="artifact__cell artifact__cell--wide">
          <dt>来源文件</dt>
          <dd>
            <template v-if="sources.length">
              <span v-for="source in sources" :key="source.filename" class="artifact__source mono">
                {{ source.filename }}（{{ formatNumber(source.rows) }} 行）
              </span>
            </template>
            <span v-else>—</span>
          </dd>
        </div>
      </dl>

      <p v-if="project?.method === 'rule_placeholder'" class="artifact__placeholder-note">
        当前逐条情感与聚合分析均由<strong>规则/词典占位逻辑</strong>生成，不是训练模型输出；接入真实模型后本标识自动消失。
      </p>
    </header>

    <!-- 处理中 / 失败 -->
    <div v-if="isProcessing" class="artifact__state">
      <h2><Icon name="loader-4-line" :size="18" spin /> {{ project?.message || '正在处理…' }}</h2>
      <div class="artifact__bar" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
        <div class="artifact__bar-fill" :style="{ width: `${progress}%` }"></div>
      </div>
      <p class="hint">处理完成后这里会出现三张表、逐条评论与聚合图表（列表每 3 秒自动刷新）。</p>
    </div>

    <div v-else-if="isFailed" class="artifact__failed">
      <h2><Icon name="error-warning-line" :size="18" /> 处理失败</h2>
      <p class="artifact__failed-reason">{{ project?.error || project?.message || '未知错误' }}</p>
      <button type="button" class="btn btn--primary" @click="emit('reprocess', project?.id)">
        <Icon name="restart-line" :size="14" />
        重新处理
      </button>
    </div>

    <template v-else-if="isCompleted">
      <!-- 1. 三张表 -->
      <div class="chart-card artifact__tables">
        <div class="chart-card__head">
          <h3><Icon name="table-line" :size="16" /> 数据表</h3>
          <span class="card__hint">列名由后端返回动态渲染 · 默认 100 行/页</span>
        </div>

        <div class="tabs" role="tablist">
          <button
            v-for="tab in stageTabs"
            :key="tab.key"
            type="button"
            role="tab"
            class="tabs__item"
            :class="{ 'is-active': stage === tab.key }"
            :aria-selected="stage === tab.key"
            @click="switchStage(tab.key)"
          >
            {{ tab.label }}
            <span v-if="tab.rows !== null" class="tabs__count">{{ formatNumber(tab.rows) }}</span>
          </button>
        </div>

        <div class="pager">
          <span class="pager__info">
            共 <strong class="mono">{{ formatNumber(totalRows) }}</strong> 行 · 第
            <strong class="mono">{{ tablePage }}</strong> / {{ tablePages }} 页
          </span>
          <div class="pager__ctrl">
            <label class="pager__limit">
              每页
              <select :value="tableLimit" @change="setTableLimit($event.target.value)">
                <option :value="50">50</option>
                <option :value="100">100</option>
                <option :value="200">200</option>
                <option :value="500">500</option>
              </select>
            </label>
            <button type="button" class="btn pager__btn" :disabled="tablePage <= 1 || tableLoading" @click="prevPage">
              <Icon name="arrow-left-s-line" :size="13" />
              上一页
            </button>
            <button type="button" class="btn pager__btn" :disabled="tablePage >= tablePages || tableLoading" @click="nextPage">
              下一页
              <Icon name="arrow-right-s-line" :size="13" />
            </button>
            <form class="pager__jump" @submit.prevent="submitJump">
              <input v-model="jumpPage" type="number" min="1" :max="tablePages" placeholder="页码" aria-label="跳转到第几页" />
              <button type="submit" class="btn pager__btn" :disabled="tableLoading">跳转</button>
            </form>
          </div>
        </div>

        <p v-if="tableLoading" class="hint">正在加载「{{ stageLabel }}」…</p>
        <p v-else-if="tableError" class="hint hint--error">
          {{ tableError }}
          <button type="button" class="btn pager__btn" @click="reload">
            <Icon name="refresh-line" :size="13" />
            重试
          </button>
        </p>
        <div v-else class="table-wrap artifact__table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th class="num">#</th>
                <th v-for="column in columns" :key="column">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rowIndex) in rows" :key="rowIndex">
                <td class="num">{{ Number(tableLimit) * (tablePage - 1) + rowIndex + 1 }}</td>
                <td v-for="(cell, cellIndex) in row" :key="cellIndex" :title="cellText(cell)">
                  <SentimentBadge
                    v-if="isLabelColumn(columns[cellIndex]) && isSentimentValue(cell)"
                    :label="String(cell)"
                    compact
                  />
                  <span v-else class="artifact__cell-text">{{ cellText(cell) }}</span>
                </td>
              </tr>
              <tr v-if="!rows.length">
                <td class="empty-cell" :colspan="Math.max(1, columns.length + 1)">该阶段暂无数据</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 2. 逐条评论情感（可展开） -->
      <div class="chart-card artifact__comments">
        <div class="chart-card__head">
          <h3><Icon name="chat-1-line" :size="16" /> 逐条评论情感</h3>
          <span class="card__hint">折叠态只加载第一页 · 展开后可翻页与筛选</span>
        </div>
        <button
          type="button"
          class="btn artifact__expand"
          :aria-expanded="commentsOpen ? 'true' : 'false'"
          @click="toggleComments"
        >
          <Icon class="artifact__expand-caret" :class="{ 'is-open': commentsOpen }" name="arrow-down-s-line" :size="14" />
          {{ commentsOpen ? '收起逐条评论' : '展开逐条评论' }}
          <span class="artifact__expand-count">共 {{ formatNumber(commentsTotal) }} 条</span>
        </button>

        <template v-if="commentsOpen">
          <div class="filters" role="group" aria-label="情感筛选">
            <button
              v-for="option in commentsLabelOptions"
              :key="option.key || 'all'"
              type="button"
              class="filters__item"
              :class="{ 'is-active': commentsLabel === option.key }"
              @click="setCommentsLabel(option.key)"
            >
              <Icon v-if="option.icon" :name="option.icon" :size="13" />
              {{ option.label }}
            </button>
          </div>

          <p v-if="commentsLoading" class="hint">正在加载逐条评论…</p>
          <p v-else-if="commentsError" class="hint hint--error">{{ commentsError }}</p>
          <ul v-else class="comment-rows">
            <li v-for="item in comments" :key="`${item.index}-${item.text}`" class="comment-row">
              <div class="comment-row__head">
                <span class="comment-row__index mono">#{{ item.index }}</span>
                <SentimentBadge :label="item.label" />
                <span class="comment-row__conf mono">置信度 {{ formatPercent(item.confidence) }}</span>
              </div>
              <p class="comment-row__text">{{ item.text }}</p>
              <ProbBars :probs="item.probs" compact />
            </li>
          </ul>

          <div class="pager">
            <span class="pager__info">
              共 <strong class="mono">{{ formatNumber(commentsTotal) }}</strong> 条 · 第
              <strong class="mono">{{ commentsPage }}</strong> / {{ commentsPages }} 页
            </span>
            <div class="pager__ctrl">
              <button type="button" class="btn pager__btn" :disabled="commentsPage <= 1 || commentsLoading" @click="commentsPrev">
                <Icon name="arrow-left-s-line" :size="13" />
                上一页
              </button>
              <button type="button" class="btn pager__btn" :disabled="commentsPage >= commentsPages || commentsLoading" @click="commentsNext">
                下一页
                <Icon name="arrow-right-s-line" :size="13" />
              </button>
            </div>
          </div>
        </template>
      </div>

      <!-- 3. 聚合情感图表 -->
      <p v-if="analysisLoading" class="hint">正在加载聚合分析…</p>
      <p v-else-if="analysisError" class="hint hint--error">
        {{ analysisError }}
        <button type="button" class="btn pager__btn" @click="reload">
          <Icon name="refresh-line" :size="13" />
          重试
        </button>
      </p>
      <template v-else>
        <div class="artifact__grid">
          <SentimentPie :sentiment="sentimentForChart" />
          <div class="chart-card">
            <div class="chart-card__head">
              <h3><Icon name="bar-chart-2-line" :size="16" /> 长度分布</h3>
              <span class="card__hint">清洗后评论字数</span>
            </div>
            <EChart v-if="analysis?.length" :option="lengthOption" height="260px" aria-label="文本长度分布柱状图" />
            <p v-else class="chart-card__empty">暂无长度数据</p>
            <dl class="kv artifact__length-kv">
              <template v-for="row in lengthRows" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd class="mono">{{ row.value }}</dd>
              </template>
            </dl>
          </div>
        </div>

        <div class="artifact__grid">
          <WordCloud :words="analysis?.keywords?.positive || []" tone="positive" />
          <WordCloud :words="analysis?.keywords?.negative || []" tone="negative" />
        </div>
      </template>
    </template>

    <!-- 4. 操作按钮：统一放页面最底部 -->
    <footer class="artifact__actions">
      <div class="artifact__actions-left">
        <button type="button" class="btn btn--primary" :disabled="opening" @click="openCsv()">
          <Icon name="file-excel-2-line" :size="15" />
          {{ opening ? '正在打开…' : '打开 CSV' }}
        </button>
        <button
          type="button"
          class="btn"
          :disabled="copying"
          title="通过只读接口 GET /api/artifacts/{id}/path 取路径并复制，不会打开文件"
          @click="copyFileAddress()"
        >
          <Icon name="file-copy-2-line" :size="15" />
          {{ copying ? '正在复制…' : '复制文件地址' }}
        </button>
        <button type="button" class="btn" @click="emit('reprocess', project?.id)">
          <Icon name="restart-line" :size="15" />
          重新处理
        </button>
        <span class="artifact__actions-hint">
          「打开 CSV」在服务端本机用系统默认程序打开当前阶段（{{ stageLabel }}）；
          「复制文件地址」只读取路径，不会打开文件
        </span>
      </div>
      <p v-if="actionMessage" class="hint hint--ok artifact__action-msg">{{ actionMessage }}</p>
      <p v-if="actionError" class="hint hint--error artifact__action-msg">{{ actionError }}</p>
    </footer>
  </section>
</template>

<style scoped>
.artifact {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.artifact__head {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 16px 20px;
}
.artifact__title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.artifact__title {
  font-size: 18px;
  flex: 1 1 auto;
  word-break: break-word;
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.artifact__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.artifact__cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.artifact__cell--wide {
  flex: 1 1 260px;
}
.artifact__cell dt {
  color: var(--text-muted);
  font-size: 12.5px;
}
.artifact__cell dd {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}
.artifact__arrow {
  color: var(--text-muted);
  margin: 0 4px;
}
/* 展开/收起逐条评论的箭头：展开时翻转 180° */
.artifact__expand-caret {
  transition: transform 0.15s ease;
}
.artifact__expand-caret.is-open {
  transform: rotate(180deg);
}
.artifact__source {
  display: inline-block;
  margin-right: 10px;
  font-size: 12.5px;
  font-weight: 400;
}
.artifact__placeholder-note {
  margin: 12px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  border: 1px solid var(--warn-border);
  color: var(--warn-text);
  font-size: 12.5px;
}
.artifact__state,
.artifact__failed {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid #ea580c;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px 22px;
}
.artifact__failed {
  border-left-color: var(--negative);
}
.artifact__state h2,
.artifact__failed h2 {
  font-size: 17px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 7px;
}
.artifact__failed h2 {
  color: var(--negative);
}
.artifact__failed-reason {
  font-size: 14px;
}
.artifact__bar {
  height: 10px;
  border-radius: 999px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  overflow: hidden;
  margin: 10px 0 14px;
}
.artifact__bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #fb923c, #ea580c);
  transition: width 0.4s ease;
}
.artifact__table-wrap {
  max-height: 460px;
  overflow: auto;
}
.artifact__cell-text {
  display: inline-block;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.tabs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.tabs__item {
  appearance: none;
  font: inherit;
  font-size: 13.5px;
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tabs__item:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.tabs__item.is-active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  font-weight: 600;
}
.tabs__count {
  font-family: var(--mono);
  font-size: 11.5px;
  opacity: 0.85;
}
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--text-muted);
}
.pager__ctrl {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.pager__btn {
  padding: 3px 10px;
  font-size: 12.5px;
}
.pager__limit {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
}
.pager__limit select {
  width: auto;
  padding: 3px 6px;
}
.pager__jump {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.pager__jump input {
  width: 72px;
  padding: 3px 6px;
}
.filters {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}
.filters__item {
  appearance: none;
  font: inherit;
  font-size: 12.5px;
  padding: 3px 12px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.filters__item.is-active {
  background: var(--primary-soft);
  border-color: var(--primary);
  color: var(--primary-dark);
  font-weight: 600;
}
.artifact__expand {
  margin-bottom: 10px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.artifact__expand-count {
  color: var(--text-muted);
  font-size: 12.5px;
}
.comment-rows {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
}
.comment-row {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-alt);
  padding: 8px 10px;
}
.comment-row__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-muted);
}
.comment-row__conf {
  font-size: 11.5px;
}
.comment-row__text {
  margin: 6px 0;
  font-size: 13.5px;
  line-height: 1.5;
  word-break: break-word;
}
.artifact__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
  align-items: start;
}
.artifact__length-kv {
  margin-top: 10px;
  grid-template-columns: 88px 1fr;
  font-size: 13px;
}
.chart-card__empty {
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  padding: 40px 0;
}
.artifact__actions {
  margin-top: 6px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.artifact__actions-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.artifact__actions-hint {
  font-size: 12.5px;
  color: var(--text-muted);
}
.artifact__action-msg {
  margin: 0;
}
</style>
