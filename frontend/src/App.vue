<script setup>
import { onMounted, ref } from 'vue'
import BilibiliView from './views/BilibiliView.vue'
import SinglePredictView from './views/SinglePredictView.vue'
import BatchPredictView from './views/BatchPredictView.vue'
import VisualizationView from './views/VisualizationView.vue'
import ModelCompareView from './views/ModelCompareView.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import Icon from './components/Icon.vue'
import { useSettings } from './composables/useSettings.js'

/**
 * 视图切换：不引入 vue-router（依赖白名单仅 vue / vite / @vitejs/plugin-vue / echarts），
 * 使用顶部导航 + 动态组件实现等价的多页面切换。
 * 首页已升级为 B 站视频评论分析主界面（BilibiliView）；其余四个既有页面保持不变。
 *
 * 左上角品牌位（原「情」方块）在 2026-10-17 改为齿轮设置按钮：
 *   - 点击打开「系统设置」弹窗（账号 / 采集参数 / 自动流程）；
 *   - 旁边显示当前账号模式徽标（匿名 / 已登录用户名），点击同样打开设置。
 * 账号模式来自共享的 useSettings（GET /api/settings），保存后徽标即时更新。
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

/** 共享设置：只取账号模式相关字段用于顶栏徽标（失败自动回退默认值 = 匿名） */
const { accountLabel, isAccountMode, load: loadSettings } = useSettings()
const settingsOpen = ref(false)

onMounted(() => {
  loadSettings() // 顶栏徽标需要账号模式；失败时 useSettings 内部已回退默认值
})

function navigate(key) {
  if (views.some((v) => v.key === key)) {
    activeKey.value = key
  }
}

function openSettings() {
  settingsOpen.value = true
}

function closeSettings() {
  settingsOpen.value = false
}
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <div class="brand">
        <button
          type="button"
          class="brand__mark"
          aria-label="设置"
          title="系统设置（账号 / 采集参数 / 自动流程）"
          aria-haspopup="dialog"
          :aria-expanded="settingsOpen ? 'true' : 'false'"
          @click="openSettings"
        >
          <Icon name="settings-3-line" :size="16" />
        </button>
        <span>
          B 站视频评论情感分析系统
          <span class="brand__sub">Vue 3 + Vite + ECharts</span>
        </span>
        <button
          type="button"
          class="account-chip"
          :class="{ 'account-chip--account': isAccountMode }"
          :title="`当前账号：${accountLabel}（点击打开系统设置）`"
          :aria-label="`账号：${accountLabel}，打开系统设置`"
          @click="openSettings"
        >
          <Icon :name="isAccountMode ? 'user-line' : 'shield-user-line'" :size="12" />
          {{ accountLabel }}
        </button>
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
    默认经 Vite 代理连真实后端；接口契约见 docs/API契约.md（无后端演示用 VITE_USE_MOCK=1）
  </footer>

  <SettingsDialog v-if="settingsOpen" @close="closeSettings" />
</template>
