<script setup>
/**
 * components/MethodBadge.vue — 情感结果来源标识
 *
 * 契约（docs/API契约.md 4.5.1 / 4.5.2）：method === 'rule_placeholder' 表示结果由
 * **规则占位**逻辑生成（真实模型尚未接入），前端必须给出醒目标识，避免误认为模型结果。
 * 其它取值（如 bilstm_attention）按模型名展示。
 */
import { computed } from 'vue'

const props = defineProps({
  method: { type: String, default: '' },
  size: { type: String, default: 'normal' }, // normal | large
})

const isPlaceholder = computed(() => !props.method || props.method === 'rule_placeholder')
const text = computed(() => (isPlaceholder.value ? '规则占位' : props.method))
</script>

<template>
  <span
    class="method-badge"
    :class="[isPlaceholder ? 'method-badge--placeholder' : 'method-badge--model', `method-badge--${size}`]"
    :title="isPlaceholder ? '当前结果由规则/词典占位逻辑生成，不是训练模型输出' : `由模型 ${method} 输出`"
  >
    <span v-if="isPlaceholder" class="method-badge__mark" aria-hidden="true">!</span>
    <span v-else class="method-badge__mark method-badge__mark--ok" aria-hidden="true">✓</span>
    {{ text }}
  </span>
</template>

<style scoped>
.method-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 600;
  padding: 1px 10px;
  border: 1px solid var(--border);
  white-space: nowrap;
}
.method-badge--large {
  font-size: 13.5px;
  padding: 3px 12px;
}
.method-badge--placeholder {
  background: var(--warn-bg);
  border-color: var(--warn-border);
  color: var(--warn-text);
}
.method-badge--model {
  background: var(--primary-soft);
  border-color: #bfdbfe;
  color: var(--primary-dark);
}
.method-badge__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--warn-border);
  color: #fff;
  font-size: 10px;
  line-height: 1;
}
.method-badge__mark--ok {
  background: #16a34a;
}
</style>
