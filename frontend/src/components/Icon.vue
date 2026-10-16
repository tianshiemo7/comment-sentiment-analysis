<script setup>
/**
 * components/Icon.vue — 全站统一图标组件（内联 SVG，零依赖）
 *
 * 用法：
 *   <Icon name="refresh-line" />                        装饰性图标，跟随文字颜色
 *   <Icon name="loader-4-line" :size="15" spin />       旋转（处理中）
 *   <Icon name="delete-bin-6-line" :size="14" label="删除" />   语义图标，带无障碍名称
 *
 * 约定：
 *   - 图标一律 fill="currentColor"，颜色由所在容器的文字色决定，组件内不写死任何颜色；
 *   - 装饰性图标输出 aria-hidden="true" focusable="false"，不干扰读屏；
 *     传入 label 时改为 role="img" + <title>，此时调用方不必再给 aria-label；
 *   - 纯图标按钮请在外层 <button> 上补 title 与 aria-label（见 main.css 的 .icon-btn）；
 *   - name 未命中时渲染中性圆环占位并在 DEV 下告警，避免拼错名字后静默渲染空白。
 *
 * 图形来源与许可见 ../assets/icons/NOTICE.txt（Remix Icon License v1.0）。
 * 该许可第 3.3 条禁止将图标用作品牌标识，故本组件的用途仅限界面控件与状态提示。
 */
import { computed } from 'vue'
import { ICON_PATHS } from '../assets/icons/icons.js'

/** 未知图标名的中性占位：一个空心圆环，保证布局不塌陷 */
const FALLBACK_PATH =
  'M12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C17.5228 2 22 6.47715 22 12C22 17.5228 17.5228 22 12 22ZM12 20C16.4183 20 20 16.4183 20 12C20 7.58172 16.4183 4 12 4C7.58172 4 4 7.58172 4 12C4 16.4183 7.58172 20 12 20Z'

const props = defineProps({
  /** icons.js 中的图标名，如 'play-circle-line' */
  name: { type: String, required: true },
  /** 边长：数字按 px，也可传 '1em' 之类跟随字号 */
  size: { type: [Number, String], default: 16 },
  /** 无障碍名称；传了才有语义，不传即视为装饰性图标 */
  label: { type: String, default: '' },
  /** 持续旋转（仅用于「处理中」这类进行中状态） */
  spin: { type: Boolean, default: false },
})

const d = computed(() => {
  const path = ICON_PATHS[props.name]
  if (path) return path
  if (import.meta.env.DEV) {
    console.warn(`[Icon] 未知图标名「${props.name}」，已回退为占位图形。可用图标见 assets/icons/icons.js`)
  }
  return FALLBACK_PATH
})

const side = computed(() => (typeof props.size === 'number' ? `${props.size}px` : props.size))
</script>

<template>
  <svg
    class="icon"
    :class="{ 'icon--spin': spin }"
    viewBox="0 0 24 24"
    fill="currentColor"
    :width="side"
    :height="side"
    :role="label ? 'img' : undefined"
    :aria-hidden="label ? undefined : 'true'"
    focusable="false"
  >
    <title v-if="label">{{ label }}</title>
    <path :d="d" />
  </svg>
</template>
