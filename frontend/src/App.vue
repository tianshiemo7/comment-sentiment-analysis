<script setup>
import { ref } from 'vue'
import HomeView from './views/HomeView.vue'
import SinglePredictView from './views/SinglePredictView.vue'
import BatchPredictView from './views/BatchPredictView.vue'
import VisualizationView from './views/VisualizationView.vue'
import ModelCompareView from './views/ModelCompareView.vue'

/**
 * 视图切换：骨架阶段不引入 vue-router（依赖白名单仅 vue / vite / @vitejs/plugin-vue），
 * 使用顶部导航 + 动态组件实现等价的多页面切换。
 */
const views = [
  { key: 'home', label: '首页', component: HomeView },
  { key: 'single', label: '单条预测', component: SinglePredictView },
  { key: 'batch', label: '批量预测', component: BatchPredictView },
  { key: 'visualization', label: '可视化', component: VisualizationView },
  { key: 'models', label: '模型对比', component: ModelCompareView },
]

const activeKey = ref('home')

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
          网站用户评论情感分析系统
          <span class="brand__sub">Vue 3 + Vite 前端骨架</span>
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
    课程设计项目骨架 · 后端推理服务 FastAPI (:8000) · 前端 Vite (:3000) ·
    接口契约见 README「前后端接口契约」一节
  </footer>
</template>
