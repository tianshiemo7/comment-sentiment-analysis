<script setup>
import { ref } from 'vue'
import BilibiliView from './views/BilibiliView.vue'
import SinglePredictView from './views/SinglePredictView.vue'
import BatchPredictView from './views/BatchPredictView.vue'
import VisualizationView from './views/VisualizationView.vue'
import ModelCompareView from './views/ModelCompareView.vue'
import Icon from './components/Icon.vue'

/**
 * 视图切换：不引入 vue-router（依赖白名单仅 vue / vite / @vitejs/plugin-vue / echarts），
 * 使用顶部导航 + 动态组件实现等价的多页面切换。
 * 首页已升级为 B 站视频评论分析主界面（BilibiliView）；其余四个既有页面保持不变。
 *
 * 图标：每项左侧一个 15px 线稿图标（图形来源与许可见 components/Icon.vue 顶部注释）。
 * 其中 'bilibili-line' 属 Remix Icon 的 Logos 类品牌图标，按许可第 4.1 条仅用于
 * 「指代 B 站这一品牌本身」，不作本项目标识 —— 品牌标记与 favicon 均为原创。
 */
const views = [
  { key: 'bilibili', label: 'B站分析', icon: 'bilibili-line', component: BilibiliView },
  { key: 'single', label: '单条预测', icon: 'chat-1-line', component: SinglePredictView },
  { key: 'batch', label: '批量预测', icon: 'file-list-3-line', component: BatchPredictView },
  { key: 'visualization', label: '实验可视化', icon: 'bar-chart-2-line', component: VisualizationView },
  { key: 'models', label: '模型对比', icon: 'stack-line', component: ModelCompareView },
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
        <span class="brand__mark" aria-hidden="true">情</span>
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
          <Icon :name="view.icon" :size="15" />
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
