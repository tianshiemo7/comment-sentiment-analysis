<script setup>
/**
 * components/charts/EChart.vue — ECharts 基础封装
 *
 * 职责只有三件事：初始化、按 option 更新、卸载时 dispose。
 *  - 窗口 resize + 容器尺寸变化（ResizeObserver）都自适应，避免图表变形；
 *  - onBeforeUnmount 一定 dispose，避免切换详情后残留实例造成内存泄漏；
 *  - 按需注册图表/组件（含 echarts-wordcloud），控制打包体积。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
// 侧效应导入：向同一个 echarts 单例注册 wordCloud 系列（series.type === 'wordCloud'）
import 'echarts-wordcloud'

echarts.use([PieChart, LineChart, BarChart, TitleComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({
  /** ECharts option（响应式，变化时整体替换） */
  option: { type: Object, required: true },
  /** 容器高度，必须显式给定，否则 canvas 高度为 0 */
  height: { type: String, default: '300px' },
  /** 无障碍描述 */
  ariaLabel: { type: String, default: '图表' },
})

const el = ref(null)
const chart = shallowRef(null)
let observer = null

/** 开发期存活实例登记表：便于验证「切换/卸载后确实 dispose」，生产构建不包含。 */
function aliveSet() {
  if (!import.meta.env.DEV) return null
  if (!window.__CSA_ECHARTS_ALIVE__) window.__CSA_ECHARTS_ALIVE__ = new Set()
  return window.__CSA_ECHARTS_ALIVE__
}

function render() {
  if (!chart.value || chart.value.isDisposed()) return
  chart.value.setOption(props.option, true)
}

function resize() {
  if (!chart.value || chart.value.isDisposed()) return
  chart.value.resize()
}

onMounted(() => {
  chart.value = echarts.init(el.value, null, { renderer: 'canvas' })
  const registry = aliveSet()
  if (registry) registry.add(chart.value.id)
  render()
  window.addEventListener('resize', resize)
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(resize)
    observer.observe(el.value)
  }
})

watch(() => props.option, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  if (observer) {
    observer.disconnect()
    observer = null
  }
  if (chart.value && !chart.value.isDisposed()) {
    const registry = aliveSet()
    if (registry) registry.delete(chart.value.id)
    chart.value.dispose()
  }
  chart.value = null
})
</script>

<template>
  <div ref="el" class="echart" :style="{ height }" role="img" :aria-label="ariaLabel"></div>
</template>

<style scoped>
.echart {
  width: 100%;
}
</style>
