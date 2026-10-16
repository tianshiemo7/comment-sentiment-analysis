<script setup>
/**
 * components/BvInput.vue — 顶部 BV 号输入小框
 * 即时校验 ^BV[0-9A-Za-z]{10}$，非法给中文提示且不提交（契约：BV 号校验规则前后端一致）。
 */
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { validateBvid } from '../utils/videoFormat.js'

const props = defineProps({
  submitting: { type: Boolean, default: false },
  /** 服务端错误（如 409 已在队列中 / 404），由父组件传入 */
  serverError: { type: String, default: '' },
})

const emit = defineEmits(['submit', 'input'])

const value = ref('')
const touched = ref(false)

const localError = computed(() => {
  const raw = value.value.trim()
  if (!raw) return touched.value ? '请输入 BV 号' : ''
  return validateBvid(raw)
})

const errorText = computed(() => localError.value || props.serverError)

function onSubmit() {
  touched.value = true
  const raw = value.value.trim()
  if (validateBvid(raw)) return // 不合法不提交
  emit('submit', raw)
}

function reset() {
  value.value = ''
  touched.value = false
}

// 用户重新输入时清掉「未填写」提示，重新做即时校验，并通知父组件清除服务端错误
watch(value, () => {
  touched.value = false
  emit('input')
})

defineExpose({ reset })
</script>

<template>
  <form class="bv-input" @submit.prevent="onSubmit">
    <label class="bv-input__label" for="bvid-input">BV 号</label>
    <div class="bv-input__row">
      <input
        id="bvid-input"
        v-model="value"
        class="bv-input__field mono"
        type="text"
        name="bvid"
        autocomplete="off"
        spellcheck="false"
        maxlength="12"
        placeholder="BV1xx411c7mD"
        :aria-invalid="errorText ? 'true' : 'false'"
        aria-describedby="bvid-input-error"
      />
      <button class="btn btn--primary" type="submit" :disabled="submitting">
        <Icon name="play-line" :size="14" />
        {{ submitting ? '提交中…' : '开始分析' }}
      </button>
    </div>
    <p id="bvid-input-error" class="bv-input__msg" :class="errorText ? 'is-error' : 'is-hint'">
      <Icon v-if="errorText" name="alert-line" :size="13" />
      {{ errorText || '输入视频 BV 号，格式形如 BV1xx411c7mD（共 11 位）' }}
    </p>
  </form>
</template>

<style scoped>
.bv-input {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.bv-input__label {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}
.bv-input__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 320px;
  min-width: 260px;
}
.bv-input__row .btn {
  flex: 0 0 auto;
  white-space: nowrap;
}
.bv-input__field {
  flex: 1 1 auto;
  min-width: 0;
  letter-spacing: 0.4px;
  padding: 8px 11px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  font: inherit;
  background: var(--surface);
  color: inherit;
}
.bv-input__field[aria-invalid='true'] {
  border-color: #ef4444;
  background: var(--negative-soft);
}
.bv-input__msg {
  flex: 1 1 100%;
  margin: 0;
  font-size: 12.5px;
  /* 出错时在文案前放一个 alert 图标 */
  display: flex;
  align-items: center;
  gap: 5px;
}
.bv-input__msg.is-hint {
  color: var(--text-muted);
}
.bv-input__msg.is-error {
  color: var(--negative);
  font-weight: 600;
}
</style>
