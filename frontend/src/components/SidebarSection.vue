<script setup>
/**
 * components/SidebarSection.vue — 左栏可收起的一段（数据项目 / 单独评论 / 视频任务）
 * 收起状态由父组件持有并写入 localStorage；本组件只负责渲染与派发事件。
 */
defineProps({
  title: { type: String, required: true },
  count: { type: Number, default: 0 },
  collapsed: { type: Boolean, default: false },
  /** 进行中数量，用于在标题右侧提示 */
  activeCount: { type: Number, default: 0 },
  empty: { type: String, default: '暂无数据' },
  isEmpty: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])
</script>

<template>
  <section class="sec" :class="{ 'sec--collapsed': collapsed }">
    <header class="sec__head">
      <button
        type="button"
        class="sec__toggle"
        :aria-expanded="collapsed ? 'false' : 'true'"
        :title="collapsed ? `展开${title}` : `收起${title}`"
        @click="emit('toggle')"
      >
        <span class="sec__caret" :class="{ 'is-collapsed': collapsed }" aria-hidden="true">▾</span>
        <span class="sec__title">{{ title }}</span>
        <span class="sec__count">{{ count }}</span>
        <span v-if="activeCount" class="sec__active">{{ activeCount }} 进行中</span>
      </button>
      <div class="sec__actions"><slot name="actions" /></div>
    </header>

    <div v-if="!collapsed" class="sec__body">
      <p v-if="isEmpty" class="sec__empty">{{ empty }}</p>
      <slot v-else />
    </div>
  </section>
</template>

<style scoped>
.sec {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  overflow: hidden;
}
.sec + .sec {
  margin-top: 8px;
}
.sec__head {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--surface-alt);
  border-bottom: 1px solid var(--border);
  padding: 4px 6px 4px 4px;
}
.sec--collapsed .sec__head {
  border-bottom: 0;
}
.sec__toggle {
  appearance: none;
  border: 0;
  background: transparent;
  font: inherit;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  padding: 4px 4px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  text-align: left;
}
.sec__toggle:hover {
  background: #eef2f7;
}
.sec__caret {
  color: var(--text-muted);
  font-size: 11px;
  transition: transform 0.15s ease;
}
.sec__caret.is-collapsed {
  transform: rotate(-90deg);
}
.sec__title {
  font-size: 13.5px;
  font-weight: 600;
  white-space: nowrap;
}
.sec__count {
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--text-muted);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 7px;
}
.sec__active {
  font-size: 11px;
  color: #1d4ed8;
  white-space: nowrap;
}
.sec__actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
.sec__body {
  padding: 8px;
}
.sec__empty {
  margin: 0;
  padding: 10px 4px;
  font-size: 12.5px;
  color: var(--text-muted);
}
</style>
