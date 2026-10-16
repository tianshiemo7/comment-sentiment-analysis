import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { mockApiPlugin } from './mock/index.js'

/**
 * 后端 FastAPI 推理服务地址。
 * 对应 src/inference/app.py，由 `uvicorn src.inference.app:app --port 8000` 启动。
 */
const BACKEND_TARGET = 'http://localhost:8000'

/** /api 与 /health 由 Vite 代理转发到后端，前端代码里一律使用同源相对路径。 */
const proxy = {
  '/api': { target: BACKEND_TARGET, changeOrigin: true },
  '/health': { target: BACKEND_TARGET, changeOrigin: true },
}

/**
 * 是否启用 mock 接口层。
 *
 * - 默认启用：后端未启动时也能完整演示界面（数据来源 frontend/mock/）。
 * - 后端就绪后关闭：`VITE_USE_MOCK=0 npm run dev`（或直接删掉下面那行 mockApiPlugin()）。
 */
const useMock = process.env.VITE_USE_MOCK !== '0'

export default defineConfig({
  plugins: [
    vue(),
    /**
     * ------------------------------------------------------------------
     * mock 插件：无后端时按 docs/API契约.md 返回 /api/videos* 与 /health。
     * 真实后端就绪后 —— 【设 VITE_USE_MOCK=0，或删除下面这一行 mockApiPlugin(), 即可】，
     * 无需改动任何业务代码。mock 代码全部位于 frontend/mock/，删除该目录同样安全。
     * 插件内的中间件注册早于 Vite proxy，所以 mock 存在时不会被转发到 :8000；
     * 关闭后 /api/videos* 会自动走下面的 proxy 到 FastAPI。
     * ------------------------------------------------------------------
     */
    ...(useMock ? [mockApiPlugin()] : []),
  ],
  server: {
    port: 3000,
    strictPort: true, // 端口固定 3000：被占用时直接报错，不静默顺延
    proxy,
  },
  // 生产构建预览时同样需要代理，方便本地验证 dist/ 产物
  preview: {
    port: 3000,
    strictPort: true,
    proxy,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // echarts 单独成一个 vendor chunk（约 545 kB / gzip 184 kB），属预期体积
    chunkSizeWarningLimit: 700,
    // 把体积较大的 echarts 单独拆包，首页业务代码更新时不必重新下载图表库
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers', 'echarts-wordcloud'],
          vue: ['vue'],
        },
      },
    },
  },
})
