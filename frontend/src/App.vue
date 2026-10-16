<script setup>
import { ref } from 'vue'
import BilibiliView from './views/BilibiliView.vue'
import SinglePredictView from './views/SinglePredictView.vue'
import BatchPredictView from './views/BatchPredictView.vue'
import VisualizationView from './views/VisualizationView.vue'
import ModelCompareView from './views/ModelCompareView.vue'

/**
 * 视图切换：不引入 vue-router（依赖白名单仅 vue / vite / @vitejs/plugin-vue / echarts），
 * 使用顶部导航 + 动态组件实现等价的多页面切换。
 * 首页已升级为 B 站视频评论分析主界面（BilibiliView）；其余四个既有页面保持不变。
 */
const views = [
  { key: 'bilibili', label: 'B站分析', component: BilibiliView },
  { key: 'single', label: '单条预测', component: SinglePredictView },
  { key: 'batch', label: '批量预测', component: BatchPredictView },
  { key: 'visualization', label: '实验可视化', component: VisualizationView },
  { key: 'models', label: '模型对比', component: ModelCompareView },
]

const activeKey = ref('bilibili')

function navigate(key) {
  if (views.some((v) => v.key === key)) {
    activeKey.value = key
  }
}
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <div class="brand">
        <span class="brand__mark">情感</span>
        <span>
          B 站视频评论情感分析系统
          <span class="brand__sub">Vue 3 + Vite + ECharts</span>
        </span>
      </div>
      <nav class="nav" aria-label="主导航">
        <button
          v-for="view in views"
          :key="view.key"
          type="button"
          class="nav__item"
          :class="{ 'is-active': view.key === activeKey }"
          :aria-current="view.key === activeKey ? 'page' : undefined"
          @click="navigate(view.key)"
        >
          {{ view.label }}
        </button>
      </nav>
    </div>
  </header>

  <main class="container">
    <component :is="views.find((v) => v.key === activeKey).component" @navigate="navigate" />
  </main>

  <footer class="app-footer">
    课程设计项目 · 后端推理服务 FastAPI (:8000) · 前端 Vite (:3000) ·
    B 站分析接口契约见 docs/API契约.md（mock 层位于 frontend/mock/，后端就绪后删除 vite.config.js 中一行即可）
  </footer>
</template>
