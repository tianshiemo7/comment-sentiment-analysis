<script setup>
/**
 * components/CommentList.vue — 代表性评论列表（正面/负面各一组）
 * 字段来自契约 top_comments.positive / .negative：[{ text, like, confidence, label }]
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { formatMetric, formatNumber } from '../utils/videoFormat.js'

const props = defineProps({
  title: { type: String, required: true },
  /** positive | negative */
  tone: { type: String, default: 'positive' },
  comments: { type: Array, default: () => [] },
})

const hasData = computed(() => props.comments.length > 0)
</script>

<template>
  <div class="comment-card">
    <div class="chart-card__head">
      <h3><Icon name="chat-quote-line" :size="16" /> {{ title }}</h3>
      <span class="card__hint">{{ comments.length }} 条</span>
    </div>
    <ul v-if="hasData" class="comment-list">
      <li v-for="(item, index) in comments" :key="index" class="comment" :class="`comment--${tone}`">
        <p class="comment__text">{{ item.text }}</p>
        <div class="comment__meta">
          <span class="comment__likes"><Icon name="thumb-up-line" :size="13" />{{ formatNumber(item.like) }}</span>
          <span>置信度 {{ formatMetric(item.confidence, 3) }}</span>
          <span class="comment__label">{{ tone === 'negative' ? '负面' : '正面' }}</span>
        </div>
      </li>
    </ul>
    <p v-else class="comment-empty">暂无代表性评论</p>
  </div>
</template>

<style scoped>
.comment-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow-y: auto;
}
.comment {
  border: 1px solid var(--border);
  border-left: 3px solid var(--positive);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  background: var(--surface-alt);
}
.comment--negative {
  border-left-color: var(--negative);
}
.comment__text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.5;
  word-break: break-word;
}
.comment__meta {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-muted);
}
/* 点赞数前的拇指图标替代原 👍 emoji（emoji 字形随平台/系统字体变化） */
.comment__likes {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.comment__label {
  margin-left: auto;
  font-weight: 600;
}
.comment--positive .comment__label {
  color: var(--positive);
}
.comment--negative .comment__label {
  color: var(--negative);
}
.comment-empty {
  margin: 0;
  padding: 26px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
