<script setup>
/**
 * components/MethodBadge.vue — 情感结果来源标识
 *
 * 契约（docs/API契约.md 4.5.1 / 4.5.2）：method === 'rule_placeholder' 表示结果由
 * **规则占位**逻辑生成（真实模型不可用时的降级），前端必须给出醒目标识，避免误认为模型结果。
 * 其它取值（如 bilstm_attention / textcnn）按真实模型名展示。
 */
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  method: { type: String, default: '' },
  size: { type: String, default: 'normal' }, // normal | large
})

const isPlaceholder = computed(() => !props.method || props.method === 'rule_placeholder')
const text = computed(() => (isPlaceholder.value ? '规则占位' : props.method))
/** 规则占位用锥形瓶（实验性/非模型），真实模型用机器人 */
const icon = computed(() => (isPlaceholder.value ? 'flask-line' : 'robot-2-line'))
</script>

<template>
  <span
    class="method-badge"
    :class="[isPlaceholder ? 'method-badge--placeholder' : 'method-badge--model', `method-badge--${size}`]"
    :title="isPlaceholder ? '当前结果由规则/词典占位逻辑生成，不是训练模型输出' : `由模型 ${method} 输出`"
  >
    <Icon :name="icon" :size="13" />
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
</style>
