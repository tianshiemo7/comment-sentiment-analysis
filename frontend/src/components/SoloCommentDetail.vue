<script setup>
/**
 * components/SoloCommentDetail.vue — 「单独评论」详情视图
 *
 * 契约 4.5.2 明确要求：**只渲染**原文 + 极性徽章 + 置信度 + 概率条 + 时间 + method 标识，
 * 不渲染任何图表（本组件不引入 ECharts / canvas / svg 图表，避免误加图表）。
 */
import { computed } from 'vue'
import MethodBadge from './MethodBadge.vue'
import SentimentBadge from './SentimentBadge.vue'
import ProbBars from './ProbBars.vue'
import { formatPercent } from '../utils/format.js'
import { formatDateTime } from '../utils/videoFormat.js'

const props = defineProps({
  /** 单条评论对象（契约 4.5.2） */
  item: { type: Object, default: null },
})

const confidence = computed(() => formatPercent(props.item?.confidence))
const createdAt = computed(() => formatDateTime(props.item?.created_at))
</script>

<template>
  <section class="solo">
    <header class="solo__head">
      <div class="solo__headline">
        <h1>单独评论</h1>
        <MethodBadge :method="item?.method" size="large" />
      </div>
      <p class="solo__sub">单条评论只展示预测结果本身，按契约不渲染任何图表。</p>
    </header>

    <article class="card solo__card">
      <div class="card__head">
        <h2>原文</h2>
        <span class="card__hint">#{{ item?.id || '—' }}</span>
      </div>
      <p class="solo__text">{{ item?.text || '—' }}</p>

      <dl class="solo__meta">
        <div class="solo__meta-cell">
          <dt>情感极性</dt>
          <dd><SentimentBadge :label="item?.label" /></dd>
        </div>
        <div class="solo__meta-cell">
          <dt>置信度</dt>
          <dd class="mono">{{ confidence }}</dd>
        </div>
        <div class="solo__meta-cell">
          <dt>保存时间</dt>
          <dd>{{ createdAt }}</dd>
        </div>
        <div class="solo__meta-cell">
          <dt>结果来源</dt>
          <dd><MethodBadge :method="item?.method" /></dd>
        </div>
      </dl>
    </article>

    <article class="card solo__card">
      <div class="card__head">
        <h2>概率分布</h2>
        <span class="card__hint">probs（由 POST /api/predict 结果原样保存）</span>
      </div>
      <ProbBars :probs="item?.probs" />
    </article>
  </section>
</template>

<style scoped>
.solo {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.solo__head {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 16px 20px;
}
.solo__headline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.solo__headline h1 {
  font-size: 18px;
}
.solo__sub {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}
.solo__text {
  margin: 0 0 4px;
  font-size: 19px;
  line-height: 1.7;
  font-weight: 500;
  word-break: break-word;
  white-space: pre-wrap;
  padding: 14px 16px;
  background: var(--surface-alt);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.solo__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 32px;
  margin: 14px 0 0;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.solo__meta-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.solo__meta-cell dt {
  color: var(--text-muted);
  font-size: 12.5px;
}
.solo__meta-cell dd {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}
</style>
